"""Governance gate 1: franchise cell isolation, capability bundle, autonomy rung, posture.

The effective rung is min(brand rung, decision table rung_max). Rung 1 drafts the booking
for the guest to finish on the brand site, rung 2 previews and waits for a nonce, rung 3
and 4 run the corridor without pausing but keep revalidation, idempotency, read-after-write
verification and the receipt.
"""
from app.audit import node_event
from app.mocks import gdl
from app.registry import bundles
from app.state import TurnState


def bundle_for(brand_id: str) -> dict:
    brands = bundles()["brands"]
    if brand_id not in brands:
        raise KeyError(f"no capability bundle for brand {brand_id}")
    return brands[brand_id]


def effective_rung(brand_id: str, intent: str, rung_max: int) -> int:
    brand_rung = bundle_for(brand_id)["rungs"].get(intent, 0)
    return min(brand_rung, rung_max)


def run(state: TurnState) -> dict:
    plan = state.plan
    intent = state.intent
    bundle = bundle_for(state.brand_id)

    if gdl.guest(state.guest_ref)["brand_id"] != state.brand_id:
        node_event(state, "gate_plan", allowed=False, reason="brand_mismatch")
        return {"response": {
            "kind": "handoff",
            "summary": "I cannot act on this membership from this brand's site.",
            "top_intents": [intent.name],
            "reason": "brand_mismatch",
        }}

    if plan.requires_capability and plan.requires_capability not in bundle["capabilities"]:
        node_event(state, "gate_plan", allowed=False, reason="capability_not_enabled")
        return {"response": {
            "kind": "handoff",
            "summary": "That is not enabled for this brand yet, so I am handing this to the "
                       "hotel team.",
            "top_intents": [intent.name],
            "reason": "capability_not_enabled",
        }}

    updates: dict = {}
    if plan.posture == "WRITE":
        rung = effective_rung(state.brand_id, intent.name, plan.rung)
        if rung == 0:
            node_event(state, "gate_plan", allowed=False, reason="rung_zero")
            return {"response": {
                "kind": "handoff",
                "summary": "Booking through the assistant is not available for this brand; "
                           "the booking page can complete this stay.",
                "top_intents": [intent.name],
                "reason": "rung_zero",
            }}
        posture = "DRAFT" if rung == 1 else "WRITE"
        updates["plan"] = plan.model_copy(update={"rung": rung, "posture": posture})

    node_event(state, "gate_plan", allowed=True, cell=bundle["cell"],
               rung=updates.get("plan", plan).rung, posture=updates.get("plan", plan).posture)
    return updates
