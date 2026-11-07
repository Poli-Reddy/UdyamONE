from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pandas as pd
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app as mod
import config
import kyc
import security
from test_workflow import event, new_order


client = TestClient(mod.app)


def test_demo_mode_is_opt_in(monkeypatch):
    monkeypatch.delenv("UDYAM_DEMO", raising=False)
    assert config.demo_mode() is False
    monkeypatch.setenv("UDYAM_DEMO", "true")
    assert config.demo_mode() is True


def test_customer_otp_session_and_logout():
    started = client.post("/api/auth/customer/request-otp", json={"phone": "9876543210"})
    assert started.status_code == 200, started.text
    challenge = started.json()
    assert challenge["debug_code"].isdigit()
    verified = client.post(
        "/api/auth/customer/verify-otp",
        json={"challenge_id": challenge["challenge_id"], "code": challenge["debug_code"]},
    )
    assert verified.status_code == 200, verified.text
    token = verified.json()["access_token"]
    headers = {"Authorization": "Bearer " + token}
    assert client.get("/api/auth/customer/session", headers=headers).json()["phone"] == "9876543210"
    assert client.post("/api/auth/customer/logout", headers=headers).json()["logged_out"]
    assert client.get("/api/auth/customer/session", headers=headers).status_code == 401


def test_customer_payload_removes_operator_evidence():
    order, _ = new_order(["email"])
    token = order["access_token"]
    internal = client.post("/api/orders/" + order["id"] + "/demo-verification").json()
    response, _ = event(internal, "main:P16:provision")
    assert response.status_code == 200
    customer = client.get(
        "/api/orders/" + order["id"], headers={"x-order-token": token}
    ).json()
    assert customer["activation"]["events"] == []
    assert all("evidence" not in task and "source" not in task for task in customer["activation"]["tasks"])


def test_staff_role_policy_is_enforced():
    context = security.set_principal(
        security.Principal(kind="staff", subject="advisor-1", roles=frozenset({security.SALES}))
    )
    try:
        assert mod._require_admin(None, frozenset({security.SALES})).subject == "advisor-1"
        with pytest.raises(HTTPException) as caught:
            mod._require_admin(None, security.ACTIVATION_WRITE_ROLES)
        assert caught.value.status_code == 403
    finally:
        security.reset_principal(context)


def test_partner_referral_is_signed_and_attributed():
    referral = client.get("/api/partner/referral?code=REP-PILOT")
    assert referral.status_code == 200, referral.text
    data = referral.json()
    assert data["qr_data_url"].startswith("data:image/png;base64,")
    query = parse_qs(urlparse(data["url"]).query)
    profile = dict(
        tier=1,
        employees=12,
        sites=1,
        gstin=True,
        type="clinic",
        needs=["email"],
        context=dict(
            locations=[dict(id="main", name="Main", employees=12, needs=["email"])],
            inventory_confirmed=True,
        ),
    )
    plan = client.post("/api/recommend", json=profile).json()["plan"]
    order = client.post(
        "/api/orders",
        json=dict(
            business_name="Partner Clinic",
            contact_name="Asha",
            phone="9876543210",
            address="1 Main Road, Delhi",
            profile=profile,
            consent=True,
            plan_fingerprint=plan["fingerprint"],
            partner_code=query["partner"][0],
            partner_exp=int(query["partner_exp"][0]),
            partner_sig=query["partner_sig"][0],
        ),
    )
    assert order.status_code == 200, order.text
    assert order.json()["partner_code"] == "REP-PILOT"


def test_missing_names_never_count_as_a_match():
    assert not kyc.same_name("", "Example Private Limited")
    assert not kyc.same_name("Example Private Limited", "")


def test_future_address_proof_requires_action(tmp_path, monkeypatch):
    gstin = "27AAAAA0000A1Z5"
    udyam = "UDYAM-MH-00-1234567"
    legal = "Example Private Limited"
    monkeypatch.setattr(
        kyc,
        "G",
        pd.DataFrame(
            [dict(gstin=gstin, legal_name=legal, trade_name="Example", status="Active")]
        ).set_index("gstin"),
    )
    monkeypatch.setattr(
        kyc,
        "U",
        pd.DataFrame([dict(udyam_number=udyam, enterprise_name=legal)]).set_index("udyam_number"),
    )
    lines = {
        "gst_certificate": [f"GSTIN: {gstin}", f"Legal name: {legal}"],
        "udyam_certificate": [f"Udyam reg. number: {udyam}", f"Enterprise name: {legal}"],
        "signatory_id": [f"Name: {legal}", f"For business: {legal}", "Valid till: 2030-01-01"],
        "address_proof": ["Bill date: 2030-01-01", f"Billed to: {legal}"],
    }
    for name in lines:
        (tmp_path / f"U_{name}.png").write_bytes(b"fixture")

    def fake_ocr(path):
        name = Path(path).stem.removeprefix("U_")
        return lines[name], 500, 0

    monkeypatch.setattr(kyc, "ocr", fake_ocr)
    _, verdict, findings = kyc.check(str(tmp_path), "U")
    assert verdict == "ACTION_REQUIRED"
    assert any(f["code"] == "address_future" and f["sev"] == "action" for f in findings)
