"""Credential-configured provider adapters. Missing/failed providers never become mock success."""
from __future__ import annotations

import hashlib
import hmac
import os
import re

import httpx
from fastapi import HTTPException


def settings(prefix: str, keys: tuple[str, ...]) -> dict:
    values = {k: os.getenv(prefix + k, "") for k in keys}
    if not all(values.values()):
        raise HTTPException(503, f"{prefix.rstrip('_')} is not configured; add the required account credentials on the server")
    return values


def status() -> dict:
    def configured(prefix, keys):
        return all(os.getenv(prefix+k) for k in keys)
    return dict(
        telecom=dict(status="not_connected", detail="Activation simulation and evidence recording are implemented. Approved Airtel provisioning API access and a product mapping are required."),
        verification=dict(status="configured_not_verified" if configured("CASHFREE_", ("CLIENT_ID", "CLIENT_SECRET")) else "not_configured", mode=os.getenv("CASHFREE_MODE", "sandbox"), provider="Cashfree Secure ID", detail="GST/Udyam registry evidence only; signatory and installation-address review remain separate."),
        payments=dict(status="configured_not_verified" if configured("RAZORPAY_", ("KEY_ID", "KEY_SECRET", "WEBHOOK_SECRET")) else "not_configured", mode=os.getenv("PAYMENT_MODE", "test"), provider="Razorpay hosted Payment Links", detail="Test checkout is supported. Live charges are disabled for the synthetic case catalogue."),
        whatsapp=dict(status="configured_not_verified" if configured("WHATSAPP_", ("TOKEN", "PHONE_NUMBER_ID", "APP_SECRET", "VERIFY_TOKEN", "GRAPH_VERSION")) else "not_configured", provider="Meta Cloud API", detail="Inbound guided advisor and phone-bound tracking; English pilot. No campaigns or automatic purchases."),
    )


async def request(method: str, url: str, **kwargs) -> dict:
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            response = await client.request(method, url, **kwargs)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("Expected provider object")
        return data
    except (httpx.HTTPError, ValueError):
        # Provider bodies may contain credentials or sensitive registry fields.
        raise HTTPException(502, "Provider request failed or returned an invalid response; no verification/payment was inferred")


def check_signature(raw: bytes, signature: str | None, secret: str, prefix: str = ""):
    expected = prefix + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    if not secret or not signature or not hmac.compare_digest(signature, expected):
        raise HTTPException(401, "Invalid webhook signature")


async def verify_registry(kind: str, number: str, business_name: str, reference: str) -> dict:
    cfg = settings("CASHFREE_", ("CLIENT_ID", "CLIENT_SECRET"))
    mode = os.getenv("CASHFREE_MODE", "sandbox")
    if mode not in ("sandbox", "production"):
        raise HTTPException(503, "CASHFREE_MODE must be sandbox or production")
    base = "https://sandbox.cashfree.com/verification" if mode == "sandbox" else "https://api.cashfree.com/verification"
    body = {"GSTIN": number, "business_name": business_name} if kind == "gstin" else {"verification_id": reference, "udyam": number}
    data = await request("POST", f"{base}/{kind}", headers={"x-client-id": cfg["CLIENT_ID"], "x-client-secret": cfg["CLIENT_SECRET"]}, json=body)
    if kind == "gstin":
        valid = data.get("valid") is True and data.get("gst_in_status") == "Active" and data.get("GSTIN") == number
        names = [data.get("legal_name_of_business", ""), data.get("trade_name_of_business", "")]
    else:
        valid = data.get("status") == "SUCCESS" and data.get("udyam_number") == number
        names = [data.get("enterprise_name", "")]
    # Exact normalized entity match is intentionally conservative; fuzzy names need review.
    normal = lambda value: re.sub(r"[^a-z0-9]", "", str(value).lower())
    match = bool(normal(business_name)) and normal(business_name) in [normal(n) for n in names]
    return dict(provider="cashfree", mode=mode, kind=kind, registry_valid=valid, business_match=match, outcome="matched" if valid and match else "review_required", reference=str(data.get("reference_id", "")), scope="registry identity only; does not verify signatory authority or installation address")


def payment_config():
    cfg = settings("RAZORPAY_", ("KEY_ID", "KEY_SECRET", "WEBHOOK_SECRET"))
    # This repository has a fictional catalogue. Adding a live key must not charge it.
    if os.getenv("PAYMENT_MODE", "test") != "test" or not cfg["KEY_ID"].startswith("rzp_test_"):
        raise HTTPException(503, "Live payments are disabled for the synthetic catalogue; use Razorpay test keys")
    return cfg


async def payment_link(order: dict, reference: str) -> dict:
    cfg = payment_config()
    amount = (order["monthly"] + order["setup"]) * 100
    data = await request("POST", "https://api.razorpay.com/v1/payment_links/", auth=(cfg["KEY_ID"], cfg["KEY_SECRET"]), json=dict(amount=amount, currency="INR", accept_partial=False, reference_id=reference, description="UdyamOne TEST: first month plus setup, synthetic quote; not a tax invoice", notify=dict(sms=False, email=False, whatsapp=False), reminder_enable=False, notes={"udyam_order_id": order["id"]}))
    if not data.get("id") or data.get("amount") != amount or data.get("currency") != "INR" or data.get("reference_id") != reference or not re.match(r"^https://rzp\.io/", data.get("short_url", "")):
        raise HTTPException(502, "Payment provider returned inconsistent link details; reconcile before retrying")
    return dict(provider_id=data["id"], url=data["short_url"], amount=amount, currency="INR", mode="test")


async def fetch_payment_link(provider_id: str) -> dict:
    cfg = payment_config()
    if not re.fullmatch(r"plink_[A-Za-z0-9]+", provider_id):
        raise HTTPException(400, "Invalid provider link ID")
    return await request("GET", "https://api.razorpay.com/v1/payment_links/" + provider_id, auth=(cfg["KEY_ID"], cfg["KEY_SECRET"]))


async def send_whatsapp(phone: str, message: str) -> dict:
    cfg = settings("WHATSAPP_", ("TOKEN", "PHONE_NUMBER_ID", "GRAPH_VERSION"))
    if not re.fullmatch(r"v\d+\.\d+", cfg["GRAPH_VERSION"]) or not cfg["PHONE_NUMBER_ID"].isdigit() or not re.fullmatch(r"\d{8,15}", phone):
        raise HTTPException(503, "WhatsApp version or phone configuration is invalid")
    return await request("POST", f"https://graph.facebook.com/{cfg['GRAPH_VERSION']}/{cfg['PHONE_NUMBER_ID']}/messages", headers={"Authorization": "Bearer " + cfg["TOKEN"]}, json=dict(messaging_product="whatsapp", recipient_type="individual", to=phone, type="text", text={"preview_url": False, "body": message[:4000]}))
