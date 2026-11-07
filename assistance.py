"""Blocker-based routing and explicit-assumption policy comparison, not learned uplift."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


def route(reason: str, consent: bool = True) -> dict:
    text = reason.lower()
    if not consent:
        return dict(channel="self_service", skill="none", reason="No contact consent; show guidance without scheduling outreach.")
    rules = [
        (("locked", "contract"), "reminder", "renewals", "Review contract expiry before proposing a switch."),
        (("gst", "udyam", "document", "paperwork", "signatory"), "remote_help", "verification", "Resolve missing or mismatched evidence with document help."),
        (("install", "feasible", "fibre"), "specialist", "installation", "A feasibility or installation specialist can resolve this blocker."),
        (("price", "budget", "cheaper"), "remote_help", "commercial", "Review the budget and existing costs before quoting again."),
        (("time", "visit", "scheduled"), "self_service", "digital", "Offer an asynchronous self-service journey; a visit may add friction."),
        (("payment",), "remote_help", "billing", "Help reconcile the payment; never request card credentials."),
        (("complex", "quote"), "remote_help", "advisor", "Explain the quote and confirm requirements."),
    ]
    for terms, channel, skill, why in rules:
        if any(t in text for t in terms):
            return dict(channel=channel, skill=skill, reason=why)
    return dict(channel="self_service", skill="digital", reason="Offer self-service first; ask which specific blocker needs help.")


class Simulation(BaseModel):
    budget: int = Field(default=5000, ge=0, le=1000000)
    capacity: int = Field(default=20, ge=0, le=1000)
    contact_cost: int = Field(default=250, ge=1, le=100000)
    effect: float = Field(default=0.12, ge=-1, le=1)
    margin: float = Field(default=0.30, ge=0, le=1)
    retention: float = Field(default=0.75, ge=0, le=1)
    horizon_months: int = Field(default=12, ge=1, le=36)
    skill: Literal["all", "verification", "installation", "commercial", "billing", "advisor"] = "all"


def simulate(leads: list[dict], assumptions: Simulation) -> dict:
    a = assumptions
    rows = []
    for lead in leads:
        probability = min(1, max(0, lead["score"] / 100))
        help_route = route(lead.get("reason", ""), lead.get("contact_consent", True))
        treatable = help_route["channel"] not in ("self_service", "reminder") and (a.skill == "all" or a.skill == help_route["skill"])
        # Pure scenario assumption. Historical labels cannot identify this effect.
        effect = max(-probability, min(a.effect, 1-probability)) if treatable else 0
        contribution = lead.get("monthly_value", 0) * a.margin * a.retention * a.horizon_months
        rows.append(dict(**lead, route=help_route, baseline=probability, assumed_effect=effect, net_incremental=effect*contribution-a.contact_cost, contribution=contribution, eligible=treatable))
    slots = min(a.capacity, a.budget // a.contact_cost)
    eligible = [r for r in rows if r["eligible"]]
    propensity = sorted(eligible, key=lambda r: (-r["baseline"], r["id"]))[:slots]
    value = sorted([r for r in eligible if r["net_incremental"] > 0], key=lambda r: (-r["net_incremental"], r["id"]))[:slots]

    def result(chosen):
        increment = sum(r["assumed_effect"] for r in chosen)
        spend = len(chosen)*a.contact_cost
        return dict(contacts=len(chosen), spend=spend, assumed_incremental_activations=round(increment, 3), cost_per_incremental_activation=round(spend/increment, 2) if increment > 0 else None, net_incremental_contribution=round(sum(r["net_incremental"] for r in chosen), 2), selected=[dict(id=r["id"], name=r["name"], score=r["score"], skill=r["route"]["skill"], assumed_effect=round(r["assumed_effect"], 4), net_incremental=round(r["net_incremental"], 2)) for r in chosen])
    return dict(kind="simulation", disclosure="Assumed effects, margins and retention; not measured uplift or proven CAC reduction. Both policies use the same eligible pool, cost and capacity. Historical contact consent is assumed for this offline simulation only; no outreach is scheduled.", assumptions=a.model_dump(), propensity=result(propensity), intervention_value=result(value), baseline_expected_activations=round(sum(r["baseline"] for r in rows), 2), pool=len(rows), pilot="Randomise consenting, eligible leads within blocker/skill strata to assistance or self-service; record assignment before treatment and measure activation, cost and 90-day retention.")
