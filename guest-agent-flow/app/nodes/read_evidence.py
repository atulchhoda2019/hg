"""Property content read. Filter first on brand, property set and stay date; rerank inside.

The property set is the one the guest can actually be shown: the offers that survived the
live availability predicates, or the property the session is already scoped to. A hotel
that has no room for these dates never reaches retrieval, so its description cannot be
quoted back as if it were an option.
"""
from app.audit import node_event
from app.mocks import content, crs, gdl
from app.query import offers_in_question
from app.state import EnvelopeItem, TurnState

EVIDENCE_TYPE = "property_content"


def required_policy(state: TurnState) -> str | None:
    for spec in state.plan.required_evidence:
        etype, _, policy = spec.partition(":")
        if etype == EVIDENCE_TYPE:
            return policy or "fp_current"
    return None


def property_scope(state: TurnState) -> list[str]:
    scoped = state.ui_context.get("property_id") or state.intent.slots.get("property_id")
    if scoped:
        prop = crs.properties().get(scoped)
        return [scoped] if prop and prop["brand_id"] == state.brand_id else []
    if any(spec.split(":")[0] == "availability" for spec in state.plan.required_evidence):
        return [offer["property_id"] for offer in offers_in_question(state)]
    if any(spec.split(":")[0] == "reservations" for spec in state.plan.required_evidence):
        booked = gdl.get_reservations(state.guest_ref)["payload"]["reservations"]
        return [row["property_id"] for row in booked.values()]
    return [pid for pid, prop in crs.properties().items() if prop["brand_id"] == state.brand_id]


def offer_scope(state: TurnState) -> tuple[set[str] | None, set[str] | None]:
    """Rooms and rate plans this turn is about, so other rooms' content is not evidence for it."""
    offers = offers_in_question(state)
    if not offers:
        return None, None
    return ({offer["room_type"] for offer in offers},
            {offer["rate_plan"] for offer in offers})


def fetch(state: TurnState, policy: str) -> list[EnvelopeItem]:
    scope = property_scope(state)
    room_types, rate_plans = offer_scope(state)
    result = content.retrieve(
        brand_id=state.brand_id,
        property_ids=scope,
        service_date=state.service_date,
        query=state.utterance,
        room_types=room_types,
        rate_plans=rate_plans,
    )
    return [
        EnvelopeItem(
            item_id=f"tmp-p{n}",
            kind="policy",
            source=f"{result['source']}#{passage['passage_id']}@{passage['version']}",
            effective_from=passage["effective_from"],
            effective_to=passage["effective_to"],
            observed_at=result["observed_at"],
            freshness_policy=policy,
            evidence_type=EVIDENCE_TYPE,
            payload={"text": passage["text"], "passage_id": passage["passage_id"]},
        )
        for n, passage in enumerate(result["payload"], start=1)
    ]


def run(state: TurnState) -> dict:
    policy = required_policy(state)
    if policy is None:
        return {"evidence_items": []}
    items = fetch(state, policy)
    node_event(state, "read_evidence", items=len(items),
               passages=[i.payload["passage_id"] for i in items])
    return {"evidence_items": items}
