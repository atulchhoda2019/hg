"""Mock reservation system of record: commit once per idempotency key, with fault injection.

A booking command is executed exactly once for a given key. The timeout drill commits the
reservation and then loses the response, which is the case the corridor must reconcile by
key rather than retry blindly.
"""
import os
import threading
from datetime import datetime, timezone

from app.mocks import crs, gdl
from app.tracing import tool_span

_LOCK = threading.Lock()
_RECEIPTS: dict[str, dict] = {}
_FAULTS_FIRED: set[str] = set()


class SorUnknownOutcome(RuntimeError):
    """The booking call did not return: the reservation may or may not have committed."""


def reset() -> None:
    with _LOCK:
        _RECEIPTS.clear()
        _FAULTS_FIRED.clear()


@tool_span("sor.book", "v8")
def book(command: dict) -> dict:
    key = command["idempotency_key"]
    with _LOCK:
        existing = _RECEIPTS.get(key)
        if existing is not None:
            return dict(existing, replayed=True)

        params = command["params"]
        reservation = {
            "confirmation_number": f"RES-{key.split('-')[-1].upper()}",
            "property_id": params["property_id"],
            "offer_id": params["offer_id"],
            "room_name": params["room_name"],
            "rate_plan": params["rate_plan"],
            "check_in": params["check_in"],
            "check_out": params["check_out"],
            "nights": params["nights"],
            "total_price": params["total_price"],
            "points_applied": params["points_applied"],
            "cash_due": params["cash_due"],
            "status": "confirmed",
        }
        receipt = {
            "receipt_id": f"RCP-{key}",
            "idempotency_key": key,
            "action": command["action"],
            "reservation": reservation,
            "committed_at": datetime.now(timezone.utc).isoformat(),
            "replayed": False,
        }
        _RECEIPTS[key] = receipt
        crs.hold_room(params["offer_id"])
        gdl.add_reservation(command["guest_ref"], reservation)

        if os.environ.get("SOR_TIMEOUT_ONCE") == "1" and "sor_timeout" not in _FAULTS_FIRED:
            _FAULTS_FIRED.add("sor_timeout")
            # The room is held and the reservation exists; only the response was lost.
            raise SorUnknownOutcome(key)

    return dict(receipt)


@tool_span("sor.lookup_by_key", "v8")
def lookup_by_key(idempotency_key: str) -> dict | None:
    with _LOCK:
        receipt = _RECEIPTS.get(idempotency_key)
        return dict(receipt, replayed=True) if receipt else None


def receipts() -> dict[str, dict]:
    """Test and audit view of everything that actually committed."""
    with _LOCK:
        return {key: dict(value) for key, value in _RECEIPTS.items()}
