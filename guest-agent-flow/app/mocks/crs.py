"""Mock central reservation system: live availability and rates.

Availability is never indexed and never cached into the content corpus; it is read here,
this turn, or it is not stated. The hard predicates - brand, market, stay window,
occupancy, price ceiling, requested amenities - are applied inside the search, so a
property that fails one of them never reaches retrieval, reranking or the model.
"""
import copy
import json
import os
import threading
from datetime import date, datetime, timezone
from decimal import Decimal

from app.registry import FIXTURES_DIR
from app.tracing import tool_span

_LOCK = threading.RLock()
_OFFERS: dict[str, dict] | None = None
_READS: dict[str, int] = {}

RATE_CHANGE_DELTA = Decimal("40.00")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> dict[str, dict]:
    global _OFFERS
    with _LOCK:
        if _OFFERS is None:
            with (FIXTURES_DIR / "inventory.json").open() as fh:
                _OFFERS = {o["offer_id"]: o for o in json.load(fh)["offers"]}
        return _OFFERS


def properties() -> dict:
    with (FIXTURES_DIR / "properties.json").open() as fh:
        return json.load(fh)


def offers() -> list[dict]:
    """Every offer the inventory knows, undecorated. For configuration checks, not answers."""
    return list(_load().values())


def reset() -> None:
    global _OFFERS
    with _LOCK:
        _OFFERS = None
        _READS.clear()


def nights(check_in: str, check_out: str) -> int:
    return (date.fromisoformat(check_out) - date.fromisoformat(check_in)).days


def _decorate(offer: dict) -> dict:
    prop = properties()[offer["property_id"]]
    return {
        **copy.deepcopy(offer),
        "property_name": prop["name"],
        "market": prop["market"],
        "landmarks": prop["landmarks"],
        "brand_id": prop["brand_id"],
        "tax_rate": prop["tax_rate"],
        "nightly_fee": prop["nightly_fee"],
    }


@tool_span("crs.search_availability", "v9")
def search_availability(
    brand_id: str,
    market: str | None,
    check_in: str,
    check_out: str,
    party_size: int = 1,
    max_nightly_rate: str | None = None,
    amenities: tuple[str, ...] = (),
    landmark: str | None = None,
    room_type: str | None = None,
    limit: int = 3,
) -> dict:
    """Hard predicates first; ranking only inside the set that already passed them."""
    hits: list[dict] = []
    for offer in sorted(_load().values(), key=lambda o: o["offer_id"]):
        row = _decorate(offer)
        if row["brand_id"] != brand_id:
            continue
        if market and row["market"] != market:
            continue
        if row["rooms_left"] < 1:
            continue
        if not (row["bookable_from"] <= check_in and check_out <= row["bookable_to"]):
            continue
        if row["max_occupancy"] < party_size:
            continue
        if max_nightly_rate and Decimal(row["nightly_rate"]) > Decimal(max_nightly_rate):
            continue
        if "breakfast" in amenities and not row["breakfast_included"]:
            continue
        if landmark and landmark not in row["landmarks"]:
            continue
        if room_type and row["room_type"] != room_type:
            continue
        hits.append(row)

    hits.sort(key=lambda row: (Decimal(row["nightly_rate"]), row["offer_id"]))
    return {
        "payload": hits[:limit],
        "source": "crs.availability.v9",
        "observed_at": _now(),
        "query": {
            "brand_id": brand_id,
            "market": market,
            "check_in": check_in,
            "check_out": check_out,
            "party_size": str(party_size),
            "max_nightly_rate": max_nightly_rate,
            "amenities": list(amenities),
            "landmark": landmark,
            "room_type": room_type,
        },
    }


@tool_span("crs.lookup_offer", "v9")
def lookup_offer(offer_id: str) -> dict | None:
    """Read one offer for display. Advisory reads use this; it never moves the rate."""
    with _LOCK:
        offer = _load().get(offer_id)
        return _decorate(offer) if offer else None


@tool_span("crs.get_offer", "v9")
def get_offer(offer_id: str) -> dict | None:
    """Re-read one offer at the moment of use inside the corridor.

    Rates move between a preview and a confirmation, so the corridor asks the CRS again
    rather than trusting the number it printed a minute ago.
    """
    with _LOCK:
        offer = _load().get(offer_id)
        if offer is None:
            return None
        _READS[offer_id] = _READS.get(offer_id, 0) + 1
        row = _decorate(offer)
        # Fault drill: the rate moves between the preview and the confirmation.
        if os.environ.get("RATE_CHANGE_ONCE") == "1" and _READS[offer_id] > 1:
            row["nightly_rate"] = str(Decimal(row["nightly_rate"]) + RATE_CHANGE_DELTA)
        return row


def hold_room(offer_id: str) -> None:
    """Called by the mock system of record when a booking commits."""
    with _LOCK:
        offer = _load()[offer_id]
        offer["rooms_left"] = max(offer["rooms_left"] - 1, 0)
