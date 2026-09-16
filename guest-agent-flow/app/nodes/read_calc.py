"""Deterministic calculators. Every number the answer may quote originates here or in a tool."""
from app.audit import node_event
from app.mocks import calculator
from app.query import availability, stay_window, target_offer
from app.state import EnvelopeItem, TurnState


def _stay_quote(state: TurnState) -> dict:
    check_in, check_out = stay_window(state)
    return calculator.quote_stay(availability(state)["payload"], check_in, check_out)


def _points_price(state: TurnState) -> dict:
    check_in, check_out = stay_window(state)
    offers = availability(state)["payload"]
    return calculator.quote_points(
        state.brand_id, state.guest_ref, target_offer(state, offers), check_in, check_out
    )


CALCS = {
    "stay_quote": _stay_quote,
    "points_price": _points_price,
}


def requested(state: TurnState) -> dict[str, str]:
    out = {}
    for spec in state.plan.required_evidence:
        etype, _, policy = spec.partition(":")
        if etype in CALCS:
            out[etype] = policy or "fp_session"
    return out


def run(state: TurnState) -> dict:
    items = []
    skipped = []
    for n, (etype, policy) in enumerate(sorted(requested(state).items()), start=1):
        result = CALCS[etype](state)
        if result["payload"].get("applicable") is False:
            skipped.append(etype)
            continue
        items.append(EnvelopeItem(
            item_id=f"tmp-c{n}",
            kind="calc",
            source=result["source"],
            effective_from=state.service_date,
            effective_to=None,
            observed_at=result["observed_at"],
            freshness_policy=policy,
            evidence_type=etype,
            payload=result["payload"],
        ))
    node_event(state, "read_calc", items=len(items),
               types=[i.evidence_type for i in items], not_applicable=skipped)
    return {"calc_items": items, "not_applicable": skipped}
