"""The write path: nothing is booked without a live, single-use, matching nonce."""
from datetime import datetime, timedelta, timezone

from langgraph.types import Command

from app.mocks import sor
from tests.conftest import config_for, make_state, run_turn

BOOK = "book offer OF-RIV-FAM"


def _preview(graph, state):
    return run_turn(graph, state)["response"]


def _confirm(graph, state, proposal_id, nonce):
    return graph.invoke(
        Command(resume={"proposal_id": proposal_id, "nonce": nonce}),
        config_for(state),
    )


def test_a_wrong_nonce_never_books_and_keeps_the_booking_parked(graph):
    state = make_state(BOOK)
    proposal = _preview(graph, state)["proposal"]

    rejected = _confirm(graph, state, proposal["proposal_id"], "not-the-nonce")
    assert rejected["__interrupt__"], "the run must still be waiting, not thrown away"
    assert sor.receipts() == {}

    receipt = _confirm(graph, state, proposal["proposal_id"], proposal["nonce"])["receipt"]
    assert receipt["verified_confirmation_number"]


def test_a_confirmation_for_another_proposal_never_books(graph):
    state = make_state(BOOK)
    preview = _preview(graph, state)
    rejected = _confirm(graph, state, "PRP-somebody-else", preview["proposal"]["nonce"])
    assert rejected["__interrupt__"]
    assert sor.receipts() == {}


def test_repeated_bad_confirmations_end_the_corridor_without_a_write(graph):
    state = make_state(BOOK)
    proposal = _preview(graph, state)["proposal"]
    for _ in range(2):
        _confirm(graph, state, proposal["proposal_id"], "wrong")
    last = _confirm(graph, state, proposal["proposal_id"], "wrong")
    assert last["response"]["rejected"] is True
    assert last.get("receipt") is None
    assert sor.receipts() == {}


def test_an_expired_confirmation_never_books(graph, monkeypatch):
    monkeypatch.setattr("app.nodes.corridor.CONFIRMATION_TTL_MINUTES", -1)
    state = make_state(BOOK)
    preview = _preview(graph, state)
    result = _confirm(graph, state, preview["proposal"]["proposal_id"],
                      preview["proposal"]["nonce"])
    assert result["response"]["expired"] is True
    assert result.get("receipt") is None
    assert sor.receipts() == {}


def test_a_nonce_is_single_use(graph):
    state = make_state(BOOK)
    preview = _preview(graph, state)
    proposal_id, nonce = preview["proposal"]["proposal_id"], preview["proposal"]["nonce"]
    first = _confirm(graph, state, proposal_id, nonce)
    assert first["receipt"]["verified_confirmation_number"]

    # Replaying the same confirmation resumes a thread that is no longer waiting.
    replay = _confirm(graph, state, proposal_id, nonce)
    assert len(sor.receipts()) == 1, "a replayed nonce must not create a second reservation"
    assert replay.get("receipt", {}).get("verified_confirmation_number") in (
        None, first["receipt"]["verified_confirmation_number"])


def test_the_preview_carries_the_exact_terms_that_will_be_written(graph):
    preview = _preview(graph, make_state(BOOK))
    proposal = preview["proposal"]
    assert proposal["total_price"] == "672.79"
    assert proposal["nights"] == 2
    assert "cancel" in proposal["cancellation"].lower()
    assert set(proposal["validations"]) >= {"brand_cell", "stay_window", "inventory", "occupancy"}
    assert datetime.fromisoformat(proposal["expires_at"]) > datetime.now(timezone.utc)
    assert datetime.fromisoformat(proposal["expires_at"]) < (
        datetime.now(timezone.utc) + timedelta(minutes=11))


def test_the_write_is_keyed_by_the_proposal_and_verified_before_the_receipt(graph):
    state = make_state(BOOK)
    preview = _preview(graph, state)
    proposal = preview["proposal"]
    receipt = _confirm(graph, state, proposal["proposal_id"], proposal["nonce"])["receipt"]

    assert proposal["proposal_id"] in sor.receipts()
    assert receipt["reconciled"] is False
    assert receipt["notified"] is False, "rung 2 asked first, so there is nothing to notify about"
    written = sor.receipts()[proposal["proposal_id"]]["reservation"]
    assert written["total_price"] == proposal["total_price"]
    assert receipt["verified_confirmation_number"] == written["confirmation_number"]


def test_points_are_checked_against_the_balance_not_asserted(graph):
    # G-7002 is Silver with a small balance: the points route is refused before any write.
    state = make_state("book offer OF-RIV-FAM with points", guest_ref="G-7002")
    response = run_turn(graph, state)["response"]
    assert response["failed_validation"] is True
    assert "points" in response["text"]
    assert sor.receipts() == {}


def test_a_sold_out_room_is_refused_at_proposal_time(graph):
    response = run_turn(graph, make_state("book offer OF-PKS-FAM"))["response"]
    assert response["failed_validation"] is True
    assert sor.receipts() == {}
