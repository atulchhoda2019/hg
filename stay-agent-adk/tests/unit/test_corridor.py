"""The corridor: everything that stands between a model's enthusiasm and a charge."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from stay_agent.concierge import corridor
from stay_agent.concierge.tools import (
    confirm_action,
    explain_room,
    get_live_quote,
    propose_action,
    search_rooms,
)
from stay_agent.mocks import faults, rate_engine, reservations


def book(tool_context) -> dict:
    quote = get_live_quote("704", tool_context)["quote"]
    preview = propose_action("BOOK", tool_context, quote_id=quote["quote_id"])
    return confirm_action(preview["proposal"]["nonce"], tool_context)


def test_search_never_returns_a_price(tool_context):
    result = search_rooms(tool_context)

    assert result["status"] == "OK"
    blob = repr(result["rooms"])
    assert "price" not in blob and "nightly" not in blob and "total" not in blob
    assert result["attribute_version"] == tool_context.state["app:attribute_version"]


def test_search_applies_hard_predicates_not_vibes(tool_context):
    tool_context.state["temp:slots"] |= {"min_floor": 6, "view": "park"}

    rooms = search_rooms(tool_context)["rooms"]

    assert rooms
    assert all(room["floor"] >= 6 and room["view"] == "park" for room in rooms)


def test_quote_is_the_only_door_money_comes_through(tool_context):
    assert not corridor.money_strings(tool_context.state)

    get_live_quote("704", tool_context)

    assert corridor.money_strings(tool_context.state)


def test_happy_path_books_once_and_verifies_the_write(tool_context):
    outcome = book(tool_context)

    assert outcome["status"] == "COMMITTED"
    receipt = outcome["receipt"]
    assert receipt["verified"] is True
    stored = reservations.read(receipt["reservation_id"])
    assert stored is not None and stored.status == "CONFIRMED"


def test_confirm_without_a_proposal_is_refused(tool_context):
    assert confirm_action("anything", tool_context)["status"] == "NO_PENDING_ACTION"


def test_a_guessed_nonce_is_refused_and_the_proposal_survives(tool_context):
    quote = get_live_quote("704", tool_context)["quote"]
    propose_action("BOOK", tool_context, quote_id=quote["quote_id"])

    assert confirm_action("not-the-nonce", tool_context)["status"] == "BAD_NONCE"
    assert tool_context.state["temp:pending_action"], "a bad guess must not clear the proposal"


def test_an_expired_preview_cannot_be_confirmed(tool_context):
    quote = get_live_quote("704", tool_context)["quote"]
    preview = propose_action("BOOK", tool_context, quote_id=quote["quote_id"])
    pending = tool_context.state["temp:pending_action"]
    pending["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()

    outcome = confirm_action(preview["proposal"]["nonce"], tool_context)

    assert outcome["status"] == "EXPIRED"
    assert reservations.for_guest("G-2001") == []


def test_a_replayed_confirmation_books_exactly_one_room(tool_context):
    first = book(tool_context)
    tool_context.state["temp:pending_action"] = {"replay": "yes"}  # a duplicate confirm arrives

    assert confirm_action("whatever", tool_context)["status"] in {"BAD_NONCE", "NO_PENDING_ACTION"}
    assert len(reservations.for_guest("G-2001")) == 1
    assert first["receipt"]["verified"] is True


def test_idempotency_is_on_the_nonce_not_the_payload(tool_context):
    """Same nonce twice at the store level is one reservation, whatever the caller does."""
    quote = get_live_quote("704", tool_context)["quote"]
    kwargs = dict(
        property_id="H-201",
        room_id="704",
        guest_id="G-2001",
        check_in=corridor.stay_dates(tool_context.state)[0],
        check_out=corridor.stay_dates(tool_context.state)[1],
        amount=quote["total"],
        currency="USD",
        rate_version=quote["rate_version"],
    )
    a = reservations.create(idempotency_key="n-1", **kwargs)
    b = reservations.create(idempotency_key="n-1", **kwargs)

    assert a.reservation_id == b.reservation_id


def test_rate_change_between_preview_and_confirm_refreshes_instead_of_charging(tool_context):
    quote = get_live_quote("704", tool_context)["quote"]
    preview = propose_action("BOOK", tool_context, quote_id=quote["quote_id"])
    faults.set_flag("RATE_CHANGED_ONCE", True)

    outcome = confirm_action(preview["proposal"]["nonce"], tool_context)

    assert outcome["status"] == "REFRESH"
    assert outcome["quote"]["rate_version"] != quote["rate_version"]
    assert reservations.for_guest("G-2001") == [], "nothing may be booked on a stale rate"


def test_sold_out_between_preview_and_confirm_books_nothing(tool_context):
    quote = get_live_quote("704", tool_context)["quote"]
    preview = propose_action("BOOK", tool_context, quote_id=quote["quote_id"])
    faults.set_flag("SOLD_OUT_AFTER_PREVIEW", True)

    outcome = confirm_action(preview["proposal"]["nonce"], tool_context)

    assert outcome["status"] == "SOLD_OUT"
    assert reservations.for_guest("G-2001") == []


def test_a_crs_timeout_is_reported_as_retryable_never_as_a_number(tool_context):
    faults.set_flag("CRS_TIMEOUT_ONCE", True)

    first = get_live_quote("704", tool_context)
    second = get_live_quote("704", tool_context)

    assert first == {"status": "UNAVAILABLE", "detail": "rate engine timed out", "retryable": True}
    assert second["status"] == "OK"


def test_proposing_without_a_quote_is_refused(tool_context):
    assert propose_action("BOOK", tool_context)["status"] == "NEEDS_QUOTE"


def test_an_expired_quote_cannot_be_proposed(tool_context):
    quote = get_live_quote("704", tool_context)["quote"]
    stored = tool_context.state["temp:quotes"][quote["quote_id"]]
    stored["expires_at"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()

    assert propose_action("BOOK", tool_context, quote_id=quote["quote_id"])["status"] == (
        "QUOTE_EXPIRED"
    )


def test_preview_text_is_rendered_from_the_quote_in_code(tool_context):
    quote = get_live_quote("704", tool_context)["quote"]

    preview = propose_action("BOOK", tool_context, quote_id=quote["quote_id"])["proposal"]

    assert f"{float(quote['total']):.2f}" in preview["preview_text"]
    assert quote["rate_version"] in preview["preview_text"]
    assert preview["nonce"] and preview["expires_at"]


def test_upsell_and_cancel_go_through_the_same_corridor(tool_context):
    receipt = book(tool_context)["receipt"]

    upsell_quote = get_live_quote("605", tool_context)["quote"]
    upsell = propose_action(
        "ATTRIBUTE_UPSELL",
        tool_context,
        quote_id=upsell_quote["quote_id"],
        reservation_id=receipt["reservation_id"],
    )
    upsold = confirm_action(upsell["proposal"]["nonce"], tool_context)

    cancel = propose_action("CANCEL", tool_context, reservation_id=receipt["reservation_id"])
    cancelled = confirm_action(cancel["proposal"]["nonce"], tool_context)

    assert upsold["status"] == "COMMITTED"
    assert "room:605" in reservations.read(receipt["reservation_id"]).upsells
    assert cancelled["status"] == "COMMITTED" and cancelled["receipt"]["verified"] is True
    assert reservations.read(receipt["reservation_id"]).status == "CANCELLED"


def test_cancelling_an_unknown_reservation_is_refused(tool_context):
    assert propose_action("CANCEL", tool_context, reservation_id="R-NOPE")["status"] == (
        "UNKNOWN_RESERVATION"
    )


def test_explain_room_answers_with_provenance_not_prose(tool_context):
    result = explain_room("704", tool_context)

    assert result["status"] == "OK"
    provenance = result["provenance"]
    assert provenance["source_doc_id"] and provenance["extractor_version"]
    assert provenance["attribute_version"] == tool_context.state.get(
        "app:attribute_version", provenance["attribute_version"]
    )


def test_quotes_expire_out_of_state_rather_than_lingering(tool_context):
    quote = get_live_quote("704", tool_context)["quote"]
    tool_context.state["temp:quotes"][quote["quote_id"]]["expires_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()

    get_live_quote("605", tool_context)

    assert quote["quote_id"] not in tool_context.state["temp:quotes"]
    assert rate_engine.QUOTE_TTL.total_seconds() > 0
