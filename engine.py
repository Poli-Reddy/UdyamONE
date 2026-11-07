"""UdyamOne rules engine.

The engine is the source of truth: eligibility, bundle selection, quantities,
pricing, dependencies, tracker dates and intent parsing are deterministic.
An LLM may explain a bundle but must never add a product or change a price.
"""
from __future__ import annotations

import datetime as dt
import math
import os
import re
from typing import Any

import pandas as pd

D = os.environ.get("DATA_DIR", "data")
ANCHOR = os.environ.get("UDYAM_TODAY", "2026-07-15")
try:
    TODAY = dt.date.fromisoformat(ANCHOR)
except ValueError:
    TODAY = dt.date(2026, 7, 15)

cat = pd.read_csv(f"{D}/product_catalogue.csv").set_index("product_id")

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

# Legacy aliases used by scenarios.py / older clients.
NEEDS = {
    "cctv": "cctv",
    "pos": "pos",
    "email": "email",
    "whatsapp": "whatsapp",
    "security": "security",
    "backup": "backup",
    "wifi": "wifi",
    "sms": "sms",
    "telephony": "telephony",
    "failover": "failover",
    "website": "website",
    "static_ip": "static_ip",
    "tollfree": "tollfree",
    "sdwan": "sdwan",
    "firewall": "firewall",
    "machine": "machine",
    "fleet": "fleet",
    "leased_line": "leased_line",
    "fibre": "fibre",
}

# Canonical need -> ordered product candidates.  The order is a business
# preference, not a substring match; this is deliberately explicit.
NEED_PRODUCTS: dict[str, list[str]] = {
    "cctv": ["P23"],
    "pos": ["P19"],
    "email": ["P16"],
    "whatsapp": ["P15"],
    "security": ["P20", "P21"],
    "backup": ["P17"],
    "wifi": ["P22"],
    "sms": ["P14"],
    "telephony": ["P12"],
    "failover": ["P09"],
    "website": ["P18"],
    "static_ip": ["P11"],
    "tollfree": ["P13"],
    "sdwan": ["P10"],
    "firewall": ["P21"],
    "machine": ["P24"],
    "fleet": ["P25"],
    "leased_line": ["P05", "P06"],
    "fibre": ["P01", "P02", "P03"],
}

NEED_ALIASES: dict[str, str] = {
    # CCTV / physical security
    "camera": "cctv", "cameras": "cctv", "cctv": "cctv", "surveillance": "cctv",
    # billing / retail
    "pos": "pos", "billing": "pos", "billing software": "pos", "billing machine": "pos",
    "cash register": "pos", "checkout": "pos", "inventory": "pos",
    # cloud productivity
    "email": "email", "e-mail": "email", "mail": "email", "office": "email",
    "google workspace": "email", "microsoft 365": "email", "storage": "backup",
    "cloud storage": "backup", "backup": "backup", "back-up": "backup",
    # messaging
    "whatsapp": "whatsapp", "whatsapp business": "whatsapp", "whatsapp api": "whatsapp",
    "sms": "sms", "bulk sms": "sms", "alerts": "sms",
    # voice
    "telephony": "telephony", "sip": "telephony", "ivr": "telephony",
    "phone": "telephony", "phone line": "telephony", "cloud phone": "telephony",
    "toll free": "tollfree", "toll-free": "tollfree", "1800": "tollfree",
    # network
    "wifi": "wifi", "wi-fi": "wifi", "guest wifi": "wifi", "access point": "wifi",
    "failover": "failover", "fail-over": "failover", "redundancy": "failover",
    "backup internet": "failover", "second line": "failover",
    "static ip": "static_ip", "static_ip": "static_ip", "fixed ip": "static_ip",
    "sdwan": "sdwan", "sd-wan": "sdwan", "central management": "sdwan",
    "multi-site network": "sdwan", "leased line": "leased_line",
    "leased_line": "leased_line", "dedicated line": "leased_line",
    "fibre": "fibre", "fiber": "fibre", "broadband": "fibre", "internet": "fibre",
    # security / IoT
    "security": "security", "cyber": "security", "cybersecurity": "security",
    "antivirus": "security", "endpoint": "security", "firewall": "firewall",
    "machine": "machine", "iot": "machine", "machine monitoring": "machine",
    "sensor": "machine", "fleet": "fleet", "gps": "fleet", "vehicle": "fleet",
    # web
    "website": "website", "web site": "website", "domain": "website",
    "landing page": "website",
}

# Business-type keywords.  The lead data uses labels such as "CA firm",
# "Plastic moulding unit", "Kirana store"; the UI uses short labels.
MFG_KEYS = (
    "manufactur", "factory", "workshop", "moulding", "molding", "packaging",
    "textile", "auto part", "auto-part", "printing press", "press",
    "furniture", "food processing", "processing unit", "industrial", "mill",
    "plant", "fabrication", "engineering unit", "chemical",
)
PROF_KEYS = (
    "clinic", "dental", "physio", "diagnostic", "pathology", "lab",
    "ca firm", "chartered accountant", "accountant", "law", "legal",
    "coaching", "academy", "school", "architecture", "architect", "studio",
    "travel agency", "travel", "real estate", "insurance", "consult",
    "office", "hotel", "resort", "advisory", "agency",
)
MICRO_KEYS = (
    "kirana", "stationery", "salon", "boutique", "mobile repair", "bakery",
    "sweet shop", "hardware", "pharmacy", "medical store", "optician",
    "retail", "shop", "store", "restaurant", "cafe", "café", "food",
    "tailor", "grocery", "supermarket", "laundry", "repair",
)

SEG_MICRO = "Micro retail & services"
SEG_PROF = "Professional offices"
SEG_MULTI = "Small multi-site businesses"
SEG_MFG = "Small manufacturers"
SEGMENTS = (SEG_MICRO, SEG_PROF, SEG_MULTI, SEG_MFG)

CORE_NAME_RE = re.compile(r"Fibre|AirFiber|Leased|Postpaid", re.I)
GST_ONLY_ALTERNATIVES = {
    "whatsapp": "website or cloud email",
    "sms": "website or cloud email",
    "telephony": "cloud email or website",
    "tollfree": "cloud email or website",
    "sdwan": "4G/5G failover or secure Wi-Fi",
    "firewall": "endpoint security",
    "machine": "cloud backup or CCTV",
    "leased_line": "business fibre",
}


def _s(value: Any) -> str:
    """Normalise a string for keyword matching."""
    return re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()


def _num(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Segment inference
# ---------------------------------------------------------------------------

def segment(emp: int, sites: int, btype: str = "") -> str:
    """Infer the case's four SMB segments from business type + size + sites.

    Sites win first: a 3-site kirana is a multi-site business.  Business type
    then decides manufacturer/professional/micro.  Size is only a fallback for
    unknown labels, never a reason to call a 30-person restaurant a factory.
    """
    emp, sites = _num(emp, 1), _num(sites, 1)
    t = _s(btype)
    if sites >= 3:
        return SEG_MULTI
    if any(k in t for k in MFG_KEYS):
        return SEG_MFG
    if any(k in t for k in PROF_KEYS):
        return SEG_PROF
    if any(k in t for k in MICRO_KEYS):
        return SEG_MICRO
    # Unknown type: conservative size fallback.
    if emp >= 26:
        return SEG_MFG
    if emp >= 7:
        return SEG_PROF
    return SEG_MICRO


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------

def _tiers(value: Any) -> list[int]:
    return [_num(x) for x in str(value).split(";") if str(x).strip()]


def reasons(pid: str, p: dict) -> list[str]:
    c = cat.loc[pid]
    r: list[str] = []
    tier = _num(p.get("tier"), 1)
    emp = _num(p.get("employees"), 1)
    sites = _num(p.get("sites"), 1)
    if tier not in _tiers(c.available_city_tiers):
        r.append(f"not sold in tier-{tier} cities")
    if emp < _num(c.min_employees, 1):
        r.append(f"needs {int(c.min_employees)}+ employees")
    if sites < _num(c.min_sites, 1):
        r.append(f"needs {int(c.min_sites)}+ sites")
    if str(c.requires_gstin).upper() == "Y" and not bool(p.get("gstin_active")):
        r.append("needs an active GSTIN")
    return r


def eligible(p: dict) -> dict[str, list[str]]:
    return {pid: reasons(pid, p) for pid in cat.index}


# ---------------------------------------------------------------------------
# Quantities and selection
# ---------------------------------------------------------------------------

def _qty(pid: str, p: dict, seg: str) -> int:
    """Quantity by catalogue unit.  This is where 'per site' becomes real."""
    emp = max(1, _num(p.get("employees"), 1))
    sites = max(1, _num(p.get("sites"), 1))
    name = str(cat.loc[pid].product_name)
    unit = str(cat.loc[pid].unit).lower()
    if "per site" in unit:
        return sites
    if "per camera" in unit:
        return max(2, 2 * sites)
    if "per counter" in unit:
        return sites
    if pid == "P24":  # machine-monitoring devices
        return max(1, min(5, math.ceil(emp / 20)))
    if "per user" in unit or "per device" in unit:
        # Not every employee needs a seat; the deck's ProOffice bundle uses
        # five seats for a 20-person office.  Scale by quarter, cap at 10.
        return max(1, min(10, math.ceil(emp / 4)))
    if "per line" in unit:
        if pid == "P07":
            return max(1, min(3, math.ceil(emp / 5)))
        return max(2, min(6, math.ceil(emp / 10)))
    if "per channel" in unit:
        return max(1, min(4, math.ceil(emp / 10)))
    if "per vehicle" in unit:
        return max(1, min(10, sites * 2))
    if "per ip" in unit:
        return sites
    # per account / per pack / per number
    return 1


def _first_ok(candidates: list[str], ok: set[str]) -> str | None:
    for pid in candidates:
        if pid in ok:
            return pid
    return None


def _needs(p: dict) -> list[str]:
    out: list[str] = []
    for raw in p.get("needs", []) or []:
        key = _s(raw).replace("-", " ")
        need = NEED_ALIASES.get(key, NEED_ALIASES.get(key.replace(" ", "_"), key.replace(" ", "_")))
        if need in NEED_PRODUCTS and need not in out:
            out.append(need)
    return out


def _choose_base(p: dict, seg: str, ok: set[str], needs: list[str]) -> str | None:
    emp = _num(p.get("employees"), 1)
    tier = _num(p.get("tier"), 1)
    del tier
    needs_fibre = bool({"failover", "static_ip", "sdwan", "fibre"}.intersection(needs))
    if "leased_line" in needs:
        wants = ["P06", "P05"] if emp >= 50 else ["P05", "P06"]
        pick = _first_ok(wants, ok)
        if pick:
            return pick

    if seg == SEG_MFG:
        order = []
        if emp >= 100:
            order.append("P06")
        if emp >= 10:
            order.append("P05")
        order += ["P03", "P02", "P01", "P04"]
        return _first_ok(order, ok)

    if seg == SEG_MULTI:
        order = []
        if emp >= 50:
            order.append("P03")
        order += ["P02", "P01", "P04"]
        return _first_ok(order, ok)

    if seg == SEG_PROF:
        order = []
        if emp >= 50:
            order.append("P03")
        if emp >= 7:
            order.append("P02")
        order += ["P01", "P04"]
        return _first_ok(order, ok)

    # Micro retail: 5G AirFiber is the fast-start default, but fibre is needed
    # by failover/static-IP/SD-WAN dependencies.
    order = (["P01", "P02", "P04"] if needs_fibre else ["P04", "P01", "P02"])
    return _first_ok(order, ok)


def _choose_mobile(seg: str, ok: set[str]) -> str | None:
    if seg == SEG_MICRO:
        return _first_ok(["P07", "P08"], ok)
    return _first_ok(["P08", "P07"], ok)


def _choose_need(need: str, seg: str, ok: set[str]) -> str | None:
    if need == "security" and seg in (SEG_MFG, SEG_MULTI) and "P21" in ok:
        return "P21"
    return _first_ok(NEED_PRODUCTS[need], ok)


def _missing_note(need: str, p: dict, ok: set[str]) -> str:
    candidates = NEED_PRODUCTS[need]
    for pid in candidates:
        rs = reasons(pid, p)
        if rs:
            name = str(cat.loc[pid].product_name)
            alt = GST_ONLY_ALTERNATIVES.get(need, "a suitable alternative")
            if any("GSTIN" in r for r in rs):
                return f"{name}: needs an active GSTIN. GST-free alternative: {alt}."
            return f"{name} not available: {', '.join(rs)}."
    return f"No product in the catalogue matches '{need}' for your profile."


def recommend(p: dict) -> tuple[str, dict[str, int], list[str]]:
    """Return (segment, {product_id: qty}, notes). Deterministic."""
    p = dict(p or {})
    p.setdefault("tier", 1)
    p.setdefault("employees", 1)
    p.setdefault("sites", 1)
    p.setdefault("gstin_active", False)
    seg = segment(p["employees"], p["sites"], p.get("type", ""))
    el = eligible(p)
    ok = {pid for pid, rs in el.items() if not rs}
    needs = _needs(p)
    pick: dict[str, int] = {}
    notes: list[str] = []

    base = _choose_base(p, seg, ok, needs)
    if base:
        pick[base] = _qty(base, p, seg)
    else:
        notes.append("No connectivity product is eligible; check city tier, employees and GSTIN.")

    mob = _choose_mobile(seg, ok)
    if mob:
        pick[mob] = _qty(mob, p, seg)

    for need in needs:
        pid = _choose_need(need, seg, ok)
        if pid:
            pick[pid] = max(pick.get(pid, 0), _qty(pid, p, seg))
        else:
            notes.append(_missing_note(need, p, ok))

    # Resolve requires_one_of dependencies.  Add the cheapest eligible base
    # rather than silently dropping a requested product where possible.
    changed = True
    while changed:
        changed = False
        for pid in list(pick):
            req = cat.loc[pid].requires_one_of
            if isinstance(req, str) and not set(req.split("|")).intersection(pick):
                candidates = [x for x in req.split("|") if x in ok]
                if candidates:
                    add = sorted(candidates, key=lambda x: cat.loc[x].price_min_inr_per_month)[0]
                    pick[add] = max(pick.get(add, 0), _qty(add, p, seg))
                    if add != base:
                        notes.append(
                            f"Added {cat.loc[add].product_name} because {cat.loc[pid].product_name} needs it."
                        )
                else:
                    del pick[pid]
                    notes.append(f"{cat.loc[pid].product_name} dropped: it needs {req.replace('|', ' / ')}.")
                changed = True

    # A requested 'fibre' must not end up on 5G AirFiber; 'failover' etc. are
    # already handled by the base choice and dependency resolver.
    if "fibre" in needs and base == "P04" and "P01" in ok:
        del pick["P04"]
        pick["P01"] = _qty("P01", p, seg)
        notes.append("Switched to Biz Fibre 100 because you asked for fibre.")

    # De-duplicate notes while preserving order.
    seen: set[str] = set()
    notes = [n for n in notes if not (n in seen or seen.add(n))]
    return seg, pick, notes


# ---------------------------------------------------------------------------
# Quote
# ---------------------------------------------------------------------------

def _price_point(pid: str, seg: str) -> int:
    lo = int(cat.loc[pid].price_min_inr_per_month)
    hi = int(cat.loc[pid].price_max_inr_per_month)
    pos = 0.30 if seg == SEG_MICRO else 0.50
    return int(round(lo + pos * (hi - lo)))


def _is_core(pid: str) -> bool:
    return bool(CORE_NAME_RE.search(str(cat.loc[pid].product_name)))


def quote(pick: dict[str, int], seg: str, addons_off: tuple | list = (), p: dict | None = None) -> dict:
    """Build a transparent quote.  Core connectivity/mobile cannot be removed;
    add-ons are togglable.  Dependents are removed if their base is removed.
    """
    off = {str(x) for x in (addons_off or [])}
    selected: dict[str, int] = {}
    for pid, qty in pick.items():
        if pid in off and not _is_core(pid):
            continue
        selected[pid] = max(1, _num(qty, 1))

    # Remove dependents whose required base is no longer present.
    changed = True
    while changed:
        changed = False
        for pid in list(selected):
            req = cat.loc[pid].requires_one_of
            if isinstance(req, str) and not set(req.split("|")).intersection(selected):
                del selected[pid]
                changed = True

    items = []
    for pid, qty in selected.items():
        c = cat.loc[pid]
        unit = _price_point(pid, seg)
        items.append(
            dict(
                id=pid,
                name=str(c.product_name),
                category=str(c.category),
                description=str(c.description),
                unit_type=str(c.unit),
                qty=qty,
                unit=unit,
                monthly=unit * qty,
                setup=int(c.one_time_setup_inr) * qty,
                months=int(c.min_contract_months),
                days=int(c.typical_install_days),
                core=_is_core(pid),
                addon=not _is_core(pid),
            )
        )

    core_monthly = sum(i["monthly"] for i in items if i["core"])
    addon_monthly = sum(i["monthly"] for i in items if not i["core"])
    return dict(
        items=items,
        monthly=sum(i["monthly"] for i in items),
        setup=sum(i["setup"] for i in items),
        contract_months=max([i["months"] for i in items] or [0]),
        core_monthly=core_monthly,
        addon_monthly=addon_monthly,
        currency="INR",
    )


# ---------------------------------------------------------------------------
# Tracker
# ---------------------------------------------------------------------------

STAGES = ("Order placed", "Quote accepted", "KYC complete", "Install scheduled", "Activated")
STATUS_INDEX = {
    "placed": 0,
    "quote_accepted": 1,
    "kyc_pending": 1,
    "kyc_complete": 2,
    "install_scheduled": 3,
    "activated": 4,
}


def tracker(q: dict, kyc_done: dt.date = TODAY, order: dict | None = None) -> list[dict]:
    """Return a real stage list.  If an order exists, use its status; otherwise
    return a projection with honest pending states (never fake 'KYC complete').
    """
    days = max([_num(x.get("days"), 0) for x in q.get("items", [])] or [0])
    order = order or {}
    status = str(order.get("status") or "quote_accepted")
    current = STATUS_INDEX.get(status, 1)
    base = kyc_done
    dates = [
        base,
        base,
        base,
        base + dt.timedelta(days=1),
        base + dt.timedelta(days=days),
    ]
    out = []
    for i, label in enumerate(STAGES):
        state = "done" if i < current else "current" if i == current else "pending"
        if status == "activated":
            state = "done"
        out.append(dict(stage=label, date=str(dates[i]), state=state))
    return out


# ---------------------------------------------------------------------------
# Deterministic intent parsing for the free-text advisor
# ---------------------------------------------------------------------------

_WORD_NUM = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}


def _number_before(text: str, words: str) -> int | None:
    m = re.search(rf"(\d{{1,4}}|" + "|".join(_WORD_NUM) + rf")\s*(?:{words})\b", text, re.I)
    if not m:
        return None
    token = m.group(1).lower()
    return int(token) if token.isdigit() else _WORD_NUM[token]


def parse_intent(text: str) -> dict:
    """Parse a free-text business description into structured advisor input.

    This is deliberately rules-based (no hallucination).  Optional LLM parsing
    can be layered on top, but the result must still pass these validators.
    """
    t = " " + _s(text) + " "
    found: dict[str, Any] = {}
    if not t.strip():
        return dict(profile=found, needs=[], missing=["type", "employees", "sites", "tier", "gstin"])

    emp = (
        _number_before(t, "employees?|people|staff|persons|workers|team members|headcount")
        or _number_before(t, "employee|person|worker")
    )
    if emp:
        found["employees"] = emp

    sites = _number_before(t, "sites?|shops?|stores?|branches?|locations?|outlets?")
    if sites:
        found["sites"] = sites
    elif any(k in t for k in ("multi site", "multi-site", "multiple sites", "chain")):
        found["sites"] = 3
    elif "single site" in t or "one shop" in t or "one store" in t:
        found["sites"] = 1

    if re.search(r"\btier\s*2\b|\btier two\b|\btier-2\b", t):
        found["tier"] = 2
    elif re.search(r"\btier\s*1\b|\btier one\b|\btier-1\b|\bmetro\b", t):
        found["tier"] = 1

    if re.search(r"\b(no|without|not have|don t have|do not have)\s+gst", t) or "gst free" in t or "gst-free" in t:
        found["gstin"] = False
    elif "gst" in t or "gstin" in t:
        found["gstin"] = True

    # Type detection.
    type_map = [
        ("manufacturer", ("manufactur", "factory", "workshop", "industrial", "plant")),
        ("clinic", ("clinic", "dental", "physio", "diagnostic", "hospital", "doctor")),
        ("office", ("ca firm", "law office", "office", "consult", "architect", "insurance")),
        ("coaching", ("coaching", "academy", "school", "classes")),
        ("restaurant", ("restaurant", "cafe", "café", "food")),
        ("retail", ("retail", "kirana", "shop", "store", "salon", "bakery", "pharmacy", "boutique")),
    ]
    for label, keys in type_map:
        if any(k in t for k in keys):
            found["type"] = label
            break

    needs: list[str] = []
    for alias, need in sorted(NEED_ALIASES.items(), key=lambda kv: -len(kv[0])):
        if alias in t and need not in needs:
            needs.append(need)
    # 'internet' is a generic need; only add fibre if nothing more specific.
    if "internet" in t and "fibre" not in needs and "leased_line" not in needs and "sdwan" not in needs:
        needs.append("fibre")

    missing = [k for k in ("type", "employees", "sites", "tier", "gstin") if k not in found]
    return dict(profile=found, needs=needs, missing=missing)
