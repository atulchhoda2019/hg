"""Mock guest data layer: profile, loyalty standing and reservations, with provenance.

This stands in for the membership and reservation systems of record. Every read returns
its payload plus where and when it came from, so nothing enters an answer unattributed.
"""
import copy
import json
import threading
from datetime import datetime, timezone

from app.registry import FIXTURES_DIR
from app.tracing import tool_span

_LOCK = threading.RLock()
_STATE: dict | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> dict:
    global _STATE
    with _LOCK:
        if _STATE is None:
            with (FIXTURES_DIR / "guests.json").open() as fh:
                _STATE = json.load(fh)
        return _STATE


def reset() -> None:
    """Tests call this between runs: a booking writes back into the fixture copy."""
    global _STATE
    with _LOCK:
        _STATE = None


def brands() -> dict:
    with (FIXTURES_DIR / "brands.json").open() as fh:
        return json.load(fh)


def brand(brand_id: str) -> dict:
    record = brands().get(brand_id)
    if record is None:
        raise KeyError(f"unknown brand {brand_id}")
    return record


def guest(guest_ref: str) -> dict:
    record = _load().get(guest_ref)
    if record is None:
        raise KeyError(f"unknown guest {guest_ref}")
    return copy.deepcopy(record)


def _envelope(payload: dict, source: str, effective_from: str = "2024-01-01") -> dict:
    return {
        "payload": payload,
        "source": source,
        "observed_at": _now(),
        "effective_from": effective_from,
        "effective_to": None,
    }


@tool_span("gdl.get_loyalty", "v4")
def get_loyalty(guest_ref: str) -> dict:
    record = guest(guest_ref)
    return _envelope(
        {
            "tier": record["tier"],
            "points_balance": record["points_balance"],
            "home_market": record["home_market"],
        },
        "gdl.loyalty.v4",
    )


@tool_span("gdl.get_reservations", "v6")
def get_reservations(guest_ref: str) -> dict:
    record = guest(guest_ref)
    reservations = {r["confirmation_number"]: r for r in record["reservations"]}
    return _envelope(
        {"count": str(len(reservations)), "reservations": reservations},
        "gdl.reservations.v6",
    )


def add_reservation(guest_ref: str, reservation: dict) -> None:
    """Called by the mock system of record only: this is the read-after-write surface."""
    with _LOCK:
        _load()[guest_ref]["reservations"].append(copy.deepcopy(reservation))
