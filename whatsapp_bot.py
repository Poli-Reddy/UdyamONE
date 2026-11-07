"""Small, deterministic English WhatsApp advisor using the web planner."""
from __future__ import annotations

import re

from planner import CAPABILITIES, SHARED_NEEDS, canonical, make_plan

QUESTIONS = {
    "type": "What kind of business do you run? (e.g. clinic, retail, manufacturer)",
    "employees": "How many employees? Reply with a number from 1 to 10000.",
    "sites": "How many business locations? This WhatsApp pilot supports one; use the website for multiple locations.",
    "tier": "City tier? Reply 1 or 2.",
    "gstin": "Do you have an active GSTIN? Reply YES or NO. This is a declaration, not verification.",
    "needs": "Which services do you need? Comma-separated: internet, mobile, email, backup, pos, cctv, wifi, security, website, whatsapp.",
    "inventory": "Existing services? Reply NONE, or capability, monthly rupees, quantity (example: email, 500, 5). Only list a service if it meets your needs. Use the website for multiple services or contract review.",
    "budget": "Total monthly budget including existing services? Reply a number, or SKIP.",
}
STEPS = list(QUESTIONS)


def respond(state: dict, message: str) -> tuple[dict, str]:
    text = message.strip()[:1500]
    command = text.upper()
    if command == "STOP":
        return {}, "Stopped. Your advisor session has been cleared. Send START if you want to return."
    if command == "HELP":
        return state, "Send START for the advisor, TRACK <order ID> for your order, or STOP to clear this chat session. No order or payment is created in chat."
    if command == "START" or not state:
        return {"step": "consent"}, "UdyamOne advisor (English pilot). May we use this chat to assess business needs and reply? Reply YES to consent, or STOP. This is a synthetic catalogue demonstration, not a live Airtel offer."
    step = state.get("step", "consent")
    if step == "consent":
        if command != "YES":
            return state, "Reply YES to the purpose above, or STOP."
        return {"step": "type", "profile": {}, "owned": [], "consent": True}, QUESTIONS["type"]
    if step == "done":
        return state, "Send START to review another business, TRACK <order ID> for status, or STOP. Complete acceptance and KYC on the website."
    if step not in STEPS:
        return {}, "Send START to begin."
    state = {**state, "profile": dict(state.get("profile", {}))}
    p = state["profile"]
    if step == "type":
        if len(text) < 2:
            return state, QUESTIONS[step]
        p[step] = text[:120]
    elif step in ("employees", "sites", "tier"):
        maximum = {"employees": 10000, "sites": 1, "tier": 2}[step]
        if not text.isdigit() or not 1 <= int(text) <= maximum:
            return state, QUESTIONS[step]
        p[step] = int(text)
    elif step == "gstin":
        if command not in ("YES", "NO"):
            return state, QUESTIONS[step]
        p["gstin_active"] = command == "YES"
    elif step == "needs":
        needs = list(dict.fromkeys(canonical(t) for t in text.split(",")))
        if not needs or any(n not in CAPABILITIES for n in needs):
            return state, QUESTIONS[step]
        p["needs"] = needs
    elif step == "inventory":
        if command == "NONE":
            state["owned"] = []
        else:
            parts = [s.strip() for s in text.split(",")]
            if len(parts) != 3 or canonical(parts[0]) not in CAPABILITIES or not parts[1].isdigit() or not parts[2].isdigit() or not 0 <= int(parts[1]) <= 10000000 or not 1 <= int(parts[2]) <= 10000:
                return state, QUESTIONS[step]
            state["owned"] = [dict(site_id="business" if canonical(parts[0]) in SHARED_NEEDS else "site-1", capability=canonical(parts[0]), monthly=int(parts[1]), quantity=int(parts[2]), adequacy="adequate")]
    elif step == "budget":
        if command != "SKIP" and (not text.isdigit() or not 0 <= int(text) <= 10000000):
            return state, QUESTIONS[step]
        p["context"] = dict(locations=[dict(id="site-1", name="Main location", tier=p["tier"], employees=p["employees"], needs=p["needs"])], owned=state["owned"], inventory_confirmed=True, budget=None if command == "SKIP" else int(text))
        plan = make_plan(p)
        state["step"] = "done"
        if plan["clarify"]:
            return state, "Review needed: " + " ".join(plan["clarify"]) + " Continue on the website; no order was placed."
        products = ", ".join(f"{i['qty']} × {i['name']}" for i in plan["quote"]["items"]) or "Nothing new to buy"
        cost = plan["costs"]
        return state, f"Keep {len(plan['keep'])} existing service(s). Add: {products}. New INR {cost['new_monthly']}/month + INR {cost['setup']} setup. Retained INR {cost['retained_monthly']}/month. Total INR {cost['total_monthly']}/month. Synthetic catalogue; tax unspecified. Review quantities and contracts on the website before accepting. No order or payment was created."
    next_step = STEPS[STEPS.index(step)+1]
    state["step"] = next_step
    return state, QUESTIONS[next_step]


def normalize_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    return "91" + digits if len(digits) == 10 else digits
