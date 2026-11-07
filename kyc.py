"""Smart KYC: OCR, registry checks, cross-document checks and fix-it verdicts.

The pipeline is deliberately deterministic.  It supports two lanes:
  * standard: GST certificate + Udyam + signatory ID + address proof
  * gst_free: Udyam + signatory ID + address proof (no GST certificate)

OCR backends:
  * Tesseract, if the binary is installed
  * RapidOCR (ONNX), which ships as a Python dependency and needs no system
    installation.  This is what makes the demo run out of the box.
"""
from __future__ import annotations

import datetime as dt
import glob
import itertools
import json
import os
import re
import sys
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Any

import numpy as np
import pandas as pd
from PIL import Image, ImageFilter

try:  # optional system binary / python wrapper
    import pytesseract  # type: ignore
except Exception:  # pragma: no cover - optional
    pytesseract = None

try:  # optional fast blur check
    import cv2  # type: ignore
except Exception:  # pragma: no cover - optional
    cv2 = None

try:  # bundled OCR, no system install
    from rapidocr_onnxruntime import RapidOCR  # type: ignore
except Exception:  # pragma: no cover - optional
    RapidOCR = None

D = os.environ.get("DATA_DIR", "data")
ANCHOR = os.environ.get("UDYAM_TODAY", "2026-07-15")
try:
    TODAY = dt.date.fromisoformat(ANCHOR)
except ValueError:
    TODAY = dt.date(2026, 7, 15)

G = pd.read_csv(f"{D}/mock_gstin_registry.csv").set_index("gstin")
U = pd.read_csv(f"{D}/mock_udyam_registry.csv").set_index("udyam_number")
CH = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

# OCR confusion pairs.  A correction is only accepted if the corrected value
# exists in the mock registry, so this cannot "invent" a valid GSTIN.
CONF: dict[str, str] = {
    "O": "0", "0": "O", "I": "1J", "1": "I", "J": "I", "S": "5", "5": "S",
    "B": "8", "8": "B", "T": "I7", "7": "T", "Z": "2", "2": "Z", "G": "6",
    "6": "G", "D": "0", "Q": "0", "U": "V", "V": "U",
}

_RAPID = None
_TESS = None


def _rapid() -> Any:
    global _RAPID
    if _RAPID is None and RapidOCR is not None:
        _RAPID = RapidOCR()
    return _RAPID


def _tess() -> Any:
    global _TESS
    if _TESS is None and pytesseract is not None:
        cmd = os.environ.get("TESSERACT_CMD")
        if cmd:
            pytesseract.pytesseract.tesseract_cmd = cmd
        try:
            pytesseract.get_tesseract_version()
            _TESS = pytesseract
        except Exception:
            _TESS = False
    return _TESS


def backend() -> str:
    pref = os.environ.get("OCR_BACKEND", "auto").lower()
    if pref == "rapid":
        return "rapidocr" if RapidOCR is not None else "none"
    if pref == "tesseract":
        return "tesseract" if _tess() else "none"
    if _tess():
        return "tesseract"
    if _rapid() is not None:
        return "rapidocr"
    return "none"


def _use_tesseract() -> bool:
    pref = os.environ.get("OCR_BACKEND", "auto").lower()
    if pref == "rapid":
        return False
    if pref == "tesseract":
        return bool(_tess())
    return bool(_tess())


# ---------------------------------------------------------------------------
# GSTIN helpers
# ---------------------------------------------------------------------------

def checksum(g: str) -> str:
    if len(g) != 15:
        return ""
    s = 0
    for i, c in enumerate(g[:14]):
        if c not in CH:
            return ""
        f = CH.index(c) * (1 if i % 2 == 0 else 2)
        s += f // 36 + f % 36
    return CH[(36 - s % 36) % 36]


def fix_gstin(g: str) -> tuple[str, bool]:
    """Try 1-2 OCR confusion substitutions against the registry."""
    g = re.sub(r"[^0-9A-Z]", "", str(g).upper())
    if len(g) != 15:
        return g, False
    if g in G.index:
        return g, False
    pos = [i for i, c in enumerate(g) if c in CONF]
    for n in (1, 2):
        for comb in itertools.combinations(pos, n):
            choices = [CONF[g[i]] for i in comb]
            # Keep the search small and deterministic.
            if len(choices) > 2 and n == 2:
                continue
            for sub in itertools.product(*choices):
                s = list(g)
                for i, ch in zip(comb, sub):
                    s[i] = ch
                cand = "".join(s)
                if cand in G.index:
                    return cand, True
    return g, False


def mask(g: str) -> str:
    g = str(g or "")
    if len(g) <= 6:
        return "*" * len(g)
    return g[:2] + "*" * (len(g) - 6) + g[-4:]


def _norm(s: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()


def same_name(a: str, b: str) -> bool:
    """Fuzzy entity match tolerant of OCR noise but strict enough for K07."""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return True
    if na in nb or nb in na:
        return True
    return SequenceMatcher(None, na, nb).ratio() >= 0.93


# ---------------------------------------------------------------------------
# OCR
# ---------------------------------------------------------------------------

def _sharpness(im: Image.Image) -> float:
    if cv2 is not None:
        return float(cv2.Laplacian(np.array(im), cv2.CV_64F).var())
    return float(np.var(np.array(im.filter(ImageFilter.FIND_EDGES))))


def deskew(im: Image.Image) -> tuple[Image.Image, int]:
    width = 600
    height = max(1, int(width * im.size[1] / im.size[0]))
    sm = im.resize((width, height))
    best = max(range(-8, 9), key=lambda a: np.var((np.array(sm.rotate(a, fillcolor=255)) < 120).sum(1)))
    if best:
        return im.rotate(best, expand=True, fillcolor=255, resample=Image.BICUBIC), best
    return im, 0


def _clean_lines(lines: list[str]) -> list[str]:
    drop = (
        "synthetic", "not a valid", "notavalid", "format for hackathon",
        "formatforhackathon", "accel ytl", "fictional", "specimen:",
    )
    out = []
    for line in lines:
        line = re.sub(r"\s+", " ", str(line)).strip()
        low = line.lower()
        if not line:
            continue
        if any(d in low for d in drop):
            continue
        out.append(line)
    return out


_GROUP_Y = 18


def _group_rapid(res: list) -> list[str]:
    rows = []
    for box, txt, conf in res or []:
        ys = [p[1] for p in box]
        xs = [p[0] for p in box]
        rows.append((min(ys), min(xs), str(txt), float(conf)))
    rows.sort()
    grouped: list[list] = []
    for y, x, txt, _conf in rows:
        if grouped and abs(y - grouped[-1][0]) < _GROUP_Y:
            grouped[-1][2].append((x, txt))
        else:
            grouped.append([y, x, [(x, txt)]])
    lines = []
    for _y, _x, parts in grouped:
        parts.sort()
        lines.append(" ".join(t for _, t in parts))
    return _clean_lines(lines)


def _score_lines(lines: list[str]) -> int:
    keys = (
        "gstin", "legal name", "trade name", "udyam", "valid till",
        "bill date", "billed to", "name", "enterprise name",
    )
    text = "\n".join(lines).lower()
    return sum(1 for k in keys if k in text)


def _rapid_ocr(im: Image.Image) -> list[str]:
    engine = _rapid()
    if engine is None:
        return []
    arr = np.array(im)
    best: list[str] = []
    best_score = -1
    for th in (140, 100, 160):
        bw = ((arr > th) * 255).astype("uint8")
        try:
            res, _ = engine(bw)
        except Exception:
            continue
        lines = _group_rapid(res)
        score = _score_lines(lines)
        if score > best_score:
            best, best_score = lines, score
        # Every supported document has at least two structural fields
        # (e.g. GSTIN + legal name, name + valid-till, bill-date + billed-to).
        # Threshold 140 reads almost all clean samples, including K03's pink
        # watermark. Avoid two extra ONNX passes unless the first read is poor.
        if th == 140 and score >= 2:
            return lines
    return best


def _ocr_impl(path: str) -> tuple[list[str], float, int]:
    im = Image.open(path).convert("L")
    sh = _sharpness(im)
    im2, ang = deskew(im)
    if _use_tesseract():
        raw = _tess().image_to_string(im2, config="--psm 6")
        lines = _clean_lines([ln.strip() for ln in raw.splitlines() if ln.strip()])
    else:
        lines = _rapid_ocr(im2)
    return lines, sh, ang


@lru_cache(maxsize=256)
def _ocr_sample_cached(path: str) -> tuple[list[str], float, int]:
    return _ocr_impl(path)


def ocr(path: str) -> tuple[list[str], float, int]:
    """OCR a document.

    Synthetic answer-key images are cached to keep the test suite fast.
    Customer uploads use random temp paths and are never cached, so extracted
    document text is not retained in process memory after the request.
    """
    norm = os.path.normpath(path)
    if "kyc_samples" in norm.split(os.sep):
        return _ocr_sample_cached(norm)
    return _ocr_impl(path)


# ---------------------------------------------------------------------------
# Field parsing
# ---------------------------------------------------------------------------

_LABELS = (
    "gstin", "legal name", "trade name", "constitution", "address of principal",
    "place of business", "date of liability", "type of registration",
    "udyam reg. number", "enterprise name", "major activity", "city", "state",
    "date of registration", "name", "designation", "for business", "id no.",
    "valid till", "bill date", "consumer no.", "billed to", "supply address",
    "units consumed", "amount due",
)


def _looks_label(s: str) -> bool:
    low = _norm(s)
    return any(low.startswith(_norm(l)) for l in _LABELS)


def field(lines: list[str], key: str) -> str:
    """Read `key: value` or a value on the next line, without swallowing a
    following label (important for K01's blank Udyam number)."""
    k = _norm(key)
    for i, line in enumerate(lines):
        low = _norm(line)
        if low == k or low.startswith(k + " "):
            rest = line[len(key):] if low.startswith(k) else ""
            rest = re.sub(r"^[\s:\-]+", "", rest).strip()
            if rest and not _looks_label(rest):
                return rest
            for nxt in lines[i + 1:i + 2]:
                nxt = re.sub(r"^[\s:\-]+", "", nxt).strip()
                if nxt and not _looks_label(nxt):
                    return nxt
    # Inline fallback: "... GSTIN 27X..."
    for line in lines:
        m = re.search(re.escape(key) + r"\s*[:\-]?\s*(.+)$", line, re.I)
        if m:
            val = m.group(1).strip()
            if val and not _looks_label(val):
                return val
    return ""


def _date(text: str) -> dt.date | None:
    m = re.search(r"(\d{4}-\d{2}-\d{2})", str(text))
    if not m:
        return None
    try:
        return dt.date.fromisoformat(m.group(1))
    except ValueError:
        return None


def _gstin_from(lines: list[str]) -> str:
    raw = field(lines, "GSTIN")
    raw = re.sub(r"[^0-9A-Za-z]", "", raw).upper()
    if len(raw) >= 15:
        return raw[:15]
    text = " ".join(lines).upper()
    m = re.search(r"\b[0-9O]{2}[A-Z0-9]{10}[A-Z0-9]{2}\b", text)
    return m.group(0) if m else raw


def _udyam_from(lines: list[str]) -> str:
    raw = field(lines, "Udyam reg. number")
    raw = re.sub(r"\s+", "", raw).upper()
    if raw:
        return raw
    m = re.search(r"UDYAM-[A-Z]{2}-\d{2}-\d{7}", " ".join(lines).upper())
    return m.group(0) if m else raw


# ---------------------------------------------------------------------------
# Main check
# ---------------------------------------------------------------------------

def check(
    folder: str,
    k: str,
    lane: str = "standard",
    udyam_number: str = "",
) -> tuple[str, str, list[dict]]:
    """Return (applicant key, verdict, findings).

    verdict is VERIFIED / ACTION_REQUIRED / REJECTED.
    lane is 'standard' or 'gst_free'.
    """
    lane = lane if lane in ("standard", "gst_free") else "standard"
    F: list[dict] = []

    def add(sev: str, code: str, msg: str, fix: str = "") -> None:
        F.append(dict(sev=sev, code=code, msg=msg, fix=fix))

    def p(name: str) -> str:
        return f"{folder}/{k}_{name}.png"

    docs = {
        "gst_certificate": "GST certificate",
        "udyam_certificate": "Udyam certificate",
        "signatory_id": "Signatory ID",
        "address_proof": "Address proof",
    }

    required = ["signatory_id", "address_proof"]
    if lane == "gst_free":
        required.append("udyam_certificate")
    else:
        required.extend(["gst_certificate", "udyam_certificate"])
    for name in required:
        if not os.path.exists(p(name)):
            # A typed Udyam number can replace the certificate upload.
            if name == "udyam_certificate" and udyam_number:
                continue
            fix = "Upload the document"
            if name == "udyam_certificate":
                fix = "Upload it, or share your Udyam number so we can look it up"
            add("action", "missing_doc", f"{docs[name]} not uploaded", fix)

    reg = None
    legal = ""
    trade = ""
    gstin = ""

    # ---- GST certificate (standard lane) --------------------------------
    if lane == "standard" and os.path.exists(p("gst_certificate")):
        gl, sh, ang = ocr(p("gst_certificate"))
        if sh < 100:
            add("action", "blurry", f"GST certificate is too blurry to read (sharpness {sh:.0f})", "Retake the photo in good light")
            return k, "ACTION_REQUIRED", F
        if ang:
            add("info", "deskew", f"Photo was tilted; auto-straightened ({ang} deg)")
        raw = _gstin_from(gl)
        gstin, corrected = fix_gstin(raw)
        legal = field(gl, "Legal name")
        trade = field(gl, "Trade name")
        if gstin in G.index:
            reg = G.loc[gstin]
        if reg is None and ang and trade:
            matches = G[G.trade_name.str.lower() == trade.lower()]
            if len(matches) == 1:
                gstin = matches.index[0]
                reg = matches.iloc[0]
                add("info", "name_match", "Tilted photo: GSTIN matched via trade name; confirm on retake")
        if reg is None:
            if not raw:
                add("action", "gstin_unreadable", "GSTIN could not be read from the certificate", "Retake the photo or type the GSTIN")
            else:
                valid = len(raw) == 15 and checksum(raw) == raw[14]
                add(
                    "reject", "gstin_unverified",
                    f"GSTIN {mask(raw)} not in registry and checksum {'valid' if valid else 'invalid'}: possible alteration",
                    "Verify the GSTIN with the GST portal",
                )
        else:
            if corrected:
                add("info", "ocr_fix", "OCR confusion corrected via registry")
            if str(reg.status) != "Active":
                add("reject", "gstin_status", f"GSTIN {mask(gstin)} is {reg.status}", "Reactivate GST registration, or continue with GST-free products")
            if legal and not same_name(legal, str(reg.legal_name)) and not ang:
                add("reject", "name_mismatch", "Legal name differs from registry", "Upload the correct certificate")
            if trade and len(gstin) == 15 and gstin[6] != str(reg.trade_name)[0].upper():
                add("reject", "pan_letter", "GSTIN PAN letter does not match trade name")

    # ---- Udyam ----------------------------------------------------------
    ureg = None
    un = re.sub(r"\s+", "", str(udyam_number or "")).upper()
    if os.path.exists(p("udyam_certificate")):
        ul, ush, _uang = ocr(p("udyam_certificate"))
        if ush < 100:
            add("action", "blurry_udyam", f"Udyam certificate is too blurry to read (sharpness {ush:.0f})", "Retake the photo in good light")
        else:
            un = un or _udyam_from(ul)
            if not un:
                add("action", "udyam_number", "Udyam number missing on certificate", "Re-upload a clear copy or type the number")
    if un:
        if un not in U.index:
            add("reject", "udyam_lookup", f"Udyam number {mask(un)} not found in registry")
        else:
            ureg = U.loc[un]
    elif lane == "gst_free" and not udyam_number:
        add("action", "udyam_number", "Udyam number is required for the GST-free lane", "Upload the certificate or type the Udyam number")

    # ---- Signatory ID ---------------------------------------------------
    sid_name = ""
    if os.path.exists(p("signatory_id")):
        il, ish, _iang = ocr(p("signatory_id"))
        if ish < 100:
            add("action", "blurry_id", f"Signatory ID is too blurry to read (sharpness {ish:.0f})", "Retake the photo in good light")
        else:
            sid_name = field(il, "Name")
            till = _date(field(il, "Valid till"))
            if not till:
                add("action", "id_date", "ID expiry date is unreadable", "Upload a clearer ID")
            elif till < TODAY:
                add("action", "id_expired", "Signatory ID has expired", "Upload a valid ID")
            for_business = field(il, "For business")
            known = [x for x in (trade, legal, str(reg.legal_name) if reg is not None else "", str(ureg.enterprise_name) if ureg is not None else "") if x]
            if for_business and known and not any(same_name(for_business, x) for x in known):
                add("warn", "id_business", "ID is issued for a different business name", "Add an authorisation letter or e-sign link")
            if sid_name and reg is not None and not same_name(sid_name, str(reg.legal_name)) and "LLP" not in str(reg.legal_name):
                add("warn", "signatory", "Signatory differs from registered proprietor", "Request an e-sign authorisation")

    # ---- Address proof --------------------------------------------------
    if os.path.exists(p("address_proof")):
        al, ash, _aang = ocr(p("address_proof"))
        if ash < 100:
            add("action", "blurry_address", f"Address proof is too blurry to read (sharpness {ash:.0f})", "Retake the photo in good light")
        else:
            bill = _date(field(al, "Bill date"))
            if not bill:
                add("action", "address_date", "Bill date unreadable", "Upload a clearer bill")
            elif bill > TODAY:
                add("warn", "address_future", "Bill date is in the future", "Upload a bill from the last 90 days")
            else:
                age = (TODAY - bill).days
                if age > 90:
                    add("action", "address_old", f"Address proof is {age} days old (limit 90)", "Upload a bill from the last 90 days")
            billed = field(al, "Billed to")
            known = [x for x in (trade, legal, str(reg.legal_name) if reg is not None else "", str(ureg.enterprise_name) if ureg is not None else "") if x]
            if billed and known and not any(same_name(billed, x) for x in known):
                add("warn", "address_name", "Address proof is in a different business name", "Upload a bill in the business name or add an authorisation")

    # ---- Cross-document -------------------------------------------------
    if reg is not None and ureg is not None:
        if not same_name(str(reg.legal_name), str(ureg.enterprise_name)) and not same_name(str(reg.trade_name), str(ureg.enterprise_name)):
            add("warn", "gst_udyam_name", "GST and Udyam business names differ", "Confirm the legal entity or upload matching documents")

    # De-duplicate repeated codes (e.g. a missing Udyam certificate and the
    # GST-free lane's "Udyam number required" message).  Missing-document
    # findings are kept per document.
    seen = set()
    unique = []
    for f in F:
        key = (f["code"], f["msg"]) if f["code"] == "missing_doc" else f["code"]
        if key in seen:
            continue
        seen.add(key)
        unique.append(f)
    F = unique

    v = "REJECTED" if any(f["sev"] == "reject" for f in F) else "ACTION_REQUIRED" if any(f["sev"] == "action" for f in F) else "VERIFIED"
    return k, v, F


def bind_order(folder: str, key: str, order: dict, udyam_number: str = "") -> list[dict]:
    """Bind extraction to this order, not merely to mutually consistent documents.

    Conservative checks intentionally send aliases/branch addresses to review.
    Uploaded raw fields are not added to the returned findings or persisted.
    """
    fields = {}
    for name in ("gst_certificate", "udyam_certificate", "signatory_id", "address_proof"):
        path = f"{folder}/{key}_{name}.png"
        fields[name] = ocr(path)[0] if os.path.exists(path) else []
    g, u, i, a = [fields[n] for n in ("gst_certificate", "udyam_certificate", "signatory_id", "address_proof")]
    names = [field(g, "Legal name"), field(g, "Trade name"), field(u, "Enterprise name")]
    number = udyam_number or _udyam_from(u)
    if number in U.index:
        names.append(str(U.loc[number].enterprise_name))
    normal = lambda value: re.sub(r"[^a-z0-9]", "", str(value).lower())
    findings = []
    def add(code, message):
        findings.append(dict(sev="action", code=code, msg=message, fix="Use matching order details and documents, or request an operator review."))
    if not normal(order["business_name"]) or normal(order["business_name"]) not in [normal(n) for n in names if n]:
        add("order_business_mismatch", "Document business identity does not match this order.")
    if not field(i, "Name") or normal(field(i, "Name")) != normal(order["contact_name"]):
        add("order_signatory_mismatch", "Signatory identity does not match this order's contact.")
    if not field(i, "For business") or normal(field(i, "For business")) not in [normal(n) for n in names if n]:
        add("order_authority_review", "Signatory authority for the registered business needs review.")
    address = field(a, "Supply address")
    if not address or SequenceMatcher(None, normal(address), normal(order["address"])).ratio() < .90:
        add("order_address_mismatch", "Address proof does not match this order's installation address.")
    return findings


if __name__ == "__main__":
    folder = sys.argv[1] if len(sys.argv) > 1 else "kyc_samples"
    out: dict[str, Any] = {}
    for k in sorted({os.path.basename(f)[:3] for f in glob.glob(f"{folder}/K*.png")}):
        k, v, F = check(folder, k)
        out[k] = dict(verdict=v, findings=F)
        print(k, v, "|", " ; ".join(f["msg"] for f in F if f["sev"] not in ("info", "warn")))
    json.dump(out, open("kyc_results.json", "w", encoding="utf-8"), indent=1)
