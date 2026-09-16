"""The five demo moments, end to end through the graph."""
from langgraph.types import Command

from tests.conftest import config_for, make_state, run_turn

SEARCH = "family suite near the park, under $300, with breakfast"


def confirm(graph, state, response):
    proposal = response["proposal"]
    return graph.invoke(
        Command(resume={"proposal_id": proposal["proposal_id"], "nonce": response["nonce"]}),
        config_for(state),
    )


def test_moment_1_documented_but_unbookable_rooms_never_reach_the_answer(graph):
    """Parkside is the best documented park property and has zero rooms left for these dates."""
    result = run_turn(graph, make_state(SEARCH))

    offered = {offer["offer_id"] for offer in result["response"]["offers"]}
    assert "OF-PKS-FAM" not in offered
    assert "Parkside" not in result["response"]["text"]
    # Content is only retrieved for properties that survived the live availability read.
    cited = {item.payload.get("passage_id") for item in result["envelope"]}
    assert "PSG-PKS-FAMILY" not in cited
    # Availability is read this turn, never served from the content index.
    live = [i for i in result["envelope"] if i.evidence_type == "availability"]
    assert live and all(i.freshness_policy in ("fp_live", "fp_session") for i in live)


def test_moment_2_the_search_filters_before_it_composes(graph):
    result = run_turn(graph, make_state(SEARCH))
    assert result["intent"].name == "property_search"
    assert result["intent"].slots["room_type"] == "family_suite"
    assert result["intent"].slots["max_nightly_rate"] == "300"
    assert result["intent"].slots["amenities"] == "breakfast"
    assert result["intent"].slots["landmark"] == "park"

    offers = result["response"]["offers"]
    assert offers, "the hard predicates must leave at least the Riverpark family suite"
    for offer in offers:
        assert offer["room_type"] == "family_suite"
        assert float(offer["nightly_rate"]) <= 300
        assert offer["breakfast_included"] is True
        assert offer["rooms_left"] > 0
        assert offer["property_id"] in ("PR-RIVERPARK", "PR-LAKEVIEW", "PR-UNIONHALL",
                                        "PR-PARKSIDE")
    assert result["response"]["citations"]


def test_moment_3_booking_previews_then_books_only_on_a_nonce(graph):
    state = make_state("book offer OF-RIV-FAM")
    first = run_turn(graph, state)
    preview = first["response"]
    assert preview["kind"] == "preview"
    assert first["receipt"] is None, "the first turn must not book anything"
    proposal = preview["proposal"]
    assert proposal["nightly_rate"] == "289.00"
    assert proposal["property_name"] == "Riverpark Grand"
    assert "cancelled without charge" in proposal["cancellation"]

    receipt = confirm(graph, state, preview)["response"]["receipt"]
    assert receipt["verified_confirmation_number"] == \
        receipt["reservation"]["confirmation_number"]
    assert receipt["reservation"]["total_price"] == proposal["total_price"]


def test_moment_4a_a_moved_rate_reprices_and_asks_again(graph, monkeypatch):
    monkeypatch.setenv("RATE_CHANGE_ONCE", "1")
    state = make_state("book offer OF-RIV-FAM")
    first = run_turn(graph, state)
    assert first["response"]["proposal"]["nightly_rate"] == "289.00"

    second = confirm(graph, state, first["response"])["response"]
    assert second["kind"] == "preview", "a changed rate must never book silently"
    assert second["proposal"]["nightly_rate"] == "329.00"
    assert second["proposal"]["nonce"] != first["response"]["proposal"]["nonce"]

    third = confirm(graph, state, second)["response"]
    assert third["kind"] == "receipt"
    assert third["receipt"]["reservation"]["total_price"] == second["proposal"]["total_price"]


def test_moment_4b_an_unknown_write_outcome_is_reconciled_not_retried(graph, monkeypatch):
    monkeypatch.setenv("SOR_TIMEOUT_ONCE", "1")
    state = make_state("book offer OF-RIV-FAM")
    first = run_turn(graph, state)
    receipt = confirm(graph, state, first["response"])["response"]["receipt"]

    assert receipt["reconciled"] is True
    from app.mocks import gdl
    booked = gdl.get_reservations("G-7001")["payload"]["reservations"]
    held = [row for row in booked.values() if row["property_id"] == "PR-RIVERPARK"]
    assert len(held) == 1, "the timed out write must not be duplicated by a retry"


def test_moment_5_every_turn_leaves_a_routable_trace(graph):
    from app import audit
    state = make_state(SEARCH)
    result = run_turn(graph, state)
    events = [e for e in audit.read(state.conversation_id) if "." not in e["node"]]
    nodes = [event["node"] for event in events]

    assert nodes[0] == "ingress" and nodes[-1] == "respond"
    planner = next(e for e in events if e["node"] == "planner")
    assert planner["row"] == result["plan"].table_row_id
    assert planner["graph_id"] == result["plan"].graph_id
    gate = next(e for e in events if e["node"] == "gate_output")
    assert gate["passed"] is True
    assert all("email" not in str(event) for event in events)


GOLDEN = [
    (SEARCH, "property_search", "answer"),
    ("how many points do I have", "points_balance", "answer"),
    ("what is my reservation", "reservation_status", "answer"),
    ("what is the cancellation policy", "property_policy", "answer"),
    ("quote offer OF-RIV-FAM", "stay_quote", "answer"),
    ("book offer OF-RIV-FAM", "booking_create", "preview"),
    ("move my reservation to next week", "booking_modify", "answer"),
    ("cancel my reservation", "booking_cancel", "answer"),
    ("I need extra towels", "service_request", "answer"),
    ("my trip stuff is wrong", "__out_of_scope__", "handoff"),
]


def test_the_market_slice_routes_to_one_graph_and_never_answers_uncited(graph):
    """The one-market slice an evaluation ring would replay before a brand is switched on."""
    for utterance, intent, kind in GOLDEN:
        result = run_turn(graph, make_state(utterance))
        assert result["intent"].name == intent, utterance
        assert result["response"]["kind"] == kind, utterance
        assert result["plan"].graph_id, utterance
        if kind == "answer":
            assert not result["response"].get("scripted"), utterance
            assert result["response"]["citations"], utterance
