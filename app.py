"""UdyamOne server.

Rules decide; an optional LLM only explains.  The app is intentionally
self-contained: SQLite for orders, CSV for the catalogue/registries, and an
OCR backend that falls back from Tesseract to RapidOCR.
"""
from __future__ import annotations

import datetime as dt
import asyncio
import base64
import io
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import tempfile
import urllib.request
import urllib.parse
from collections import OrderedDict
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from fastapi import FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from engine import D, TODAY, SEGMENTS, cat, eligible, parse_intent, quote, recommend, segment, tracker
from planner import BusinessContext, make_plan, bill_draft
import activation
from assistance import Simulation, route, simulate
from config import SETTINGS, demo_mode
from rate_limit import LIMITER, LIMITS
from security import (
    ADMIN,
    ACTIVATION_WRITE_ROLES,
    ANALYTICS_ROLES,
    BILLING_ROLES,
    KYC_WRITE_ROLES,
    MANAGER,
    ORDER_READ_ROLES,
    PARTNER,
    SALES,
    STAFF_ROLES,
    Principal,
    current_principal,
    has_any_role,
    reset_principal,
    set_principal,
    verify_oidc_token,
)
from db import DB_PATH, connect as db_connect

BASE = Path(__file__).resolve().parent
STATIC = BASE / "static" / "index.html"
DB = DB_PATH
CONSENT_LOG = Path(os.environ.get("UDYAM_CONSENT_LOG", BASE / "consent_log.jsonl"))
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
LANGS = {"en": "English", "hi": "Hindi", "ta": "Tamil", "te": "Telugu"}
TTS_VOICES = {
    "en": {"female": "en-IN-NeerjaExpressiveNeural", "male": "en-IN-PrabhatNeural"},
    "hi": {"female": "hi-IN-SwaraNeural", "male": "hi-IN-MadhurNeural"},
    "ta": {"female": "ta-IN-PallaviNeural", "male": "ta-IN-ValluvarNeural"},
    "te": {"female": "te-IN-ShrutiNeural", "male": "te-IN-MohanNeural"},
}
TTS_RATES = {"en": "+0%", "hi": "-3%", "ta": "-6%", "te": "-5%"}
_TTS_CACHE: OrderedDict[tuple[str, str, str], bytes] = OrderedDict()
_TTS_CACHE_MAX = 64

app = FastAPI(title="UdyamOne", version="3.0.0")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
_OCR_SEMAPHORE = asyncio.Semaphore(SETTINGS.ocr_concurrency)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    length = request.headers.get("content-length")
    if length:
        try:
            if int(length) > SETTINGS.max_request_bytes:
                return JSONResponse({"detail": "Request body is too large"}, status_code=413)
        except ValueError:
            return JSONResponse({"detail": "Invalid Content-Length header"}, status_code=400)

    client = request.client.host if request.client else "unknown"
    if SETTINGS.trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
        if forwarded:
            client = forwarded
    rule = LIMITS.get(request.url.path)
    if rule and not demo_mode():
        allowed, retry = await LIMITER.allow(client, request.url.path, *rule)
        if not allowed:
            return JSONResponse(
                {"detail": "Too many requests; try again later"},
                status_code=429,
                headers={"Retry-After": str(retry)},
            )

    principal = None
    authorization = request.headers.get("authorization", "")
    if authorization:
        scheme, _, credential = authorization.partition(" ")
        if scheme.lower() != "bearer" or not credential:
            return JSONResponse({"detail": "Invalid authorization header"}, status_code=401)
        try:
            if credential.startswith("uoc_"):
                principal = _customer_principal(credential)
            else:
                principal = await asyncio.to_thread(verify_oidc_token, credential)
        except ValueError:
            return JSONResponse({"detail": "Invalid or expired access token"}, status_code=401)
    context_token = set_principal(principal)
    try:
        response = await call_next(request)
    finally:
        reset_principal(context_token)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), geolocation=(), microphone=(self), payment=()"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["X-Request-ID"] = request.headers.get("x-request-id", secrets.token_hex(12))[:128]
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    if SETTINGS.production:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; connect-src 'self'; media-src 'self' blob:; "
        "frame-ancestors 'none'"
    )
    return response

LEADS = pd.read_csv(f"{D}/smb_leads.csv")
ACTIVATED = LEADS[LEADS.outcome == "Activated"]
SEG_MEDIAN = ACTIVATED.groupby("segment").monthly_bundle_value_inr.median().to_dict()

ACT = {
    "Price too high": "Offer the starter bundle near current spend",
    "Competitor offered cheaper plan": "Send a price-match comparison",
    "Quote too complex to understand": "Re-send the plain-language quote",
    "Quote took too long to arrive": "Instant digital quote",
    "Missing GST or Udyam document": "Switch to the GST-free lane or Udyam lookup",
    "Found paperwork too long": "Assisted KYC by partner",
    "Document details did not match": "Fix-it KYC with retake link",
    "Authorised signatory not available": "E-sign link to the authorised person",
    "Installation delayed beyond 21 days": "Offer 5G AirFiber fast-start",
    "Customer cancelled before install": "Confirm the install slot by WhatsApp",
    "First payment failed": "UPI autopay mandate",
    "Site not feasible for fibre": "Offer 5G AirFiber",
    "Owner had no time for a visit": "Self-serve link on WhatsApp",
    "Visit not scheduled by sales": "Self-serve link on WhatsApp",
}

_MODEL = None
_MODEL_AUC: float | None = None


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

def _normalise_phone(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\+?[0-9 ()-]+", value.strip()):
        raise ValueError("Enter a valid phone number")
    digits = re.sub(r"\D", "", value)
    if not 10 <= len(digits) <= 15:
        raise ValueError("Phone number must contain 10 to 15 digits")
    return ("+" if value.strip().startswith("+") else "") + digits

class Profile(BaseModel):
    tier: int = Field(default=1, ge=1, le=2)
    employees: int = Field(default=1, ge=1, le=10000)
    sites: int = Field(default=1, ge=1, le=100)
    gstin: bool = False
    gstin_active: bool | None = None
    type: str = Field(default="", max_length=120)
    needs: list[str] = Field(default_factory=list, max_length=24)
    current_spend: int | None = Field(default=None, ge=0, le=10000000)
    current_provider: str = Field(default="", max_length=120)
    budget: int | None = Field(default=None, ge=0, le=10000000)
    state: str = Field(default="", max_length=80)
    context: BusinessContext | None = None

    def as_engine_profile(self) -> dict:
        p = self.model_dump(mode="json")
        if self.context and len(self.context.locations) != self.sites:
            raise HTTPException(422, "Location count must match the business site count")
        p["gstin_active"] = bool(self.gstin if self.gstin_active is None else self.gstin_active)
        return p


class RecommendRequest(Profile):
    off: list[str] = Field(default_factory=list, max_length=25)
    lang: str = "en"


class OrderRequest(BaseModel):
    business_name: str = Field(min_length=2, max_length=160)
    contact_name: str = Field(min_length=2, max_length=120)
    phone: str = Field(min_length=7, max_length=32)
    email: str = ""
    address: str = Field(min_length=5, max_length=400)
    language: str = "en"
    profile: Profile
    off: list[str] = Field(default_factory=list, max_length=25)
    partner_code: str = Field(default="", max_length=64)
    partner_exp: int | None = Field(default=None, ge=0)
    partner_sig: str = Field(default="", max_length=128)
    consent: bool = False
    plan_fingerprint: str = ""

    @field_validator("business_name", "contact_name", "address", mode="before")
    @classmethod
    def trim_contact_fields(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value):
        return _normalise_phone(value)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value):
        value = value.strip()
        if value and (len(value) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value)):
            raise ValueError("Enter a valid email address or leave it blank")
        return value

    def clean_phone(self) -> str:
        return re.sub(r"[^0-9+]", "", self.phone)


class IntentRequest(BaseModel):
    text: str = Field(default="", max_length=1500)
    lang: str = "en"


class StatusRequest(BaseModel):
    status: str


class RecoveryRequest(BaseModel):
    allow_wireless: bool = False
    consent: bool = False
    base_fingerprint: str = ""
    candidate_fingerprint: str = ""


class TTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=800)
    lang: Literal["en", "hi", "ta", "te"] = "en"
    gender: Literal["female", "male"] = "female"

    @field_validator("text", mode="before")
    @classmethod
    def trim_speech(cls, value):
        return re.sub(r"\s+", " ", value).strip() if isinstance(value, str) else value


class OTPStartRequest(BaseModel):
    phone: str = Field(min_length=7, max_length=32)

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value):
        return _normalise_phone(value)


class OTPVerifyRequest(BaseModel):
    challenge_id: str = Field(pattern=r"^[A-Za-z0-9_-]{20,100}$")
    code: str = Field(pattern=r"^\d{6}$")


# ---------------------------------------------------------------------------
# Orders database
# ---------------------------------------------------------------------------

def _conn():
    return db_connect()


def init_db() -> None:
    with _conn() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                business_name TEXT NOT NULL,
                contact_name TEXT NOT NULL,
                phone TEXT NOT NULL,
                email TEXT,
                address TEXT NOT NULL,
                language TEXT NOT NULL,
                segment TEXT NOT NULL,
                lane TEXT NOT NULL,
                items_json TEXT NOT NULL,
                monthly INTEGER NOT NULL,
                setup INTEGER NOT NULL,
                contract_months INTEGER NOT NULL,
                status TEXT NOT NULL,
                kyc_status TEXT NOT NULL,
                kyc_verdict TEXT,
                partner_code TEXT,
                consent INTEGER NOT NULL,
                consent_version TEXT NOT NULL,
                profile_json TEXT NOT NULL
            )
            """
        )
        activation.init_tables(c)
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS auth_otp_challenges (
                id TEXT PRIMARY KEY, phone TEXT NOT NULL, code_hash TEXT NOT NULL,
                created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0, consumed_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_auth_otp_phone_created
                ON auth_otp_challenges(phone, created_at);
            CREATE TABLE IF NOT EXISTS customer_sessions (
                token_hash TEXT PRIMARY KEY, phone TEXT NOT NULL,
                created_at TEXT NOT NULL, expires_at TEXT NOT NULL, revoked_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_customer_sessions_phone
                ON customer_sessions(phone);
            CREATE TABLE IF NOT EXISTS order_customers (
                order_id TEXT PRIMARY KEY, phone TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_order_customers_phone
                ON order_customers(phone);
            CREATE TABLE IF NOT EXISTS consent_events (
                id TEXT PRIMARY KEY, occurred_at TEXT NOT NULL, purpose TEXT NOT NULL,
                notice TEXT NOT NULL, order_id TEXT, lane TEXT, outcome TEXT,
                codes_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_consent_order_time
                ON consent_events(order_id, occurred_at);
            """
        )


if SETTINGS.auto_create_schema:
    init_db()
else:
    try:
        with _conn() as c:
            c.execute("SELECT id FROM orders LIMIT 1").fetchone()
    except Exception as exc:
        raise RuntimeError("Database schema is not ready; run `alembic upgrade head`") from exc


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _otp_secret() -> bytes:
    # Production startup validation requires an explicit pepper. A process-local
    # fallback is sufficient only for an explicitly enabled local demo.
    if SETTINGS.otp_pepper:
        return SETTINGS.otp_pepper.encode()
    if demo_mode():
        value = getattr(_otp_secret, "_demo", None)
        if value is None:
            value = secrets.token_bytes(32)
            setattr(_otp_secret, "_demo", value)
        return value
    raise ValueError("Customer OTP is not configured")


def _partner_secret() -> bytes:
    if SETTINGS.partner_referral_secret:
        return SETTINGS.partner_referral_secret.encode()
    if demo_mode():
        value = getattr(_partner_secret, "_demo", None)
        if value is None:
            value = secrets.token_bytes(32)
            setattr(_partner_secret, "_demo", value)
        return value
    raise HTTPException(503, "Partner referral signing is not configured")


def _partner_signature(code: str, expires: int) -> str:
    return hmac.new(_partner_secret(), f"{code}:{expires}".encode(), hashlib.sha256).hexdigest()


def _otp_hash(challenge_id: str, code: str) -> str:
    return hmac.new(_otp_secret(), f"{challenge_id}:{code}".encode(), hashlib.sha256).hexdigest()


def _customer_principal(token: str) -> Principal:
    if not token.startswith("uoc_") or len(token) < 40:
        raise ValueError("Invalid customer token")
    digest = hashlib.sha256(token.encode()).hexdigest()
    with _conn() as c:
        row = c.execute(
            "SELECT phone,expires_at,revoked_at FROM customer_sessions WHERE token_hash=?",
            (digest,),
        ).fetchone()
    if not row or row["revoked_at"] or dt.datetime.fromisoformat(row["expires_at"]) <= _utcnow():
        raise ValueError("Expired customer token")
    return Principal(kind="customer", subject="phone:" + row["phone"], phone=row["phone"], token_hash=digest)


async def _deliver_otp(phone: str, code: str) -> None:
    if demo_mode():
        return
    if not (SETTINGS.otp_delivery_url and SETTINGS.otp_delivery_token):
        raise HTTPException(503, "Customer sign-in delivery service is not configured")
    import httpx

    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            response = await client.post(
                SETTINGS.otp_delivery_url,
                headers={"Authorization": "Bearer " + SETTINGS.otp_delivery_token},
                json={
                    "destination": phone,
                    "template": "udyamone_login",
                    "variables": {"code": code, "valid_minutes": 5},
                },
            )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(503, "Sign-in code could not be delivered; try again later") from exc


def _record_consent(
    purpose: str,
    notice: str,
    order_id: str | None = None,
    lane: str | None = None,
    outcome: str | None = None,
    codes: list[str] | None = None,
    connection=None,
) -> None:
    values = (
        "ce_" + secrets.token_hex(16),
        _utcnow().isoformat(timespec="seconds"),
        purpose,
        notice,
        order_id,
        lane,
        outcome,
        json.dumps(codes or []),
    )
    sql = "INSERT INTO consent_events (id,occurred_at,purpose,notice,order_id,lane,outcome,codes_json) VALUES (?,?,?,?,?,?,?,?)"
    if connection is not None:
        connection.execute(sql, values)
    else:
        with _conn() as c:
            c.execute(sql, values)


def _lane_for(p: dict) -> str:
    return "Standard" if p.get("gstin_active") else "GST-free"


RECORDING_DEMO_PHONE = "8074982840"
RECORDING_DEMO_ORDER = "UO-DEMO-8074982840"


def _prepare_recording_demo(phone: str) -> str | None:
    """Reset one synthetic recording account; never available outside demo mode."""
    if not demo_mode() or phone != RECORDING_DEMO_PHONE:
        return None
    profile = {
        "tier": 1,
        "employees": 2,
        "sites": 2,
        "gstin_active": True,
        "type": "manufacturer",
        "needs": ["cctv", "whatsapp"],
        "context": {
            "locations": [
                {"id": "site-1", "name": "Main workshop", "tier": 1, "employees": 1, "needs": ["cctv", "whatsapp"], "quantities": {}},
                {"id": "site-2", "name": "Storage unit", "tier": 1, "employees": 1, "needs": ["cctv", "whatsapp"], "quantities": {}},
            ],
            "owned": [],
            "inventory_confirmed": True,
            "budget": 5000,
            "desired_start": None,
            "wait_for_contract": False,
        },
    }
    plan = make_plan(profile)
    if not plan["ready"]:
        raise RuntimeError("Recording demo plan is not ready")
    quote_data, now = plan["quote"], _utcnow().isoformat(timespec="seconds")
    with _conn() as connection:
        connection.execute("BEGIN IMMEDIATE")
        for table in ("activation_events", "activation_tasks", "plan_revisions", "decision_plans", "order_customers", "consent_events", "payment_intents", "registry_evidence", "interventions"):
            connection.execute(f"DELETE FROM {table} WHERE order_id=?", (RECORDING_DEMO_ORDER,))
        connection.execute("DELETE FROM orders WHERE id=?", (RECORDING_DEMO_ORDER,))
        connection.execute(
            "INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                RECORDING_DEMO_ORDER, now, "Ganesh Woodcraft", "Joseph Singh", phone,
                "joseph@example.com", "Shop/Unit 80, Civil Lines, Nagpur, Maharashtra - 411231",
                "en", plan["segment"], "Standard", json.dumps(quote_data["items"], ensure_ascii=False),
                quote_data["monthly"], quote_data["setup"], quote_data["contract_months"],
                "install_scheduled", "verified", "VERIFIED", "", 1, "demo-recording-v1",
                json.dumps(profile, ensure_ascii=False),
            ),
        )
        access_token = secrets.token_urlsafe(32)
        connection.execute("INSERT INTO decision_plans VALUES (?,?,?,?)", (RECORDING_DEMO_ORDER, json.dumps(plan, ensure_ascii=False), hashlib.sha256(access_token.encode()).hexdigest(), now))
        connection.execute("INSERT INTO order_customers (order_id,phone) VALUES (?,?)", (RECORDING_DEMO_ORDER, phone))
        activation.seed(connection, RECORDING_DEMO_ORDER, quote_data["items"])
        events = [
            activation.TaskEvent(event_id="demo-verification", task_id="verification", action="complete", expected_version=0, evidence="Preloaded synthetic K03 verification for recording"),
            activation.TaskEvent(event_id="demo-whatsapp-setup", task_id="business:P15:provision", action="complete", expected_version=0, evidence="Synthetic WhatsApp workspace configured"),
            activation.TaskEvent(event_id="demo-whatsapp-check", task_id="business:P15:check", action="complete", expected_version=0, evidence="Synthetic message send and receive check completed"),
        ]
        for item in events:
            activation.record(connection, RECORDING_DEMO_ORDER, item, "demo_only", "recording-demo")
        _record_consent("Preloaded synthetic recording journey", "recording-demo-v1", RECORDING_DEMO_ORDER, "Standard", "accepted", connection=connection)
    return RECORDING_DEMO_ORDER


def _order_dict(row: Any) -> dict:
    d = dict(row)
    d["items"] = json.loads(d.pop("items_json"))
    d["profile"] = json.loads(d.pop("profile_json"))
    d["consent"] = bool(d["consent"])
    try:
        start = dt.date.fromisoformat(str(d["created_at"])[:10])
    except ValueError:
        start = TODAY
    d["tracker"] = tracker(dict(items=d["items"]), kyc_done=start, order=d)
    with _conn() as c:
        saved = c.execute("SELECT plan_json FROM decision_plans WHERE order_id=?", (d["id"],)).fetchone()
        if saved:
            d["plan"] = json.loads(saved[0])
            d["activation"] = activation.snapshot(c, d["id"], d["plan"].get("desired_start") or d["created_at"])
    return d


def _order_payload(order_id: str) -> dict:
    with _conn() as c:
        row = c.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Order not found")
    return _order_dict(row)


def _customer_order_payload(order: dict) -> dict:
    """Remove operator-only evidence from customer API responses."""
    payload = deepcopy(order)
    progress = payload.get("activation")
    if progress:
        progress["events"] = []
        for task in progress.get("tasks", []):
            task.pop("evidence", None)
            task.pop("source", None)
            task.pop("actor", None)
    return payload


def _require_admin(token: str | None, allowed_roles=STAFF_ROLES) -> Principal | None:
    principal = current_principal()
    if principal and principal.kind == "staff":
        if has_any_role(principal, allowed_roles):
            return principal
        raise HTTPException(403, "Your staff role cannot perform this action")
    # The shared token remains only as a local/staging compatibility mechanism.
    if not SETTINGS.production and ADMIN_TOKEN:
        if token and hmac.compare_digest(token, ADMIN_TOKEN):
            return None
        raise HTTPException(401, "Staff authentication required")
    if demo_mode() and not SETTINGS.production:
        return None
    if not SETTINGS.oidc_issuer:
        raise HTTPException(503, "OIDC staff authentication is not configured")
    raise HTTPException(401, "Staff authentication required")


def _require_order(order_id: str, token: str | None) -> None:
    principal = current_principal()
    if principal and principal.kind == "customer":
        with _conn() as c:
            row = c.execute("SELECT phone FROM order_customers WHERE order_id=?", (order_id,)).fetchone()
        if row and hmac.compare_digest(str(row["phone"]), principal.phone):
            return
        raise HTTPException(403, "This order does not belong to the signed-in customer")
    if SETTINGS.customer_auth_required:
        raise HTTPException(401, "Customer sign-in is required")
    with _conn() as c:
        row = c.execute("SELECT access_hash FROM decision_plans WHERE order_id=?", (order_id,)).fetchone()
    if row:
        if not token or not hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), row[0]):
            raise HTTPException(401, "This order's access token is required")
    elif not demo_mode():
        raise HTTPException(403, "Legacy orders require operator access outside demo mode")


# ---------------------------------------------------------------------------
# Explanation (deterministic by default, optional guarded LLM)
# ---------------------------------------------------------------------------

SEG_L10N = {
    "en": {
        "Micro retail & services": "a micro retail or services business",
        "Professional offices": "a professional office",
        "Small multi-site businesses": "a small multi-site business",
        "Small manufacturers": "a small manufacturer",
    },
    "hi": {
        "Micro retail & services": "एक छोटा रिटेल या सेवा व्यवसाय",
        "Professional offices": "एक प्रोफेशनल ऑफिस",
        "Small multi-site businesses": "एक छोटा मल्टी-साइट व्यवसाय",
        "Small manufacturers": "एक छोटा निर्माता",
    },
    "ta": {
        "Micro retail & services": "ஒரு சிறிய சில்லறை அல்லது சேவை வணிகம்",
        "Professional offices": "ஒரு தொழில்முறை அலுவலகம்",
        "Small multi-site businesses": "ஒரு சிறிய பல இட வணிகம்",
        "Small manufacturers": "ஒரு சிறிய உற்பத்தியாளர்",
    },
    "te": {
        "Micro retail & services": "ఒక చిన్న రిటైల్ లేదా సేవా వ్యాపారం",
        "Professional offices": "ఒక ప్రొఫెషనల్ కార్యాలయం",
        "Small multi-site businesses": "ఒక చిన్న బహుళ-సైట్ వ్యాపారం",
        "Small manufacturers": "ఒక చిన్న తయారీదారు",
    },
}

EXPLAIN_T = {
    "en": "For {seg} we suggest {names}: Rs {monthly:,} a month, Rs {setup:,} setup, {months}-month contract.",
    "hi": "{seg} के लिए हम {names} सुझाते हैं: ₹{monthly:,} प्रति माह, ₹{setup:,} सेटअप, {months} महीने का अनुबंध।",
    "ta": "{seg}க்கு {names} பரிந்துரைக்கிறோம்: மாதம் ₹{monthly:,}, அமைப்பு ₹{setup:,}, {months} மாத ஒப்பந்தம்.",
    "te": "{seg} కోసం {names} సిఫార్సు చేస్తున్నాము: నెలకు ₹{monthly:,}, సెటప్ ₹{setup:,}, {months} నెలల ఒప్పందం.",
}

# Voice output intentionally avoids reading English catalogue names through a
# Tamil/Telugu/Hindi voice. The visual quote keeps exact product names; this
# shorter native-language summary sounds natural and preserves every total.
SPEECH_T = {
    "en": (
        "The new services in your plan cost {monthly} rupees per month, "
        "with {setup} rupees setup. The longest new contract is {months} months."
    ),
    "hi": (
        "आपकी योजना की नई सेवाओं का मासिक शुल्क {monthly} रुपये है। "
        "सेटअप शुल्क {setup} रुपये है। सबसे लंबा नया अनुबंध {months} महीने का है।"
    ),
    "ta": (
        "உங்கள் திட்டத்தில் புதிய சேவைகளுக்கான மாத கட்டணம் {monthly} ரூபாய். "
        "அமைப்பு கட்டணம் {setup} ரூபாய். மிக நீண்ட புதிய ஒப்பந்த காலம் {months} மாதங்கள்."
    ),
    "te": (
        "మీ ప్లాన్‌లోని కొత్త సేవల నెలవారీ ఛార్జీ {monthly} రూపాయలు. "
        "సెటప్ ఛార్జీ {setup} రూపాయలు. కొత్త ఒప్పందాలలో అత్యధిక కాలం {months} నెలలు."
    ),
}

SPEECH_LANE_NOTE = {
    "en": " This bundle does not need a GSTIN. You can use Udyam based KYC.",
    "hi": " इस बंडल के लिए जी एस टी आई एन की जरूरत नहीं है। आप उद्यम आधारित के वाई सी कर सकते हैं।",
    "ta": " இந்த தொகுப்பிற்கு ஜி எஸ் டி எண் தேவையில்லை. உத்யம் அடிப்படையிலான கே ஒய் சி பயன்படுத்தலாம்.",
    "te": " ఈ బండిల్‌కు జి ఎస్ టి నంబర్ అవసరం లేదు. ఉద్యమ్ ఆధారిత కె వై సి ఉపయోగించవచ్చు.",
}

LANE_NOTE = {
    "GST-free": {
        "en": " This bundle needs no GSTIN, so you can start with Udyam-based KYC.",
        "hi": " इस बंडल के लिए GSTIN की जरूरत नहीं है, इसलिए आप उद्यम आधारित KYC से शुरू कर सकते हैं।",
        "ta": " இந்த தொகுப்பிற்கு GSTIN தேவையில்லை; உத்யம் அடிப்படையிலான KYC தொடங்கலாம்.",
        "te": " ఈ బండిల్‌కు GSTIN అవసరం లేదు; ఉద్యమ్ ఆధారిత KYCతో ప్రారంభించవచ్చు.",
    },
}


def _guard_explanation(text: str, q: dict) -> bool:
    """Reject an LLM explanation that adds numbers, free offers or products."""
    allowed = {str(x) for x in [q["monthly"], q["setup"], q["contract_months"]]}
    for i in q["items"]:
        allowed.update(str(x) for x in [i["qty"], i["unit"], i["monthly"], i["setup"], i["months"], i["days"]])
    nums = {n.replace(",", "") for n in re.findall(r"\d[\d,]*", text)}
    if not nums.issubset(allowed):
        return False
    low = text.lower()
    if any(w in low for w in ("free", "guarantee", "discount", "%", "cashback", "unlimited")):
        return False
    quoted = {str(i["name"]).lower() for i in q["items"]}
    for name in cat.product_name.astype(str):
        if name.lower() in low and name.lower() not in quoted:
            return False
    return True


def explain(seg: str, q: dict, lane: str, lang: str) -> str:
    lang = lang if lang in LANGS else "en"
    names = ", ".join(i["name"] for i in q["items"])
    base = EXPLAIN_T[lang].format(
        seg=SEG_L10N[lang].get(seg, seg),
        names=names,
        monthly=q["monthly"],
        setup=q["setup"],
        months=q["contract_months"],
    )
    base += LANE_NOTE.get(lane, {}).get(lang, "")
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return base
    facts = json.dumps(
        dict(
            segment=seg,
            items=[(i["name"], i["qty"], i["unit_type"], i["monthly"]) for i in q["items"]],
            monthly=q["monthly"],
            setup=q["setup"],
            months=q["contract_months"],
            lane=lane,
        )
    )
    try:
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            json.dumps(
                dict(
                    model=os.environ.get("ADVISOR_MODEL", "claude-sonnet-4-5"),
                    max_tokens=300,
                    messages=[
                        dict(
                            role="user",
                            content=(
                                f"Explain this bundle to a small-business owner in {LANGS[lang]}, "
                                "3 short plain sentences. Use ONLY these facts, add no product, "
                                f"price, discount or promise: {facts}"
                            ),
                        )
                    ],
                )
            ).encode(),
            {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        )
        txt = json.load(urllib.request.urlopen(req, timeout=8))["content"][0]["text"]
        return txt if _guard_explanation(txt, q) else base
    except Exception:
        return base


def speech_explanation(q: dict, lane: str, lang: str) -> str:
    lang = lang if lang in LANGS else "en"
    text = SPEECH_T[lang].format(
        monthly=f"{q['monthly']:,}",
        setup=f"{q['setup']:,}",
        months=q["contract_months"],
    )
    if lane == "GST-free":
        text += SPEECH_LANE_NOTE[lang]
    return text


def plan_speech_explanation(plan: dict, lane: str, lang: str) -> str:
    """Read the same decision as the screen, including retained costs and blockers."""
    lang = lang if lang in LANGS else "en"
    if not plan["ready"]:
        return {
            "en": "A few details need your review before this plan can be accepted. Check the open questions and budget on screen.",
            "hi": "इस योजना को स्वीकार करने से पहले कुछ जानकारी जांचनी है। स्क्रीन पर दिए सवाल और बजट देखें।",
            "ta": "இந்தத் திட்டத்தை ஏற்கும் முன் சில விவரங்களைச் சரிபார்க்க வேண்டும். திரையில் உள்ள கேள்விகளையும் வரவுசெலவுத் திட்டத்தையும் பார்க்கவும்.",
            "te": "ఈ ప్లాన్‌ను అంగీకరించే ముందు కొన్ని వివరాలను పరిశీలించాలి. తెరపై ఉన్న ప్రశ్నలు, బడ్జెట్‌ను చూడండి.",
        }[lang]
    if plan["nothing_to_buy"]:
        return {
            "en": "Your existing services cover the needs you reviewed. There is nothing new to buy. Existing provider bills continue separately.",
            "hi": "आपकी मौजूदा सेवाएं जांची गई जरूरतें पूरी करती हैं। नई सेवा खरीदने की जरूरत नहीं है। मौजूदा प्रदाताओं के बिल अलग से जारी रहेंगे।",
            "ta": "ஏற்கனவே உள்ள சேவைகள் நீங்கள் சரிபார்த்த தேவைகளைப் பூர்த்தி செய்கின்றன. புதிதாக எதையும் வாங்கத் தேவையில்லை. தற்போதைய வழங்குநர்களின் கட்டணங்கள் தனியாகத் தொடரும்.",
            "te": "ప్రస్తుత సేవలు మీరు పరిశీలించిన అవసరాలను తీరుస్తున్నాయి. కొత్తగా కొనాల్సిన అవసరం లేదు. ప్రస్తుత సేవా ప్రదాతల బిల్లులు విడిగా కొనసాగుతాయి.",
        }[lang]
    if not plan["quote"]["items"]:
        return {
            "en": "There is no new purchase now. Some improvements are deferred until the contracts end. Review the dates on screen.",
            "hi": "अभी नई खरीद नहीं है। कुछ सुधार अनुबंध समाप्त होने तक टाले गए हैं। स्क्रीन पर तारीखें देखें।",
            "ta": "இப்போது புதிய கொள்முதல் இல்லை. சில மேம்பாடுகள் ஒப்பந்தம் முடியும் வரை ஒத்திவைக்கப்பட்டுள்ளன. திரையில் தேதிகளைப் பார்க்கவும்.",
            "te": "ఇప్పుడు కొత్త కొనుగోలు లేదు. కొన్ని మెరుగుదలలు ఒప్పందాలు ముగిసే వరకు వాయిదా పడ్డాయి. తెరపై తేదీలను చూడండి.",
        }[lang]
    text = speech_explanation(plan["quote"], lane, lang)
    if not plan["costs"]["retained_cost_complete"]:
        return text + {
            "en": " Some existing service costs are missing, so the combined monthly total is not yet known. Review those bills separately.",
            "hi": " कुछ मौजूदा सेवाओं के खर्च उपलब्ध नहीं हैं, इसलिए पूरा मासिक खर्च अभी ज्ञात नहीं है। उन बिलों को अलग से देखें।",
            "ta": " சில தற்போதைய சேவைகளின் செலவுகள் தெரியவில்லை. அதனால் மொத்த மாத செலவை இன்னும் கணக்கிட முடியாது. அந்த பில்களைத் தனியாகப் பார்க்கவும்.",
            "te": " కొన్ని ప్రస్తుత సేవల ఖర్చులు తెలియవు. అందువల్ల మొత్తం నెలవారీ ఖర్చు ఇంకా తెలియదు. ఆ బిల్లులను విడిగా పరిశీలించండి.",
        }[lang]
    total = f"{plan['costs']['total_monthly']:,}"
    text += {
        "en": f" Including the existing services you recorded, the combined monthly cost is {total} rupees. Existing provider bills remain separate.",
        "hi": f" आपकी दर्ज की गई मौजूदा सेवाओं सहित कुल मासिक खर्च {total} रुपये है। मौजूदा प्रदाताओं के बिल अलग रहेंगे।",
        "ta": f" நீங்கள் பதிவு செய்த தற்போதைய சேவைகளுடன் சேர்த்து மாத செலவு {total} ரூபாய். தற்போதைய வழங்குநர்களின் பில்கள் தனியாக இருக்கும்.",
        "te": f" మీరు నమోదు చేసిన ప్రస్తుత సేవలతో కలిపి మొత్తం నెలవారీ ఖర్చు {total} రూపాయలు. ప్రస్తుత సేవా ప్రదాతల బిల్లులు విడిగానే ఉంటాయి.",
    }[lang]
    return text


# ---------------------------------------------------------------------------
# Propensity model (real Random Forest, with a transparent fallback)
# ---------------------------------------------------------------------------

def _train_model():
    global _MODEL, _MODEL_AUC
    if _MODEL is not None:
        return _MODEL, _MODEL_AUC
    try:
        from sklearn.compose import ColumnTransformer
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.impute import SimpleImputer
        from sklearn.metrics import roc_auc_score
        from sklearn.model_selection import train_test_split
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import OneHotEncoder

        df = LEADS.copy()
        df["target"] = (df.outcome == "Activated").astype(int)
        # Exclude stage_reached and drop_reason: they are outcome-derived and
        # would leak the label (Activated leads have no drop reason).
        df = df[df.stage_reached.isin(["Needs assessed", "Quote accepted", "KYC complete", "Activated"])].copy()
        num = ["employee_count", "num_sites", "city_tier", "monthly_telecom_spend_inr", "customer_follow_up_calls"]
        catf = ["segment", "has_gstin", "lead_source", "current_provider", "business_type", "state", "preferred_language"]
        X, y = df[num + catf], df["target"]
        pre = ColumnTransformer(
            [
                ("num", SimpleImputer(strategy="median"), num),
                ("cat", Pipeline([("imp", SimpleImputer(strategy="most_frequent")), ("oh", OneHotEncoder(handle_unknown="ignore", min_frequency=8))]), catf),
            ]
        )
        model = Pipeline(
            [
                ("pre", pre),
                ("rf", RandomForestClassifier(n_estimators=140, max_depth=8, min_samples_leaf=8, random_state=7, n_jobs=-1, class_weight="balanced")),
            ]
        )
        Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.2, random_state=7, stratify=y)
        model.fit(Xtr, ytr)
        _MODEL_AUC = float(roc_auc_score(yte, model.predict_proba(Xte)[:, 1]))
        model.fit(X, y)
        _MODEL = (model, num + catf)
    except Exception:
        _MODEL = False
        _MODEL_AUC = None
    return _MODEL, _MODEL_AUC


def _fallback_score(row) -> float:
    seg = row.get("segment")
    sub = LEADS[(LEADS.segment == seg) & (LEADS.has_gstin == row.get("has_gstin"))]
    rate = float((sub.outcome == "Activated").mean()) if len(sub) else float((LEADS.outcome == "Activated").mean())
    return rate


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def home():
    return HTMLResponse(STATIC.read_text(encoding="utf-8"))


@app.get("/robots.txt", response_class=PlainTextResponse, include_in_schema=False)
def robots():
    return "User-agent: *\nAllow: /\nDisallow: /api/\n"


@app.post("/api/auth/customer/request-otp")
async def request_customer_otp(b: OTPStartRequest):
    phone = b.phone
    now = _utcnow()
    with _conn() as c:
        c.execute("DELETE FROM auth_otp_challenges WHERE expires_at<?", ((now - dt.timedelta(days=1)).isoformat(),))
        c.execute("DELETE FROM customer_sessions WHERE expires_at<?", ((now - dt.timedelta(days=7)).isoformat(),))
        recent = c.execute(
            "SELECT created_at FROM auth_otp_challenges WHERE phone=? ORDER BY created_at DESC LIMIT 1",
            (phone,),
        ).fetchone()
        if recent and (now - dt.datetime.fromisoformat(recent["created_at"])).total_seconds() < 60:
            raise HTTPException(429, "Wait before requesting another sign-in code")
    challenge_id = secrets.token_urlsafe(24)
    code = f"{secrets.randbelow(1_000_000):06d}"
    expires = now + dt.timedelta(minutes=5)
    with _conn() as c:
        c.execute(
            "INSERT INTO auth_otp_challenges (id,phone,code_hash,created_at,expires_at,attempts,consumed_at) VALUES (?,?,?,?,?,0,NULL)",
            (challenge_id, phone, _otp_hash(challenge_id, code), now.isoformat(), expires.isoformat()),
        )
    try:
        await _deliver_otp(phone, code)
    except HTTPException:
        with _conn() as c:
            c.execute("DELETE FROM auth_otp_challenges WHERE id=?", (challenge_id,))
        raise
    result = {"challenge_id": challenge_id, "expires_in": 300, "delivered": not demo_mode()}
    if demo_mode():
        result["debug_code"] = code
    return result


@app.post("/api/auth/customer/verify-otp")
def verify_customer_otp(b: OTPVerifyRequest):
    now = _utcnow()
    phone = ""
    with _conn() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.select_for_update("SELECT * FROM auth_otp_challenges WHERE id=?", (b.challenge_id,)).fetchone()
        if not row or row["consumed_at"] or dt.datetime.fromisoformat(row["expires_at"]) <= now:
            raise HTTPException(400, "Sign-in code is invalid or expired")
        if int(row["attempts"]) >= 5:
            raise HTTPException(429, "Too many incorrect attempts; request a new code")
        if not hmac.compare_digest(row["code_hash"], _otp_hash(b.challenge_id, b.code)):
            c.execute("UPDATE auth_otp_challenges SET attempts=attempts+1 WHERE id=?", (b.challenge_id,))
            raise HTTPException(400, "Sign-in code is invalid or expired")
        token = "uoc_" + secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode()).hexdigest()
        expires = now + dt.timedelta(hours=SETTINGS.otp_session_hours)
        c.execute("UPDATE auth_otp_challenges SET consumed_at=? WHERE id=?", (now.isoformat(), b.challenge_id))
        c.execute(
            "INSERT INTO customer_sessions (token_hash,phone,created_at,expires_at,revoked_at) VALUES (?,?,?,?,NULL)",
            (digest, row["phone"], now.isoformat(), expires.isoformat()),
        )
        phone = str(row["phone"])
    demo_order_id = _prepare_recording_demo(phone)
    return {"access_token": token, "token_type": "bearer", "expires_at": expires.isoformat(), "latest_order_id": demo_order_id}


@app.get("/api/auth/customer/session")
def customer_session():
    principal = current_principal()
    if not principal or principal.kind != "customer":
        raise HTTPException(401, "Customer sign-in is required")
    with _conn() as c:
        latest = c.execute(
            "SELECT oc.order_id FROM order_customers oc JOIN orders o ON o.id=oc.order_id WHERE oc.phone=? ORDER BY o.created_at DESC LIMIT 1",
            (principal.phone,),
        ).fetchone()
    return {"authenticated": True, "phone": principal.phone, "latest_order_id": latest["order_id"] if latest else None}


@app.post("/api/auth/customer/logout")
def customer_logout():
    principal = current_principal()
    if not principal or principal.kind != "customer":
        raise HTTPException(401, "Customer sign-in is required")
    with _conn() as c:
        c.execute(
            "UPDATE customer_sessions SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL",
            (_utcnow().isoformat(), principal.token_hash),
        )
    return {"logged_out": True}


@app.get("/api/live")
def live():
    return {"status": "ok"}


@app.get("/api/ready")
def ready():
    try:
        with _conn() as c:
            c.execute("SELECT 1").fetchone()
        if cat.empty or LEADS.empty:
            raise RuntimeError("Reference data is unavailable")
    except Exception:
        raise HTTPException(503, "Application dependencies are not ready")
    return {"status": "ready", "version": app.version}


@app.get("/api/health")
def health():
    try:
        import kyc

        ocr = kyc.backend()
    except Exception:
        ocr = "unavailable"
    try:
        import edge_tts  # noqa: F401

        tts = "microsoft-neural-online"
    except Exception:
        tts = "browser-fallback"
    model, auc = _train_model() if SETTINGS.enable_synthetic_model else (None, None)
    result = dict(
        status="ok",
        version=app.version,
        ocr=ocr,
        tts=tts,
        products=len(cat),
        leads=len(LEADS),
        orders=_count_orders(),
        model_auc=round(auc, 3) if auc else None,
        anchor_date=str(TODAY),
    )
    if SETTINGS.production:
        return {k: result[k] for k in ("status", "version", "ocr", "tts")}
    return result


@app.get("/api/voices")
def voices():
    return dict(
        provider="Microsoft neural voices via edge-tts",
        network_required=True,
        browser_fallback=True,
        voices=TTS_VOICES,
    )


@app.get("/api/operator/session")
def operator_session(response: Response, token: str | None = Header(None, alias="x-admin-token")):
    """Check staff access without fetching customer records or creating a session."""
    principal = _require_admin(token)
    response.headers["Cache-Control"] = "no-store"
    method = "oidc" if principal else "staff_token" if ADMIN_TOKEN else "local_demo"
    return dict(authorized=True, authentication=method, roles=sorted(principal.roles) if principal else [], partner_code=principal.partner_code if principal else "")


async def _generate_tts(text: str, voice: str, rate: str) -> bytes:
    try:
        import edge_tts
    except Exception:
        raise HTTPException(503, "Neural voice package is not installed")

    async def collect() -> bytes:
        audio = bytearray()
        communicate = edge_tts.Communicate(text=text, voice=voice, rate=rate)
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio":
                audio.extend(chunk["data"])
        return bytes(audio)

    try:
        data = await asyncio.wait_for(collect(), timeout=20)
    except asyncio.TimeoutError:
        raise HTTPException(504, "Neural voice service timed out")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, "Neural voice service is temporarily unavailable")
    if not data:
        raise HTTPException(503, "Neural voice service returned no audio")
    return data


@app.post("/api/tts")
async def tts(b: TTSRequest):
    text = re.sub(r"\s+", " ", b.text).strip()
    voice = TTS_VOICES[b.lang][b.gender]
    key = (text, b.lang, b.gender)
    data = _TTS_CACHE.get(key)
    if data is None:
        data = await _generate_tts(text, voice, TTS_RATES[b.lang])
        _TTS_CACHE[key] = data
        _TTS_CACHE.move_to_end(key)
        while len(_TTS_CACHE) > _TTS_CACHE_MAX:
            _TTS_CACHE.popitem(last=False)
    else:
        _TTS_CACHE.move_to_end(key)
    return Response(
        data,
        media_type="audio/mpeg",
        headers={
            "Cache-Control": "no-store",
            "X-TTS-Voice": voice,
            "X-Content-Type-Options": "nosniff",
        },
    )


def _count_orders() -> int:
    with _conn() as c:
        return int(c.execute("SELECT COUNT(*) FROM orders").fetchone()[0])


def _recommend_payload(p: dict, off: list[str], lang: str) -> dict:
    if p.get("context"):
        plan = make_plan(p)
        q = plan["quote"]
        return dict(segment=plan["segment"], lane=_lane_for(p), quote=q, plan=plan, notes=plan["clarify"], tracker=tracker(q), explanation=("Your existing services cover the reviewed needs. Nothing new to buy." if plan["nothing_to_buy"] else f"Keep adequate services and add the missing capabilities. New monthly charges: INR {q['monthly']:,}. Review retained costs and any open questions below."), speech_text=plan_speech_explanation(plan, _lane_for(p), lang), needs=p.get("needs", []))
    seg, pick, notes = recommend(p)
    q = quote(pick, seg, off, p)
    lane = _lane_for(p)
    for item in q["items"]:
        item["why"] = str(cat.loc[item["id"]].description)
    if p.get("current_spend"):
        try:
            spend = int(p["current_spend"])
            q["current_spend"] = spend
            q["monthly_delta"] = q["monthly"] - spend
            # Current total spend is not evidence that the new bundle replaces it.
        except (TypeError, ValueError):
            pass
    if not q["items"]:
        notes.append("All items were removed. Re-enable at least one product.")
    return dict(
        segment=seg,
        lane=lane,
        quote=q,
        notes=notes,
        tracker=tracker(q),
        explanation=explain(seg, q, lane, lang),
        speech_text=speech_explanation(q, lane, lang),
        profile={k: p.get(k) for k in ("tier", "employees", "sites", "gstin_active", "type")},
        needs=p.get("needs", []),
    )


@app.post("/api/recommend")
def rec(b: RecommendRequest):
    p = b.as_engine_profile()
    p["needs"] = b.needs
    return _recommend_payload(p, b.off, b.lang)


@app.post("/api/intent")
def intent(b: IntentRequest):
    parsed = parse_intent(b.text)
    profile = parsed["profile"]
    if not parsed["missing"]:
        p = dict(
            tier=int(profile.get("tier", 1)),
            employees=int(profile.get("employees", 1)),
            sites=int(profile.get("sites", 1)),
            gstin_active=bool(profile.get("gstin", False)),
            type=str(profile.get("type", "")),
            needs=parsed["needs"],
        )
        payload = _recommend_payload(p, [], b.lang)
        payload["parsed"] = parsed
        return payload
    return dict(parsed=parsed, missing=parsed["missing"], needs=parsed["needs"])


@app.post("/api/orders")
def create_order(b: OrderRequest):
    if not b.consent:
        raise HTTPException(400, "Consent is required before creating an order")
    p = b.profile.as_engine_profile()
    principal = current_principal()
    if SETTINGS.customer_auth_required:
        if not principal or principal.kind != "customer":
            raise HTTPException(401, "Customer sign-in is required before accepting an order")
        if not hmac.compare_digest(principal.phone, b.clean_phone()):
            raise HTTPException(403, "The order phone must match the signed-in customer")
    plan = make_plan(p) if p.get("context") else None
    if plan:
        if not plan["ready"]:
            raise HTTPException(409, "Resolve the plan's open questions before accepting")
        if b.plan_fingerprint != plan["fingerprint"]:
            raise HTTPException(409, "The plan changed; review and accept the refreshed quote")
        seg, q = plan["segment"], plan["quote"]
    else:
        if not demo_mode():
            raise HTTPException(409, "Review locations and existing services before accepting a plan")
        seg, pick, notes = recommend(p)
        q = quote(pick, seg, b.off, p)
    if not q["items"]:
        raise HTTPException(400, "The selected bundle is empty")
    lane = _lane_for(p)
    oid = "UO-" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d") + "-" + secrets.token_hex(16).upper()
    partner = re.sub(r"[^A-Za-z0-9-]", "", b.partner_code or "")[:24].upper()
    if partner:
        valid_referral = bool(
            b.partner_exp
            and b.partner_exp >= int(_utcnow().timestamp())
            and b.partner_exp <= int((_utcnow() + dt.timedelta(days=31)).timestamp())
            and b.partner_sig
            and hmac.compare_digest(b.partner_sig, _partner_signature(partner, b.partner_exp))
        )
        if not valid_referral and not demo_mode():
            raise HTTPException(400, "Partner referral is invalid or expired")
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    access_token = secrets.token_urlsafe(32)
    with _conn() as c:
        c.execute(
            "INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                oid, now, b.business_name.strip(), b.contact_name.strip(), b.clean_phone(),
                b.email.strip(), b.address.strip(), b.language, seg, lane,
                json.dumps(q["items"], ensure_ascii=False), q["monthly"], q["setup"],
                q["contract_months"], "quote_accepted", "pending", None, partner, 1, "plan-v3" if plan else "v1",
                json.dumps(p, ensure_ascii=False),
            ),
        )
        c.execute("INSERT INTO decision_plans VALUES (?,?,?,?)", (oid, json.dumps(plan or {"quote": q, "legacy": True}), hashlib.sha256(access_token.encode()).hexdigest(), now))
        c.execute("INSERT INTO order_customers (order_id,phone) VALUES (?,?)", (oid, b.clean_phone()))
        activation.seed(c, oid, q["items"])
        _record_consent("Order creation and contact", "order-v1", oid, lane, "accepted", connection=c)
    result = _customer_order_payload(_order_payload(oid))
    if not SETTINGS.customer_auth_required:
        result["access_token"] = access_token
    return result


@app.get("/api/orders")
def list_orders(limit: int = Query(20, ge=1, le=100), token: str | None = Header(None, alias="x-admin-token")):
    _require_admin(token, ORDER_READ_ROLES)
    with _conn() as c:
        rows = c.execute("SELECT * FROM orders ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [_order_dict(r) for r in rows]


@app.get("/api/orders/{order_id}")
def get_order(order_id: str, token: str | None = Header(None, alias="x-order-token")):
    _require_order(order_id, token)
    return _customer_order_payload(_order_payload(order_id))


@app.get("/api/operator/orders/{order_id}")
def operator_order(order_id: str, token: str | None = Header(None, alias="x-admin-token")):
    _require_admin(token, ORDER_READ_ROLES)
    return _order_payload(order_id)


@app.post("/api/orders/{order_id}/status")
def set_status(order_id: str, b: StatusRequest, token: str | None = Header(None, alias="x-admin-token")):
    _require_admin(token, ACTIVATION_WRITE_ROLES)
    allowed = ("placed", "quote_accepted", "kyc_pending", "kyc_complete", "install_scheduled", "activated", "cancelled")
    if b.status not in allowed:
        raise HTTPException(400, f"status must be one of {allowed}")
    order = _order_payload(order_id)
    if order.get("activation") and b.status != "cancelled":
        raise HTTPException(409, "Status is derived from activation evidence; use task events")
    with _conn() as c:
        cur = c.execute("UPDATE orders SET status=? WHERE id=?", (b.status, order_id))
        if cur.rowcount == 0:
            raise HTTPException(404, "Order not found")
    return _order_payload(order_id)


@app.post("/api/kyc/extract-udyam")
async def extract_udyam_number(
    consent: str = Form(...),
    order_id: str = Form(""),
    udyam: UploadFile = File(...),
    token: str | None = Header(None, alias="x-order-token"),
    admin_token: str | None = Header(None, alias="x-admin-token"),
    staff_request: str | None = Header(None, alias="x-staff-request"),
):
    """Consent-gated OCR prefill; the image and raw OCR text are not retained."""
    if consent != "true":
        raise HTTPException(400, "Consent is required before reading the Udyam certificate")
    if not SETTINGS.allow_mock_kyc:
        raise HTTPException(503, "Production document-verification provider is not configured")
    if order_id:
        principal = current_principal()
        if (principal and principal.kind == "staff") or admin_token or (demo_mode() and staff_request == "true"):
            _require_admin(admin_token, KYC_WRITE_ROLES)
        else:
            _require_order(order_id, token)
    if not udyam.filename:
        raise HTTPException(400, "Choose a Udyam certificate image")
    if udyam.content_type and not udyam.content_type.startswith("image/"):
        raise HTTPException(400, "Udyam certificate must be an image (JPG/PNG)")
    data = await udyam.read(8 * 1024 * 1024 + 1)
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(400, "Udyam certificate is larger than 8 MB")
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in {"PNG", "JPEG", "WEBP"}:
                raise HTTPException(415, "Udyam certificate must be PNG, JPEG or WebP")
            if image.width * image.height > 25_000_000:
                raise HTTPException(413, "Udyam certificate has too many pixels")
            image.verify()
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, "Udyam certificate is not a valid image")
    try:
        import kyc
    except Exception as exc:
        raise HTTPException(503, f"KYC engine unavailable: {exc}")
    if kyc.backend() == "none":
        raise HTTPException(503, "KYC engine unavailable: install RapidOCR or Tesseract")
    folder = tempfile.mkdtemp()
    path = f"{folder}/udyam_certificate.png"
    try:
        with open(path, "wb") as output:
            output.write(data)
        try:
            await asyncio.wait_for(_OCR_SEMAPHORE.acquire(), timeout=2)
        except asyncio.TimeoutError:
            raise HTTPException(503, "Document processing is busy; try again shortly")
        try:
            number = await asyncio.to_thread(kyc.extract_udyam_number, path)
        finally:
            _OCR_SEMAPHORE.release()
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    _record_consent("Udyam certificate number prefill", "udyam-prefill-v1", order_id or None, outcome="extracted" if number else "not_found")
    return {
        "udyam_number": number,
        "ocr": kyc.backend(),
        "message": "Udyam number filled from the certificate" if number else "The Udyam number could not be read; enter it manually",
    }


@app.post("/api/kyc")
async def kyc_api(
    consent: str = Form(...),
    lane: str = Form("standard"),
    order_id: str = Form(""),
    udyam_number: str = Form(""),
    gst: UploadFile | None = File(None),
    sid: UploadFile | None = File(None),
    addr: UploadFile | None = File(None),
    udyam: UploadFile | None = File(None),
    token: str | None = Header(None, alias="x-order-token"),
    admin_token: str | None = Header(None, alias="x-admin-token"),
    staff_request: str | None = Header(None, alias="x-staff-request"),
):
    if consent != "true":
        raise HTTPException(400, "Consent is required before KYC")
    if not SETTINGS.allow_mock_kyc:
        raise HTTPException(503, "Production document-verification provider is not configured")
    lane = lane if lane in ("standard", "gst_free") else "standard"
    if lane == "gst_free":
        if gst is not None and gst.filename:
            raise HTTPException(400, "The GST-free lane must not upload a GST certificate")
    try:
        import kyc
    except Exception as exc:
        raise HTTPException(503, f"KYC engine unavailable: {exc}")
    if kyc.backend() == "none":
        raise HTTPException(503, "KYC engine unavailable: install RapidOCR or Tesseract")
    if order_id:
        principal = current_principal()
        if (principal and principal.kind == "staff") or admin_token or (demo_mode() and staff_request == "true"):
            _require_admin(admin_token, KYC_WRITE_ROLES)
        else:
            _require_order(order_id, token)
        bound_order = _order_payload(order_id)
        if bound_order["status"] == "cancelled":
            raise HTTPException(409, "This order is cancelled")
        if lane != ("standard" if bound_order["lane"] == "Standard" else "gst_free"):
            raise HTTPException(409, "KYC lane must match the accepted order")
        with _conn() as c:
            existing_verification = c.execute("SELECT data_json FROM activation_tasks WHERE order_id=? AND id='verification'", (order_id,)).fetchone()
        if existing_verification and json.loads(existing_verification[0])["status"] == "complete":
            raise HTTPException(409, "Verification is already recorded; use an operator review for identity changes, or a new order after demo-only verification")
    elif not demo_mode():
        _require_admin(None, KYC_WRITE_ROLES)
    t = tempfile.mkdtemp()
    try:
        files = [(gst, "gst_certificate"), (sid, "signatory_id"), (addr, "address_proof"), (udyam, "udyam_certificate")]
        for f, name in files:
            if f is None or not f.filename:
                continue
            if f.content_type and not f.content_type.startswith("image/"):
                raise HTTPException(400, f"{name} must be an image (JPG/PNG)")
            data = await f.read(8 * 1024 * 1024 + 1)
            if len(data) > 8 * 1024 * 1024:
                raise HTTPException(400, f"{name} is larger than 8 MB")
            try:
                from PIL import Image
                with Image.open(io.BytesIO(data)) as image:
                    if image.format not in {"PNG", "JPEG", "WEBP"}:
                        raise HTTPException(415, f"{name} must be PNG, JPEG or WebP")
                    if image.width * image.height > 25_000_000:
                        raise HTTPException(413, f"{name} has too many pixels")
                    image.verify()
            except HTTPException:
                raise
            except Exception:
                raise HTTPException(400, f"{name} is not a valid image")
            with open(f"{t}/U_{name}.png", "wb") as out:
                out.write(data)
        try:
            try:
                await asyncio.wait_for(_OCR_SEMAPHORE.acquire(), timeout=2)
            except asyncio.TimeoutError:
                raise HTTPException(503, "Document processing is busy; try again shortly")
            try:
                _, verdict, findings = await asyncio.to_thread(
                    kyc.check, t, "U", lane, udyam_number
                )
                if order_id:
                    binding = await asyncio.to_thread(kyc.bind_order, t, "U", bound_order, udyam_number)
                    findings.extend(binding)
                    if binding and verdict == "VERIFIED":
                        verdict = "ACTION_REQUIRED"
            finally:
                _OCR_SEMAPHORE.release()
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(400, "One or more documents could not be read as images")
    finally:
        shutil.rmtree(t, ignore_errors=True)  # images are not retained after extraction
    record = dict(
        ts=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        purpose="KYC verification",
        notice="v1",
        lane=lane,
        order_id=order_id or None,
        verdict=verdict,
        codes=[f["code"] for f in findings],
    )
    _record_consent("KYC verification", "kyc-v1", order_id or None, lane, verdict, record["codes"])
    if not SETTINGS.production:
        CONSENT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with CONSENT_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    if order_id:
        with _conn() as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute(
                """UPDATE orders SET kyc_status=?, kyc_verdict=?,
                   status=CASE
                     WHEN status IN ('activated','install_scheduled') THEN status
                     WHEN ?='VERIFIED' THEN 'kyc_complete'
                     ELSE 'kyc_pending'
                   END WHERE id=?""",
                ("verified" if verdict == "VERIFIED" else "action_required" if verdict == "ACTION_REQUIRED" else "rejected", verdict, verdict, order_id),
            )
            task = c.execute("SELECT data_json FROM activation_tasks WHERE order_id=? AND id='verification'", (order_id,)).fetchone()
            if verdict == "VERIFIED" and task and json.loads(task[0])["status"] != "complete":
                activation.record(c, order_id, activation.TaskEvent(event_id="kyc-"+secrets.token_hex(12), task_id="verification", action="complete", expected_version=json.loads(task[0])["version"], evidence="Mock registry and OCR checks matched the accepted order identity, signatory and address."), "mock_registry_bound")
    return dict(verdict=verdict, findings=findings, lane=lane, ocr=kyc.backend(), source="mock_registry", scope="Synthetic local KYC; not live government verification")


@app.get("/api/backoffice")
def bo(
    limit: int = Query(25, ge=1, le=100),
    segment: str | None = None,
    token: str | None = Header(None, alias="x-admin-token"),
):
    _require_admin(token, frozenset({*ANALYTICS_ROLES, SALES}))
    if not SETTINGS.enable_synthetic_model:
        raise HTTPException(503, "Production lead model is not configured or approved")
    x = LEADS[(LEADS.outcome == "Dropped") & LEADS.stage_reached.isin(["Needs assessed", "Quote accepted", "KYC complete"])].copy()
    if segment:
        x = x[x.segment == segment]
    model, auc = _train_model()
    if model:
        pipe, cols = model
        try:
            x["prob"] = pipe.predict_proba(x[cols])[:, 1]
        except Exception:
            x["prob"] = x.apply(lambda r: _fallback_score(r), axis=1)
    else:
        x["prob"] = x.apply(lambda r: _fallback_score(r), axis=1)
    x["expected_value"] = x["prob"] * x["segment"].map(SEG_MEDIAN).fillna(0)
    x = x.sort_values(["expected_value", "prob"], ascending=False).head(limit)
    leads = [
        dict(
            id=r.lead_id,
            name=r.business_name,
            segment=r.segment,
            city=r.city,
            stage=r.stage_reached,
            reason=r.drop_reason,
            score=round(float(r.prob) * 100, 1),
            expected_value=round(float(r.expected_value)),
            action=ACT.get(r.drop_reason, "Call back"),
            routing=route(str(r.drop_reason)),
            monthly_value=float(SEG_MEDIAN.get(r.segment, 0)),
        )
        for r in x.itertuples()
    ]
    return dict(leads=leads, model_auc=round(auc, 3) if auc else None, count=len(leads), segments=list(SEGMENTS), disclosure="Propensity is not the causal effect of assistance. Routes reflect blockers; historical contact consent must be reconfirmed before outreach.")


@app.get("/api/partner/qr")
def partner_qr(code: str, request: Request):
    if SETTINGS.production:
        raise HTTPException(404, "Use the authenticated partner referral endpoint")
    code = re.sub(r"[^A-Za-z0-9-]", "", code)[:24].upper()
    if not code:
        raise HTTPException(400, "code is required")
    try:
        import qrcode

        img = qrcode.make(str(request.base_url).rstrip("/") + f"/?partner={code}")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return Response(buf.getvalue(), media_type="image/png")
    except Exception as exc:
        raise HTTPException(501, f"QR generation unavailable: {exc}")


@app.get("/api/partner/referral")
def partner_referral(
    request: Request,
    code: str = "",
    token: str | None = Header(None, alias="x-admin-token"),
):
    principal = _require_admin(token, frozenset({ADMIN, MANAGER, PARTNER}))
    if principal and PARTNER in principal.roles:
        code = principal.partner_code
    code = re.sub(r"[^A-Za-z0-9-]", "", code)[:24].upper()
    if not code:
        raise HTTPException(400, "Partner code is required")
    expires = int((_utcnow() + dt.timedelta(days=30)).timestamp())
    signature = _partner_signature(code, expires)
    query = urllib.parse.urlencode({"partner": code, "partner_exp": expires, "partner_sig": signature})
    url = str(request.base_url).rstrip("/") + "/?" + query
    try:
        import qrcode
        image = qrcode.make(url)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
    except Exception as exc:
        raise HTTPException(501, f"QR generation unavailable: {exc}")
    return {
        "code": code,
        "expires_at": dt.datetime.fromtimestamp(expires, dt.timezone.utc).isoformat(),
        "url": url,
        "qr_data_url": "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode(),
    }


@app.get("/api/partner/summary")
def partner_summary(code: str, token: str | None = Header(None, alias="x-admin-token")):
    principal = _require_admin(token, frozenset({*ANALYTICS_ROLES, PARTNER}))
    if principal and PARTNER in principal.roles:
        if code and code.upper() != principal.partner_code:
            raise HTTPException(403, "Partners can view only their own attribution")
        code = principal.partner_code
    code = re.sub(r"[^A-Za-z0-9-]", "", code)[:24].upper()
    with _conn() as c:
        rows = c.execute("SELECT * FROM orders WHERE partner_code=?", (code,)).fetchall()
    orders = [_order_dict(r) for r in rows]
    monthly = sum(o["monthly"] for o in orders)
    return dict(
        code=code,
        orders=len(orders),
        monthly_value=monthly,
        month1_commission=monthly,
        annual_trail=round(monthly * 0.08 * 12),
        leads_count=len(LEADS[LEADS.lead_source.str.contains("Partner", case=False, na=False)]),
    )


@app.post("/api/bills/extract")
async def extract_bill(consent: str = Form(...), bill: UploadFile = File(...)):
    if SETTINGS.customer_auth_required:
        principal = current_principal()
        if not principal or principal.kind != "customer":
            raise HTTPException(401, "Customer sign-in is required")
    if consent != "true":
        raise HTTPException(400, "Consent is required to read the bill")
    content = await bill.read(8 * 1024 * 1024 + 1)
    if len(content) > 8 * 1024 * 1024:
        raise HTTPException(413, "Bill must be smaller than 8 MB")
    if bill.content_type not in ("image/png", "image/jpeg", "image/webp"):
        raise HTTPException(415, "Use a PNG, JPEG or WebP bill image")
    import kyc
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "bill.png"
        path.write_bytes(content)
        try:
            lines, _, _ = await asyncio.to_thread(kyc.ocr, str(path))
        except Exception:
            raise HTTPException(400, "Bill could not be read; enter the service details manually")
    draft = bill_draft("\n".join(lines))
    _record_consent("Bill OCR for existing-service review", "bill-ocr-v1", outcome="processed")
    # Only extracted candidate fields leave this request; raw OCR is not stored.
    return draft


@app.post("/api/orders/{order_id}/events")
def activation_event(order_id: str, event: activation.TaskEvent, token: str | None = Header(None, alias="x-admin-token")):
    principal = _require_admin(token, ACTIVATION_WRITE_ROLES)
    order = _order_payload(order_id)
    if order["status"] == "cancelled":
        raise HTTPException(409, "This order is cancelled")
    source = "simulation" if demo_mode() else "operator_evidence"
    try:
        with _conn() as c:
            c.execute("BEGIN IMMEDIATE")
            activation.record(c, order_id, event, source, principal.subject if principal else None)
            progress = activation.snapshot(c, order_id, order["created_at"])
            state = "activated" if progress["complete"] else "install_scheduled" if any(t["status"] == "complete" and t["kind"] == "provision" for t in progress["tasks"]) else order["status"]
            c.execute("UPDATE orders SET status=? WHERE id=?", (state, order_id))
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    return _order_payload(order_id)


@app.post("/api/orders/{order_id}/demo-verification")
def demo_verification(order_id: str, token: str | None = Header(None, alias="x-admin-token")):
    _require_admin(token, ACTIVATION_WRITE_ROLES)
    if not demo_mode():
        raise HTTPException(403, "Demo verification is disabled")
    order = _order_payload(order_id)
    if order["status"] == "cancelled":
        raise HTTPException(409, "This order is cancelled")
    with _conn() as c:
        c.execute("BEGIN IMMEDIATE")
        task = c.execute("SELECT data_json FROM activation_tasks WHERE order_id=? AND id='verification'", (order_id,)).fetchone()
        if not task:
            raise HTTPException(409, "This legacy order has no task plan")
        data = json.loads(task[0])
        if data["status"] != "complete":
            activation.record(c, order_id, activation.TaskEvent(event_id="demo-"+secrets.token_hex(12), task_id="verification", action="complete", expected_version=data["version"], evidence="Explicit demo simulation. No documents checked; not valid KYC."), "demo_only")
            c.execute("UPDATE orders SET kyc_status='simulation_only',status='kyc_complete' WHERE id=?", (order_id,))
    return _order_payload(order_id)


@app.post("/api/assistance/simulate")
def assistance_simulator(b: Simulation, token: str | None = Header(None, alias="x-admin-token")):
    _require_admin(token, ANALYTICS_ROLES)
    return simulate(bo(limit=5000, segment=None, token=token)["leads"], b)


def _recovery_candidate(order: dict, allow_wireless: bool) -> tuple[dict, dict]:
    if not order.get("plan", {}).get("context"):
        raise HTTPException(409, "Recovery planning requires an accepted needs plan")
    p = json.loads(json.dumps(order["profile"]))
    failed_sites = {t["site_id"] for t in order["activation"]["tasks"] if t["status"] == "failed" and t["product_id"] in ("P01", "P02", "P03", "P05", "P06")}
    if not failed_sites:
        raise HTTPException(409, "No failed wired-connection branch needs replanning")
    for location in p["context"]["locations"]:
        if location["id"] in failed_sites:
            location["unavailable_products"] = ["P01", "P02", "P03", "P05", "P06"]
            if allow_wireless:
                location["needs"] = ["internet" if n in ("fibre", "leased_line") else n for n in location["needs"]]
    candidate = make_plan(p)
    return p, candidate


@app.post("/api/orders/{order_id}/recovery")
def recovery_preview(order_id: str, b: RecoveryRequest, token: str | None = Header(None, alias="x-order-token")):
    _require_order(order_id, token)
    order = _order_payload(order_id)
    if order["status"] == "cancelled":
        raise HTTPException(409, "This order is cancelled")
    _, candidate = _recovery_candidate(order, b.allow_wireless)
    return dict(plan=candidate, base_fingerprint=order["plan"]["fingerprint"], monthly_change=candidate["quote"]["monthly"]-order["monthly"], setup_change=candidate["quote"]["setup"]-order["setup"], applied=False, notice="This candidate treats wired connectivity at the failed site as unavailable. Allowing wireless changes the connection requirement, including any dedicated-line expectations. It still needs site feasibility. SKU dependencies remain enforced. Nothing changes until you accept.")


@app.post("/api/orders/{order_id}/recovery/accept")
def accept_recovery(order_id: str, b: RecoveryRequest, token: str | None = Header(None, alias="x-order-token")):
    _require_order(order_id, token)
    if not b.consent:
        raise HTTPException(400, "Explicit revised-plan acceptance is required")
    order = _order_payload(order_id)
    p, candidate = _recovery_candidate(order, b.allow_wireless)
    if not candidate["ready"] or not candidate["quote"]["items"]:
        raise HTTPException(409, "The recovery candidate has unresolved requirements")
    if b.candidate_fingerprint != candidate["fingerprint"]:
        raise HTTPException(409, "Recovery candidate changed; review it again")
    old_items = {(i.get("site_id"), i["id"]):i for i in order["items"]}
    new_items = {(i.get("site_id"), i["id"]):i for i in candidate["quote"]["items"]}
    changed = {key for key in old_items if key not in new_items or old_items[key] != new_items[key]}
    with _conn() as c:
        c.execute("BEGIN IMMEDIATE")
        current = c.select_for_update("SELECT plan_json FROM decision_plans WHERE order_id=?", (order_id,)).fetchone()
        if json.loads(current[0]).get("fingerprint") != b.base_fingerprint:
            raise HTTPException(409, "Accepted plan changed; refresh the recovery preview")
        if c.execute("SELECT status FROM orders WHERE id=?", (order_id,)).fetchone()[0] == "cancelled":
            raise HTTPException(409, "This order is cancelled")
        if c.execute("SELECT 1 FROM payment_intents WHERE order_id=?", (order_id,)).fetchone():
            raise HTTPException(409, "A payment link exists; operator billing review is required before changing the accepted amount")
        tasks = [json.loads(r[0]) for r in c.execute("SELECT data_json FROM activation_tasks WHERE order_id=?", (order_id,))]
        if any(t["status"] == "complete" and t["kind"] in ("provision", "check") and (t["site_id"],t["product_id"]) in changed for t in tasks):
            raise HTTPException(409, "An affected service is already provisioned; operator migration review is required")
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        c.execute("INSERT INTO plan_revisions VALUES (?,?,?,?,?)", (order_id,b.base_fingerprint,current[0],json.dumps(tasks),now))
        c.execute("UPDATE decision_plans SET plan_json=?,accepted_at=? WHERE order_id=?", (json.dumps(candidate),now,order_id))
        q = candidate["quote"]
        c.execute("UPDATE orders SET items_json=?,monthly=?,setup=?,contract_months=?,profile_json=? WHERE id=?", (json.dumps(q["items"]),q["monthly"],q["setup"],q["contract_months"],json.dumps(p),order_id))
        activation.replace_tasks(c, order_id, q["items"])
        _record_consent("Revised recovery plan acceptance", "recovery-v1", order_id, outcome="accepted", connection=c)
    return _customer_order_payload(_order_payload(order_id))


@app.get("/api/assistance/orders")
def order_assistance(token: str | None = Header(None, alias="x-admin-token")):
    _require_admin(token, frozenset({*ORDER_READ_ROLES, MANAGER, SALES}))
    orders = list_orders(limit=100, token=token)
    rows = []
    for order in orders:
        progress = order.get("activation", {})
        tasks = progress.get("tasks", [])
        reason = "Installation failed" if any(t["status"] == "failed" for t in tasks) else "Installation delayed" if any(t["status"] == "delayed" for t in tasks) else "Missing document" if order["kyc_status"] in ("pending", "action_required", "rejected") else "Self-service progressing"
        rows.append(dict(id=order["id"], name=order["business_name"], status=order["status"], partner=order["partner_code"], reason=reason, routing=route(reason, order["consent"])))
    return dict(orders=rows, note="Routing recommendations only; no messages or visits are dispatched.")


from integrations import register as register_integrations
register_integrations(app, _conn, _order_payload, _require_order, _require_admin, _record_consent)
