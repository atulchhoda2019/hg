"""The stay query: what the guest asked for, joined with what the session already knows.

Dates, brand and party size come from the trusted request context, never from a model's
reading of the text; the utterance only contributes the typed slots the planner extracted.
Both the availability read and the pricing read derive their inputs here, so they cannot
drift apart while running in parallel.
"""
from app.mocks import crs, gdl
from app.state import TurnState

DEFAULT_CHECK_IN = "2026-06-12"
DEFAULT_CHECK_OUT = "2026-06-14"


def stay_window(state: TurnState) -> tuple[str, str]:
    check_in = state.check_in or state.ui_context.get("check_in") or DEFAULT_CHECK_IN
    check_out = state.check_out or state.ui_context.get("check_out") or DEFAULT_CHECK_OUT
    return check_in, check_out


def search_params(state: TurnState) -> dict:
    slots = state.intent.slots if state.intent else {}
    profile = gdl.guest(state.guest_ref)
    check_in, check_out = stay_window(state)
    amenities = tuple(a for a in slots.get("amenities", "").split("|") if a)
    return {
        "brand_id": state.brand_id,
        "market": slots.get("market") or state.ui_context.get("market") or profile["home_market"],
        "check_in": check_in,
        "check_out": check_out,
        "party_size": int(slots.get("party_size") or profile["party_size"]),
        "max_nightly_rate": slots.get("max_nightly_rate"),
        "amenities": amenities,
        "landmark": slots.get("landmark"),
        "room_type": slots.get("room_type"),
    }


def availability(state: TurnState) -> dict:
    """The live CRS read for this turn's stay query, provenance included."""
    return crs.search_availability(**search_params(state))


def target_offer(state: TurnState, offers: list[dict]) -> dict | None:
    """The one offer a quote or a booking is about: the named one, else the cheapest hit."""
    offer_id = (state.intent.slots if state.intent else {}).get("offer_id")
    if offer_id:
        for offer in offers:
            if offer["offer_id"] == offer_id:
                return offer
        named = crs.lookup_offer(offer_id)
        # A named offer still has to belong to this brand cell to be quotable.
        return named if named and named["brand_id"] == state.brand_id else None
    return offers[0] if offers else None
