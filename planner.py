"""Evidence-aware needs planning. Catalogue rules, not an LLM, control purchases."""
from __future__ import annotations

import datetime as dt
import hashlib
import itertools
import json
import math
import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from engine import NEED_PRODUCTS, NEED_ALIASES, _price_point, cat, reasons, segment

CAPABILITIES = {**NEED_PRODUCTS, "internet": ["P01", "P02", "P03", "P04", "P05", "P06"], "mobile": ["P07", "P08"]}
NETWORK = {"internet", "fibre", "leased_line"}
SHARED_NEEDS = {"backup", "website", "whatsapp", "tollfree", "sms"}


class Location(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,32}$")
    name: str = Field(min_length=1, max_length=80)
    tier: int = Field(default=1, ge=1, le=2)
    employees: int = Field(default=1, ge=1, le=10000)
    needs: list[str] = Field(default_factory=list, max_length=24)
    quantities: dict[str, int] = Field(default_factory=dict)
    unavailable_products: list[str] = Field(default_factory=list, max_length=25)

    @model_validator(mode="after")
    def quantities_valid(self):
        if any(k not in CAPABILITIES or not 1 <= v <= 10000 for k, v in self.quantities.items()):
            raise ValueError("Quantities must use a supported need and be between 1 and 10,000")
        if self.id == "business":
            raise ValueError("'business' is reserved for shared services")
        if any(pid not in cat.index for pid in self.unavailable_products):
            raise ValueError("Unknown unavailable product")
        return self


class OwnedService(BaseModel):
    site_id: str = Field(max_length=32)
    capability: str = Field(max_length=40)
    provider: str = Field(default="", max_length=80)
    quantity: int = Field(default=1, ge=1, le=10000)
    adequacy: Literal["adequate", "inadequate", "unknown"] = "unknown"
    monthly: int | None = Field(default=None, ge=0, le=10000000)
    contract_end: dt.date | None = None
    switching_cost: int = Field(default=0, ge=0, le=10000000)
    source: Literal["customer_reviewed", "bill_reviewed"] = "customer_reviewed"

    @model_validator(mode="after")
    def capability_valid(self):
        if self.capability not in CAPABILITIES:
            raise ValueError("Unsupported existing service capability")
        return self


class BusinessContext(BaseModel):
    locations: list[Location] = Field(min_length=1, max_length=100)
    owned: list[OwnedService] = Field(default_factory=list, max_length=300)
    inventory_confirmed: bool = False
    budget: int | None = Field(default=None, ge=0, le=10000000)
    desired_start: dt.date | None = None
    wait_for_contract: bool = False

    @model_validator(mode="after")
    def references_valid(self):
        ids = [s.id for s in self.locations]
        if len(ids) != len(set(ids)):
            raise ValueError("Location IDs must be unique")
        if any(o.site_id not in ids + ["business"] for o in self.owned):
            raise ValueError("Existing service references an unknown location")
        for owned in self.owned:
            if (owned.site_id == "business") != (owned.capability in SHARED_NEEDS):
                raise ValueError("Use Whole business for shared account/number/pack services; assign all other existing units to a specific location")
        return self


def canonical(raw: str) -> str:
    raw = raw.lower().strip()
    return raw if raw in CAPABILITIES else NEED_ALIASES.get(raw, raw)


def _quantity(pid: str, need: str, location: dict) -> int:
    if need in location.get("quantities", {}):
        return location["quantities"][need]
    unit = str(cat.loc[pid].unit)
    if "per user" in unit or pid == "P20":
        return max(1, min(10, math.ceil(location["employees"] / 4)))
    if "per camera" in unit:
        return 2
    if pid == "P24":
        return max(1, min(5, math.ceil(location["employees"] / 20)))
    return 1


def _dependencies(pid: str) -> list[str]:
    value = cat.loc[pid].requires_one_of
    return value.split("|") if isinstance(value, str) else []


def _cost(selection: dict, seg: str) -> int:
    return sum((_price_point(pid, seg) * 12 + int(cat.loc[pid].one_time_setup_inr)) * qty for pid, qty in selection.items())


def make_plan(profile: dict, today: dt.date | None = None) -> dict:
    """Minimise catalogue 12-month new cost across feasible need choices.

    Incumbent capabilities can meet a need, but cannot satisfy a SKU-specific
    catalogue dependency. That compatibility must be resolved before acceptance.
    """
    ctx = BusinessContext.model_validate(profile["context"]).model_dump(mode="json")
    today = today or dt.date.today()
    start = dt.date.fromisoformat(ctx["desired_start"]) if ctx["desired_start"] else today
    seg = segment(profile["employees"], len(ctx["locations"]), profile.get("type", ""))
    keep, later, clarify, items, deferred = [], [], [], [], []
    if not ctx["inventory_confirmed"]:
        clarify.append("Review the existing-service inventory, including locations with no services.")
    for owned in ctx["owned"]:
        entry = dict(owned, evidence=owned["source"], reason="Keep the existing service; no cancellation is assumed.")
        keep.append(entry)
        if owned["adequacy"] == "unknown":
            clarify.append(f"{owned['site_id']}: confirm whether existing {owned['capability']} meets the requirement.")
        if owned["adequacy"] == "inadequate":
            later.append(dict(owned, reason="Review replacement at contract expiry; migration and cancellation require a new accepted quote.", review_on=owned["contract_end"] or str(today)))
    shared = {}
    for loc in ctx["locations"]:
        lp = dict(profile, tier=loc["tier"], sites=len(ctx["locations"]))
        owned = [s for s in ctx["owned"] if s["site_id"] in (loc["id"], "business")]
        need_options = []
        why = {}
        for need in dict.fromkeys(canonical(n) for n in loc["needs"]):
            if need not in CAPABILITIES:
                clarify.append(f"{loc['name']}: clarify unsupported need '{need}'.")
                continue
            matching = [s for s in owned if s["capability"] == need or (need == "internet" and s["capability"] in NETWORK)]
            if any(s["adequacy"] == "unknown" for s in matching):
                continue
            if ctx["wait_for_contract"] and any(s["adequacy"] == "inadequate" and s["contract_end"] and dt.date.fromisoformat(s["contract_end"]) > start for s in matching):
                deferred.append(dict(site_id=loc["id"], need=need, reason="Customer agreed to wait for the existing contract to expire; this need is not met now."))
                continue
            options = []
            for pid in CAPABILITIES[need]:
                wanted = _quantity(pid, need, loc)
                available = sum(s["quantity"] for s in matching if s["adequacy"] == "adequate")
                qty = max(0, wanted - available)
                if qty and (reasons(pid, lp) or pid in loc["unavailable_products"]):
                    continue
                options.append({pid: qty} if qty else {})
                why[pid] = f"{need}: {wanted} required, {available} adequate existing units; add only the gap."
            if not options:
                detail = "; ".join(sorted(set(r for p in CAPABILITIES[need] for r in reasons(p, lp)))) or "requested products are unavailable at this location"
                clarify.append(f"{loc['name']}: {need} cannot be fulfilled ({detail}).")
            else:
                need_options.append(options)
        choices = []
        for combination in itertools.product(*need_options):
            selection = {}
            for option in combination:
                for pid, qty in option.items():
                    selection[pid] = max(selection.get(pid, 0), qty)
            candidates = [selection]
            # All current catalogue dependencies are on base connectivity.
            for pid in list(selection):
                req = _dependencies(pid)
                expanded = []
                for candidate in candidates:
                    if not req or set(req).intersection(candidate):
                        expanded.append(candidate)
                    elif any(s["capability"] in NETWORK and s["adequacy"] == "adequate" for s in owned):
                        # Do not duplicate an adequate incumbent merely to sell an add-on.
                        continue
                    else:
                        for dependency in req:
                            if not reasons(dependency, lp) and dependency not in loc["unavailable_products"]:
                                expanded.append({**candidate, dependency: 1})
                candidates = expanded
            choices.extend(candidates)
        if not choices:
            clarify.append(f"{loc['name']}: catalogue add-ons require compatible provider connectivity. Existing third-party internet is not proof of compatibility; review the dependency before buying.")
            continue
        chosen = min(choices, key=lambda s: (_cost(s, seg), sorted(s)))
        for pid, qty in sorted(chosen.items()):
            row = cat.loc[pid]
            is_shared = str(row.unit) in ("per account", "per number", "per pack")
            site_id = "business" if is_shared else loc["id"]
            entry = dict(id=pid, site_id=site_id, site_name="Whole business" if is_shared else loc["name"], name=str(row.product_name), category=str(row.category), description=str(row.description), unit_type=str(row.unit), qty=qty, unit=_price_point(pid, seg), setup=int(row.one_time_setup_inr)*qty, months=int(row.min_contract_months), days=int(row.typical_install_days), core=False, addon=False, why=why.get(pid, "Required by the selected product's catalogue dependency."))
            entry["monthly"] = entry["unit"] * qty
            if is_shared:
                if pid not in shared or shared[pid]["qty"] < qty:
                    shared[pid] = entry
            else:
                items.append(entry)
    items.extend(shared.values())
    monthly = sum(i["monthly"] for i in items)
    setup = sum(i["setup"] for i in items)
    retained = sum(s["monthly"] or 0 for s in ctx["owned"])
    unknown_cost = any(s["monthly"] is None for s in ctx["owned"])
    total = monthly + retained
    budget = ctx["budget"]
    budget_state = "not_set" if budget is None else "unknown" if unknown_cost else "within_budget" if total <= budget else "over_budget"
    if budget_state == "over_budget":
        clarify.append(f"Monthly budget is short by INR {total-budget:,}; no essential requirement was silently removed.")
    if budget_state == "unknown":
        clarify.append("Existing service costs are missing, so budget feasibility cannot be confirmed.")
    clarify = list(dict.fromkeys(clarify))
    q = dict(items=items, monthly=monthly, setup=setup, contract_months=max([i["months"] for i in items] or [0]), core_monthly=0, addon_monthly=monthly, currency="INR")
    plan = dict(version=1, as_of=str(today), desired_start=str(max(start, today)), segment=seg, keep=keep, replace_later=later, clarify=clarify, quote=q, costs=dict(new_monthly=monthly, retained_monthly=retained, retained_cost_complete=not unknown_cost, total_monthly=total, setup=setup, twelve_month_new=12*monthly+setup, budget=budget, budget_state=budget_state, switching_cost_exposure=sum(s["switching_cost"] for s in ctx["owned"])), ready=not clarify, nothing_to_buy=not items and not clarify, assumptions=["Synthetic Accel case catalogue; prices are not live Airtel offers and tax is not specified.", "Existing services are customer-reviewed, not provider-verified. Retained costs continue; no cancellation or savings is assumed.", "Shared account/number/pack products use one business subscription; user quantities default to one seat per four staff (cap ten) and CCTV to two cameras per location. Review quantities before acceptance.", "Selection minimises 12-month new catalogue charges among eligible candidates; it does not optimise unprovided bandwidth, SLA or contract requirements."], context=ctx)
    plan["deferred"] = deferred
    plan["nothing_to_buy"] = not items and not clarify and not deferred
    plan["fingerprint"] = hashlib.sha256(json.dumps({k:v for k,v in plan.items() if k != "fingerprint"}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return plan


def bill_draft(text: str) -> dict:
    """Conservative extraction. Never infer adequacy, exact SKU, or contract date."""
    services = []
    for capability, pattern in {"internet": r"broadband|fibre|fiber|internet", "mobile": r"postpaid|mobile", "email": r"email|workspace|office 365", "backup": r"cloud backup"}.items():
        match = re.search(pattern, text, re.I)
        if match:
            services.append(dict(capability=capability, evidence=match.group(0), adequacy="unknown", quantity=1))
    amount = re.search(r"(?:monthly (?:charge|rental)|recurring charge)\s*[:\-]?\s*(?:INR|Rs\.?|₹)?\s*([\d,]+(?:\.\d{1,2})?)", text, re.I)
    provider = next((p for p in ("Airtel", "Jio", "Vi", "BSNL", "ACT", "Tata") if re.search(r"\b"+p+r"\b", text, re.I)), "")
    return dict(services=services, provider=provider, monthly=round(float(amount.group(1).replace(",", ""))) if amount else None, review_required=True, note="Review every extracted fact. Amount due, taxes and arrears are not treated as monthly rental; contract expiry and service adequacy remain unknown.")
