"""HTTP boundaries, durable payment reconciliation and signed inbound WhatsApp."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import secrets
import sqlite3
import time
from typing import Literal

from fastapi import Header, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

import providers
from config import demo_mode
from security import ANALYTICS_ROLES, BILLING_ROLES, KYC_WRITE_ROLES, current_principal
from whatsapp_bot import normalize_phone, respond


class RegistryRequest(BaseModel):
    kind: Literal["gstin", "udyam"]
    number: str = Field(min_length=10, max_length=25)
    consent: bool = False


class ReconcileRequest(BaseModel):
    provider_id: str = ""


class PreviewRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1500)
    state: dict = Field(default_factory=dict)


class OutcomeRequest(BaseModel):
    cost: int = Field(ge=0, le=1000000)
    outcome: Literal["pending", "declined", "activated", "retained_90d", "churned"]
    note: str = Field(default="", max_length=500)


async def bounded_body(request: Request):
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 1024*1024:
            raise HTTPException(413, "Webhook payload is too large")
    return bytes(body)


def register(app, conn, order_payload, require_order, require_admin, record_consent=None):
    with conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS payment_intents (
            order_id TEXT PRIMARY KEY, reference TEXT UNIQUE NOT NULL, amount INTEGER NOT NULL,
            currency TEXT NOT NULL, provider_id TEXT, url TEXT, status TEXT NOT NULL, mode TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS provider_events (id TEXT PRIMARY KEY, digest TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS registry_evidence (
            order_id TEXT NOT NULL, kind TEXT NOT NULL, result_json TEXT NOT NULL,
            created_at TEXT NOT NULL, PRIMARY KEY(order_id,kind)
        );
        CREATE TABLE IF NOT EXISTS whatsapp_sessions (phone TEXT PRIMARY KEY, state_json TEXT NOT NULL, updated_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS whatsapp_receipts (id TEXT PRIMARY KEY, status TEXT NOT NULL, created_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS interventions (
            order_id TEXT PRIMARY KEY, assignment TEXT NOT NULL, skill TEXT NOT NULL, created_at TEXT NOT NULL,
            cost INTEGER NOT NULL DEFAULT 0, outcome TEXT NOT NULL DEFAULT 'pending', note TEXT NOT NULL DEFAULT ''
        );
        """)

    @app.get("/api/integrations")
    def integration_status():
        return dict(providers=providers.status(), demo=demo_mode())

    @app.post("/api/orders/{order_id}/registry")
    async def registry(
        order_id: str,
        b: RegistryRequest,
        token: str | None = Header(None, alias="x-order-token"),
        admin_token: str | None = Header(None, alias="x-admin-token"),
        staff_request: str | None = Header(None, alias="x-staff-request"),
    ):
        principal = current_principal()
        if (principal and principal.kind == "staff") or admin_token or (demo_mode() and staff_request == "true"):
            require_admin(admin_token, KYC_WRITE_ROLES)
        else:
            require_order(order_id, token)
        order = order_payload(order_id)
        if not b.consent:
            raise HTTPException(400, "Consent to send this registration number and order business name to Cashfree is required")
        if order["status"] == "cancelled":
            raise HTTPException(409, "This order is cancelled")
        number = b.number.strip().upper()
        pattern = r"\d{2}[A-Z]{5}\d{4}[A-Z][A-Z0-9]Z[A-Z0-9]" if b.kind == "gstin" else r"UDYAM-[A-Z]{2}-\d{2}-\d{7}"
        if not re.fullmatch(pattern, number):
            raise HTTPException(422, "Registration number format is invalid")
        result = await providers.verify_registry(b.kind, number, order["business_name"], "uo_"+secrets.token_hex(12))
        result["consent"] = "registry-lookup-v1"
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        with conn() as c:
            c.execute(
                """INSERT INTO registry_evidence (order_id,kind,result_json,created_at) VALUES (?,?,?,?)
                   ON CONFLICT(order_id,kind) DO UPDATE SET result_json=excluded.result_json,created_at=excluded.created_at""",
                (order_id, b.kind, json.dumps(result), now),
            )
            if record_consent:
                record_consent("External registry lookup", "registry-lookup-v1", order_id, outcome=result["outcome"], connection=c)
        return dict(result, recorded_at=now, valid_until=(dt.datetime.now(dt.timezone.utc)+dt.timedelta(days=1)).isoformat(), full_kyc_complete=False)

    @app.get("/api/orders/{order_id}/registry")
    def registry_history(
        order_id: str,
        token: str | None = Header(None, alias="x-order-token"),
        admin_token: str | None = Header(None, alias="x-admin-token"),
        staff_request: str | None = Header(None, alias="x-staff-request"),
    ):
        principal = current_principal()
        if (principal and principal.kind == "staff") or admin_token or (demo_mode() and staff_request == "true"):
            require_admin(admin_token, KYC_WRITE_ROLES)
        else:
            require_order(order_id, token)
        with conn() as c:
            rows = c.execute("SELECT result_json,created_at FROM registry_evidence WHERE order_id=?", (order_id,)).fetchall()
        return [dict(json.loads(r[0]), recorded_at=r[1]) for r in rows]

    @app.get("/api/orders/{order_id}/payment")
    def payment_status(order_id: str, token: str | None = Header(None, alias="x-order-token")):
        require_order(order_id, token)
        with conn() as c:
            row = c.execute("SELECT * FROM payment_intents WHERE order_id=?", (order_id,)).fetchone()
        return dict(row) if row else dict(status="not_created", mode="test")

    @app.post("/api/orders/{order_id}/payment-link")
    async def create_link(order_id: str, token: str | None = Header(None, alias="x-order-token")):
        require_order(order_id, token)
        order = order_payload(order_id)
        if order["status"] == "cancelled":
            raise HTTPException(409, "This order is cancelled")
        providers.payment_config()
        reference = "uo_" + secrets.token_hex(14)
        with conn() as c:
            c.execute("BEGIN IMMEDIATE")
            c.select_for_update("SELECT id FROM orders WHERE id=?", (order_id,)).fetchone()
            old = c.execute("SELECT * FROM payment_intents WHERE order_id=?", (order_id,)).fetchone()
            if old:
                if old["status"] in ("creating", "needs_reconciliation"):
                    raise HTTPException(409, "Link creation is pending or uncertain; reconcile it before retrying")
                return dict(old)
            c.execute("INSERT INTO payment_intents VALUES (?,?,?,?,NULL,NULL,'creating','test')", (order_id, reference, (order["monthly"]+order["setup"])*100, "INR"))
        try:
            result = await providers.payment_link(order, reference)
        except HTTPException:
            with conn() as c:
                c.execute("UPDATE payment_intents SET status='needs_reconciliation' WHERE order_id=? AND status='creating'", (order_id,))
            raise
        with conn() as c:
            c.execute("UPDATE payment_intents SET provider_id=?,url=?,status=CASE WHEN status='paid' THEN 'paid' ELSE 'ready' END WHERE order_id=?", (result["provider_id"], result["url"], order_id))
        return payment_status(order_id, token)

    def reconcile_entity(c, link, pay=None):
        row = c.execute("SELECT * FROM payment_intents WHERE reference=?", (link.get("reference_id"),)).fetchone()
        if not row:
            raise HTTPException(409, "Unknown payment reference")
        if link.get("amount") != row["amount"] or link.get("currency") != row["currency"] or not re.fullmatch(r"plink_[A-Za-z0-9]+", link.get("id", "")) or (row["provider_id"] and row["provider_id"] != link.get("id")):
            raise HTTPException(409, "Payment does not match the accepted order amount, currency or link")
        status = link.get("status")
        if status == "paid":
            if link.get("amount_paid") != row["amount"]:
                raise HTTPException(409, "Payment total is inconsistent")
            if pay is not None and (pay.get("amount") != row["amount"] or pay.get("currency") != row["currency"] or pay.get("captured") is not True or pay.get("status") != "captured" or not pay.get("order_id") or pay.get("order_id") != link.get("order_id")):
                raise HTTPException(409, "Captured payment details are inconsistent")
        if status in ("paid", "cancelled", "expired") and (row["status"] != "paid" or status == "paid"):
            c.execute("UPDATE payment_intents SET provider_id=?,status=? WHERE order_id=?", (link["id"], status, row["order_id"]))
        elif status == "created" and row["status"] != "paid":
            url = link.get("short_url", "")
            if not re.match(r"^https://rzp\.io/", url):
                raise HTTPException(409, "Invalid hosted payment URL")
            c.execute("UPDATE payment_intents SET provider_id=?,url=?,status='ready' WHERE order_id=?", (link["id"], url, row["order_id"]))
        return row["order_id"]

    @app.post("/api/orders/{order_id}/payment/reconcile")
    async def reconcile(order_id: str, b: ReconcileRequest, token: str | None = Header(None, alias="x-admin-token")):
        require_admin(token, BILLING_ROLES)
        with conn() as c:
            row = c.execute("SELECT * FROM payment_intents WHERE order_id=?", (order_id,)).fetchone()
        if not row:
            raise HTTPException(404, "No payment intent exists")
        provider_id = row["provider_id"] or b.provider_id
        if not provider_id:
            raise HTTPException(409, "Find the link by the saved reference in Razorpay, then supply its provider_id")
        link = await providers.fetch_payment_link(provider_id)
        if link.get("reference_id") != row["reference"]:
            raise HTTPException(409, "This provider link belongs to another order")
        with conn() as c:
            c.execute("BEGIN IMMEDIATE")
            reconcile_entity(c, link)
            result = dict(c.execute("SELECT * FROM payment_intents WHERE order_id=?", (order_id,)).fetchone())
        return result

    @app.post("/api/webhooks/razorpay")
    async def razorpay_webhook(request: Request):
        cfg = providers.payment_config()
        raw = await bounded_body(request)
        providers.check_signature(raw, request.headers.get("x-razorpay-signature"), cfg["WEBHOOK_SECRET"])
        try:
            body = json.loads(raw)
            event = body["event"]
        except (ValueError, KeyError, TypeError):
            raise HTTPException(400, "Invalid payment event")
        if event not in ("payment_link.paid", "payment_link.cancelled", "payment_link.expired"):
            return {"ignored": True}
        digest = hashlib.sha256(raw).hexdigest()
        event_id = "razorpay:" + request.headers.get("x-razorpay-event-id", digest)[:200]
        with conn() as c:
            c.execute("BEGIN IMMEDIATE")
            old = c.execute("SELECT digest FROM provider_events WHERE id=?", (event_id,)).fetchone()
            if old:
                if old[0] != digest:
                    raise HTTPException(409, "Event ID conflict")
                return {"duplicate": True}
            try:
                link = body["payload"]["payment_link"]["entity"]
                pay = body["payload"]["payment"]["entity"] if event == "payment_link.paid" else None
                if link.get("status") != event.split(".")[1]:
                    raise HTTPException(409, "Event type and payment status disagree")
                reconcile_entity(c, link, pay)
            except (KeyError, TypeError, AttributeError):
                raise HTTPException(400, "Incomplete payment event")
            c.execute("INSERT INTO provider_events VALUES (?,?,?)", (event_id, digest, dt.datetime.now(dt.timezone.utc).isoformat()))
        return {"received": True, "mode": "test"}

    @app.post("/api/whatsapp/preview")
    def preview(b: PreviewRequest):
        if not demo_mode():
            raise HTTPException(403, "Preview is disabled outside demo mode")
        try:
            state, reply = respond(b.state, b.message)
        except (ValueError, KeyError, TypeError):
            raise HTTPException(422, "Invalid preview state; send START with an empty state")
        return dict(state=state, reply=reply, mode="local_preview", sent=False)

    @app.get("/api/webhooks/whatsapp", response_class=PlainTextResponse)
    def whatsapp_challenge(request: Request):
        cfg = providers.settings("WHATSAPP_", ("VERIFY_TOKEN",))
        q = request.query_params
        if q.get("hub.mode") != "subscribe" or not secrets.compare_digest(q.get("hub.verify_token", ""), cfg["VERIFY_TOKEN"]):
            raise HTTPException(403, "Invalid webhook verification token")
        return q.get("hub.challenge", "")

    @app.post("/api/webhooks/whatsapp")
    async def whatsapp_webhook(request: Request):
        cfg = providers.settings("WHATSAPP_", ("APP_SECRET", "PHONE_NUMBER_ID", "TOKEN", "GRAPH_VERSION"))
        raw = await bounded_body(request)
        providers.check_signature(raw, request.headers.get("x-hub-signature-256"), cfg["APP_SECRET"], "sha256=")
        try:
            body = json.loads(raw)
            if body.get("object") != "whatsapp_business_account":
                raise ValueError()
            incoming = []
            for entry in body.get("entry", []):
                for change in entry.get("changes", []):
                    value = change.get("value", {})
                    if value.get("metadata", {}).get("phone_number_id") == cfg["PHONE_NUMBER_ID"]:
                        incoming.extend(value.get("messages", []))
            if len(incoming) > 20:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(400, "Invalid WhatsApp webhook")
        for message in incoming:
            try:
                phone, mid = message["from"], message["id"]
                timestamp = int(message["timestamp"])
                if not re.fullmatch(r"\d{8,15}", phone) or not isinstance(mid, str) or len(mid) > 300 or not -300 <= int(time.time())-timestamp < 86400:
                    continue
            except (KeyError, ValueError, TypeError):
                continue
            # One durable claim per inbound message. Ambiguous send failures are
            # recorded, never blindly resent (Meta has no send idempotency key).
            with conn() as c:
                c.execute("BEGIN IMMEDIATE")
                if c.execute("SELECT 1 FROM whatsapp_receipts WHERE id=?", (mid,)).fetchone():
                    continue
                c.execute("DELETE FROM whatsapp_sessions WHERE updated_at<?", (int(time.time())-86400,))
                row = c.execute("SELECT state_json FROM whatsapp_sessions WHERE phone=?", (phone,)).fetchone()
                state = json.loads(row[0]) if row else {}
                text = message.get("text", {}).get("body", "") if message.get("type") == "text" else "HELP"
                if not isinstance(text, str):
                    text = "HELP"
                if text.upper().startswith("TRACK ") and state.get("consent"):
                    oid = text[6:].strip()[:100]
                    order = c.execute("SELECT id,phone,status FROM orders WHERE id=?", (oid,)).fetchone()
                    reply = f"Order {oid}: {order['status']}. Use the secure web tracker for task details." if order and normalize_phone(order["phone"]) == normalize_phone(phone) else "No matching order for this WhatsApp number. Check the ID or use your secure web tracker."
                else:
                    state, reply = respond(state, text)
                if state:
                    c.execute(
                        """INSERT INTO whatsapp_sessions (phone,state_json,updated_at) VALUES (?,?,?)
                           ON CONFLICT(phone) DO UPDATE SET state_json=excluded.state_json,updated_at=excluded.updated_at""",
                        (phone, json.dumps(state), int(time.time())),
                    )
                else:
                    c.execute("DELETE FROM whatsapp_sessions WHERE phone=?", (phone,))
                c.execute("INSERT INTO whatsapp_receipts VALUES (?,'sending',?)", (mid, int(time.time())))
            try:
                await providers.send_whatsapp(phone, reply)
                result = "sent"
            except HTTPException:
                result = "send_unknown_no_auto_retry"
            with conn() as c:
                c.execute("UPDATE whatsapp_receipts SET status=? WHERE id=?", (result, mid))
        return {"received": True}

    @app.post("/api/assistance/{order_id}/assign")
    def assign(order_id: str, token: str | None = Header(None, alias="x-admin-token")):
        require_admin(token, ANALYTICS_ROLES)
        order = order_payload(order_id)
        if not order["consent"] or order["status"] in ("activated", "cancelled"):
            raise HTTPException(409, "Order is not eligible for the assistance pilot")
        tasks = order.get("activation", {}).get("tasks", [])
        skill = "installation" if any(t["status"] in ("failed", "delayed") for t in tasks) else "verification" if order["kyc_status"] in ("pending", "action_required", "rejected") else "advisor"
        with conn() as c:
            c.execute(
                """INSERT INTO interventions (order_id,assignment,skill,created_at) VALUES (?,?,?,?)
                   ON CONFLICT(order_id) DO NOTHING""",
                (order_id, secrets.choice(["assistance", "self_service"]), skill, dt.datetime.now(dt.timezone.utc).isoformat()),
            )
            result = dict(c.execute("SELECT * FROM interventions WHERE order_id=?", (order_id,)).fetchone())
        return dict(result, dispatched=False, note="Randomised pilot assignment only; confirm research/contact consent and available staff before delivery.")

    @app.post("/api/assistance/{order_id}/outcome")
    def outcome(order_id: str, b: OutcomeRequest, token: str | None = Header(None, alias="x-admin-token")):
        require_admin(token, ANALYTICS_ROLES)
        order = order_payload(order_id)
        if b.outcome == "activated" and order["status"] != "activated":
            raise HTTPException(409, "Activation has not been recorded for this order")
        with conn() as c:
            row = c.execute("SELECT created_at FROM interventions WHERE order_id=?", (order_id,)).fetchone()
            if not row:
                raise HTTPException(404, "Assign the pilot arm before recording an outcome")
            if b.outcome == "retained_90d" and (dt.datetime.now(dt.timezone.utc)-dt.datetime.fromisoformat(row[0])).days < 90:
                raise HTTPException(409, "The 90-day observation window has not elapsed")
            c.execute("UPDATE interventions SET cost=?,outcome=?,note=? WHERE order_id=?", (b.cost, b.outcome, b.note, order_id))
        return dict(recorded=True, source="operator_reported")

    @app.get("/api/assistance/pilot/results")
    def results(token: str | None = Header(None, alias="x-admin-token")):
        require_admin(token, ANALYTICS_ROLES)
        with conn() as c:
            rows = [dict(r) for r in c.execute("SELECT * FROM interventions ORDER BY created_at DESC LIMIT 100")]
        return dict(records=rows, note="Observed operator records; no causal estimate is claimed from this sample.")
