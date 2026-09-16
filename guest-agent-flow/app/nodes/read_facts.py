"""Typed operational reads: live availability, loyalty standing, reservations.

Only the evidence types the fired table row asked for are read, and availability is always
read live - it is never served from the content index, however recently it was crawled.
"""
from app.audit import node_event
from app.mocks import gdl
from app.query import availability, offers_in_question
from app.state import EnvelopeItem, TurnState


def offer_row(offer: dict) -> dict:
    return {
        "property": offer["property_name"],
        "room": offer["room_name"],
        "rate plan": offer["rate_plan"],
        "nightly rate": offer["nightly_rate"],
        "breakfast included": "yes" if offer["breakfast_included"] else "no",
        "refundable": "yes" if offer["refundable"] else "no",
        "sleeps": str(offer["max_occupancy"]),
        "rooms left": str(offer["rooms_left"]),
    }


def _availability(state: TurnState) -> tuple[dict, list[dict]]:
    result = availability(state)
    offers = offers_in_question(state)
    payload: dict = {"count": str(len(offers)), "check in": result["query"]["check_in"],
                     "check out": result["query"]["check_out"]}
    for offer in offers:
        payload[f"offer {offer['offer_id']}"] = offer_row(offer)
    return {**result, "payload": payload}, offers


def _loyalty(state: TurnState) -> tuple[dict, list[dict]]:
    return gdl.get_loyalty(state.guest_ref), []


def _reservations(state: TurnState) -> tuple[dict, list[dict]]:
    return gdl.get_reservations(state.guest_ref), []


READERS = {
    "availability": _availability,
    "loyalty": _loyalty,
    "reservations": _reservations,
}


def requested(state: TurnState) -> dict[str, str]:
    out = {}
    for spec in state.plan.required_evidence:
        etype, _, policy = spec.partition(":")
        if etype in READERS:
            out[etype] = policy or "fp_live"
    return out


def run(state: TurnState) -> dict:
    items: list[EnvelopeItem] = []
    offers: list[dict] = []
    for index, (etype, policy) in enumerate(sorted(requested(state).items()), start=1):
        result, hits = READERS[etype](state)
        offers += hits
        items.append(EnvelopeItem(
            item_id=f"tmp-f{index}",
            kind="fact",
            source=result["source"],
            effective_from=result.get("effective_from") or state.service_date,
            effective_to=result.get("effective_to"),
            observed_at=result["observed_at"],
            freshness_policy=policy,
            evidence_type=etype,
            payload=result["payload"],
        ))
    node_event(state, "read_facts", items=len(items),
               types=[i.evidence_type for i in items], offers=[o["offer_id"] for o in offers])
    return {"fact_items": items, "offers": offers}
