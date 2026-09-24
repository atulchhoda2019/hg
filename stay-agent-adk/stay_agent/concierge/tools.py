"""The concierge's tools. Plain Python, typed returns, deterministic.

Each one is a row in `registry/actions.yaml`; the before-tool callback checks that before
any body below runs. None of them takes free text from the model beyond an id, which is
why prompt injection has so little to work with here.
"""

from __future__ import annotations

from typing import Any

from google.adk.tools.tool_context import ToolContext

from ..contracts import RoomHit
from ..mocks import attribute_store, inventory, rate_engine
from ..tracing import span
from . import corridor


def _state(tool_context: ToolContext) -> Any:
    return tool_context.state


def search_rooms(tool_context: ToolContext, limit: int = 5) -> dict[str, Any]:
    """Find rooms matching the guest's typed slots. Attributes and provenance, no prices.

    Reads `slots`, filters the committed attribute version with hard predicates, and
    joins a prose snippet. Prices never appear in a search result: they are a live read.
    """
    state = _state(tool_context)
    slots = corridor.slots(state)
    property_id = corridor.property_id(state)
    check_in, check_out = corridor.stay_dates(state)
    version = attribute_store.current_version()
    state[corridor.VERSION_KEY] = version

    hits: list[RoomHit] = []
    for room in attribute_store.rooms(property_id, version):
        if slots.get("min_floor") is not None and room.floor < slots["min_floor"]:
            continue
        if slots.get("view") not in (None, "any") and room.view != slots["view"]:
            continue
        if slots.get("accessible") and not room.accessible:
            continue
        if slots.get("connecting") and not room.connecting_with:
            continue
        max_distance = slots.get("max_distance_to_elevator_m")
        if max_distance is not None and (
            room.distance_to_elevator_m is None or room.distance_to_elevator_m < max_distance
        ):
            # "away from the elevator" is a floor, not a ceiling: the guest wants distance.
            continue
        if not inventory.is_available(property_id, room.room_id, check_in, check_out):
            continue
        hits.append(
            RoomHit(
                room_id=room.room_id,
                property_id=property_id,
                floor=room.floor,
                view=room.view,
                connecting_with=room.connecting_with,
                distance_to_elevator_m=room.distance_to_elevator_m,
                accessible=room.accessible,
                attribute_version=version,
                snippet=corridor.snippet_for(property_id, room.view, room.floor),
            )
        )

    hits.sort(key=lambda hit: (-hit.floor, -(hit.distance_to_elevator_m or 0)))
    with span(
        "concierge.search_rooms",
        property_id=property_id,
        attribute_version=version,
        check_in=check_in.isoformat(),
        check_out=check_out.isoformat(),
        hits=len(hits),
        **{f"slot.{key}": value for key, value in slots.items()},
    ):
        # The span is the point of the search for an auditor: which version answered, under
        # which slots, with how many rooms. The filtering above is pure and needs no timing.
        pass
    return {
        "status": "OK" if hits else "NO_MATCH",
        "property_id": property_id,
        "attribute_version": version,
        "check_in": check_in.isoformat(),
        "check_out": check_out.isoformat(),
        "rooms": [hit.model_dump() for hit in hits[:limit]],
    }


def get_live_quote(room_id: str, tool_context: ToolContext) -> dict[str, Any]:
    """Price one room for the current stay dates. The only path for money into the chat."""
    state = _state(tool_context)
    property_id = corridor.property_id(state)
    check_in, check_out = corridor.stay_dates(state)
    with span("concierge.get_live_quote", property_id=property_id, room_id=room_id) as current:
        if not inventory.is_available(property_id, room_id, check_in, check_out):
            current.set_attribute("status", "SOLD_OUT")
            return {"status": "SOLD_OUT", "room_id": room_id}
        try:
            quote = rate_engine.quote(property_id, room_id, check_in, check_out)
        except rate_engine.RateUnavailable as exc:
            current.set_attribute("status", "UNAVAILABLE")
            return {"status": "UNAVAILABLE", "detail": str(exc), "retryable": True}
        corridor.remember_quote(state, quote)
        current.set_attribute("status", "OK")
        current.set_attribute("rate_version", quote.rate_version)
        return {"status": "OK", "quote": quote.model_dump(mode="json")}


def explain_room(room_id: str, tool_context: ToolContext) -> dict[str, Any]:
    """Why the app believes what it says about a room: document, extractor, approver."""
    state = _state(tool_context)
    property_id = corridor.property_id(state)
    room = attribute_store.room(property_id, room_id)
    if room is None:
        return {"status": "UNKNOWN_ROOM", "room_id": room_id}
    return {
        "status": "OK",
        "room": room.model_dump(mode="json"),
        "provenance": room.provenance.model_dump(mode="json") if room.provenance else None,
    }


def propose_action(
    kind: str,
    tool_context: ToolContext,
    room_id: str = "",
    quote_id: str = "",
    reservation_id: str = "",
) -> dict[str, Any]:
    """Revalidate, render the preview in code, mint a nonce. Nothing is executed here."""
    return corridor.propose(
        _state(tool_context),
        kind=kind,
        room_id=room_id or None,
        quote_id=quote_id or None,
        reservation_id=reservation_id or None,
    )


def confirm_action(nonce: str, tool_context: ToolContext) -> dict[str, Any]:
    """The gate: nonce, expiry, live revalidation, idempotent execute, read-after-write."""
    return corridor.confirm(_state(tool_context), nonce=nonce)
