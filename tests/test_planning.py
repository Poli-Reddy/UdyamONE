import datetime as dt

import pytest

from planner import make_plan, bill_draft, BusinessContext
from assistance import Simulation, route, simulate


def profile(needs=None, owned=None, **context):
    return dict(tier=1, employees=12, sites=1, gstin=True, gstin_active=True, type="clinic", needs=needs or ["email", "backup"], context=dict(locations=[dict(id="main", name="Main clinic", employees=12, needs=needs or ["email", "backup"])], owned=owned or [], inventory_confirmed=True, **context))


def own(capability, **kw):
    return dict(site_id="main", capability=capability, quantity=5, adequacy="adequate", monthly=700, **kw)


def test_keep_only_add_gap_no_automatic_connectivity():
    p = make_plan(profile(owned=[own("email")]))
    assert [i["id"] for i in p["quote"]["items"]] == ["P17"]
    assert p["costs"]["total_monthly"] == p["quote"]["monthly"]+700
    assert "savings" not in p["quote"]
    assert p["ready"]


def test_nothing_to_buy_is_valid():
    p = make_plan(profile(needs=["email"], owned=[own("email")], budget=700))
    assert p["nothing_to_buy"] and p["ready"]
    assert p["quote"]["monthly"] == 0


def test_partial_seat_gap_and_explicit_quantity():
    p = profile(needs=["email"], owned=[{**own("email"), "quantity":2}])
    p["context"]["locations"][0]["quantities"] = {"email": 7}
    plan = make_plan(p)
    assert plan["quote"]["items"][0]["qty"] == 5


def test_budget_never_silently_drops_needs():
    p = make_plan(profile(budget=50))
    assert {i["id"] for i in p["quote"]["items"]} == {"P16", "P17"}
    assert not p["ready"] and p["costs"]["budget_state"] == "over_budget"


def test_unknown_cost_and_inventory_block_acceptance():
    raw = profile(owned=[{**own("email"), "monthly":None}], budget=10000)
    raw["context"]["inventory_confirmed"] = False
    plan = make_plan(raw)
    assert not plan["ready"]
    assert plan["costs"]["budget_state"] == "unknown"


def test_third_party_internet_cannot_satisfy_catalogue_dependency():
    plan = make_plan(profile(needs=["cctv"], owned=[own("internet")]))
    assert not plan["ready"]
    assert not plan["quote"]["items"]
    assert any("compatibility" in s for s in plan["clarify"])


def test_new_dependencies_fulfilled_on_same_site():
    plan = make_plan(profile(needs=["cctv"]))
    assert plan["ready"]
    assert {i["id"] for i in plan["quote"]["items"]} == {"P04", "P23"}
    assert {i["site_id"] for i in plan["quote"]["items"]} == {"main"}


def test_gst_ineligibility_is_an_open_question():
    raw = profile(needs=["whatsapp"])
    raw["gstin_active"] = False
    plan = make_plan(raw)
    assert not plan["ready"] and any("GSTIN" in x for x in plan["clarify"])


def test_contract_deferral_requires_explicit_preference():
    owned = [{**own("internet", contract_end="2027-04-01", switching_cost=1000), "adequacy":"inadequate"}]
    later = make_plan(profile(needs=["internet"], owned=owned, wait_for_contract=True), today=dt.date(2026,11,6))
    now = make_plan(profile(needs=["internet"], owned=owned), today=dt.date(2026,11,6))
    assert not later["quote"]["items"] and later["replace_later"]
    assert now["quote"]["items"] and now["costs"]["switching_cost_exposure"] == 1000


def test_fingerprint_changes_with_cost_or_needs():
    a = make_plan(profile())
    assert a["fingerprint"] == make_plan(profile())["fingerprint"]
    assert a["fingerprint"] != make_plan(profile(budget=50000))["fingerprint"]


def test_multisite_scope_and_shared_accounts():
    raw = profile()
    raw["sites"] = 2
    raw["context"]["locations"].append(dict(id="branch", name="Branch", employees=8, needs=["email", "backup"]))
    plan = make_plan(raw)
    assert len([i for i in plan["quote"]["items"] if i["id"] == "P16"]) == 2
    assert len([i for i in plan["quote"]["items"] if i["id"] == "P17"]) == 1


def test_invalid_location_references_rejected():
    with pytest.raises(ValueError):
        BusinessContext.model_validate(dict(locations=[dict(id="main", name="Main")], owned=[own("email", provider="test")|{"site_id":"missing"}]))


def test_bill_extraction_does_not_invent_monthly_cost_or_adequacy():
    draft = bill_draft("Airtel broadband. Amount due Rs 2,700. Contract starts 2026-01-01")
    assert draft["monthly"] is None
    assert draft["services"][0]["adequacy"] == "unknown"
    assert bill_draft("Jio internet Monthly rental: Rs 1,200")["monthly"] == 1200


def test_assistance_respects_blocker_consent_budget_and_negative_effects():
    assert route("Missing GST document")["skill"] == "verification"
    assert route("Missing GST document", consent=False)["channel"] == "self_service"
    leads = [dict(id="a", name="Likely buyer", score=99, reason="Missing document", monthly_value=1000), dict(id="b", name="Needs help", score=20, reason="Missing document", monthly_value=1000)]
    result = simulate(leads, Simulation(capacity=1, budget=250, contact_cost=250, effect=.2))
    assert result["propensity"]["selected"][0]["id"] == "a"
    assert result["intervention_value"]["selected"][0]["id"] == "b"
    assert result["intervention_value"]["spend"] <= 250
    assert simulate(leads, Simulation(effect=-.2))["intervention_value"]["contacts"] == 0
    assert simulate(leads, Simulation(budget=0))["propensity"]["contacts"] == 0


def test_owned_shared_service_is_kept_without_new_gst_eligibility():
    raw=profile(needs=['whatsapp'],owned=[{**own('whatsapp'), 'site_id':'business'}])
    raw['gstin_active']=False
    assert make_plan(raw)['nothing_to_buy']


def test_seats_cannot_be_counted_twice_across_locations():
    raw=profile(owned=[{**own('email'),'site_id':'business'}])
    with pytest.raises(ValueError,match='specific location'):
        make_plan(raw)
