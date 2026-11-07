"""API tests.  They use the real app, SQLite order store and 5,000-lead model."""
import io
import os

from fastapi.testclient import TestClient

import app as appmod

client = TestClient(appmod.app)
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLES = os.path.join(HERE, "kyc_samples")


def rec(**kw):
    body = dict(tier=1, employees=5, sites=1, gstin=True, type="retail", needs=["pos"])
    body.update(kw)
    return client.post("/api/recommend", json=body)


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "ok"
    assert d["products"] == 25
    assert d["leads"] == 5000
    assert d["ocr"] in ("rapidocr", "tesseract")
    assert d["tts"] in ("microsoft-neural-online", "browser-fallback")
    voices = client.get("/api/voices").json()["voices"]
    assert voices["hi"]["female"] == "hi-IN-SwaraNeural"
    assert voices["ta"]["female"] == "ta-IN-PallaviNeural"
    assert voices["te"]["female"] == "te-IN-ShrutiNeural"
    assert client.post("/api/tts", json={"text": "test", "lang": "xx"}).status_code == 422


def test_recommend_and_gst_free():
    d = rec(gstin=False, needs=["pos", "whatsapp"]).json()
    assert "P19" in [i["id"] for i in d["quote"]["items"]]
    assert "P15" not in [i["id"] for i in d["quote"]["items"]]
    assert d["lane"] == "GST-free"
    assert d["speech_text"]
    tamil = rec(lang="ta", type="clinic", employees=12, needs=["email"]).json()
    assert "மாத கட்டணம்" in tamil["speech_text"]
    assert "Biz Fibre" not in tamil["speech_text"]


def test_order_persistence_and_tracker():
    body = dict(
        business_name="API Test Clinic",
        contact_name="Asha",
        phone="9876543210",
        email="asha@example.com",
        address="1 Main Road, Delhi",
        language="en",
        profile=dict(tier=1, employees=12, sites=1, gstin=True, type="clinic", needs=["email", "backup"]),
        off=[],
        consent=True,
    )
    r = client.post("/api/orders", json=body)
    assert r.status_code == 200, r.text
    o = r.json()
    assert o["status"] == "quote_accepted"
    assert o["monthly"] > 0
    assert [s["stage"] for s in o["tracker"]][0] == "Order placed"
    assert client.get("/api/orders/" + o["id"]).status_code == 401
    got = client.get("/api/orders/" + o["id"], headers={"x-order-token": o["access_token"]})
    assert got.status_code == 200
    assert got.json()["id"] == o["id"]


def test_order_requires_consent():
    r = client.post(
        "/api/orders",
        json=dict(
            business_name="No Consent",
            contact_name="Asha",
            phone="9999999999",
            address="Somewhere",
            profile=dict(tier=1, employees=5, sites=1, gstin=True, type="retail", needs=[]),
            consent=False,
        ),
    )
    assert r.status_code == 400


def test_backoffice_returns_scored_leads():
    r = client.get("/api/backoffice?limit=5")
    assert r.status_code == 200
    d = r.json()
    assert d["count"] <= 5
    assert d["leads"]
    assert all(0 <= x["score"] <= 100 for x in d["leads"])


def test_partner_qr():
    r = client.get("/api/partner/qr?code=REP1")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/png")


def _files(applicant, include_gst=True):
    names = {
        "gst": "gst_certificate",
        "udyam": "udyam_certificate",
        "sid": "signatory_id",
        "addr": "address_proof",
    }
    out = {}
    for key, suffix in names.items():
        if key == "gst" and not include_gst:
            continue
        path = os.path.join(SAMPLES, f"{applicant}_{suffix}.png")
        out[key] = (os.path.basename(path), open(path, "rb"), "image/png")
    return out


def test_kyc_standard_and_gst_free():
    files = _files("K03")
    try:
        r = client.post(
            "/api/kyc",
            data={"consent": "true", "lane": "standard", "order_id": "", "udyam_number": ""},
            files=files,
        )
    finally:
        for _, (_, fh, _) in files.items():
            fh.close()
    assert r.status_code == 200
    assert r.json()["verdict"] == "VERIFIED"

    files = _files("K03", include_gst=False)
    try:
        r = client.post(
            "/api/kyc",
            data={"consent": "true", "lane": "gst_free", "order_id": "", "udyam_number": ""},
            files=files,
        )
    finally:
        for _, (_, fh, _) in files.items():
            fh.close()
    assert r.status_code == 200
    assert r.json()["verdict"] == "VERIFIED"
    assert r.json()["lane"] == "gst_free"
