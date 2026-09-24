"""Mock reservation system of record.

Writes are idempotent on a key the corridor supplies (the nonce), so a duplicate confirm
returns the original reservation instead of booking a second room. Every write is followed
by a read, and the receipt only claims `verified` when that read matches what was written.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal


@dataclass
class Reservation:
    reservation_id: str
    property_id: str
    room_id: str
    guest_id: str
    check_in: date
    check_out: date
    amount: Decimal
    currency: str
    rate_version: str
    status: str = "CONFIRMED"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    upsells: list[str] = field(default_factory=list)


_store: dict[str, Reservation] = {}
_by_idempotency_key: dict[str, str] = {}


def reset() -> None:
    _store.clear()
    _by_idempotency_key.clear()


def create(
    *,
    idempotency_key: str,
    property_id: str,
    room_id: str,
    guest_id: str,
    check_in: date,
    check_out: date,
    amount: Decimal,
    currency: str,
    rate_version: str,
) -> Reservation:
    existing = _by_idempotency_key.get(idempotency_key)
    if existing:
        return _store[existing]
    reservation = Reservation(
        reservation_id=f"R-{uuid.uuid4().hex[:8].upper()}",
        property_id=property_id,
        room_id=room_id,
        guest_id=guest_id,
        check_in=check_in,
        check_out=check_out,
        amount=amount,
        currency=currency,
        rate_version=rate_version,
    )
    _store[reservation.reservation_id] = reservation
    _by_idempotency_key[idempotency_key] = reservation.reservation_id
    return reservation


def add_upsell(*, idempotency_key: str, reservation_id: str, upsell: str) -> Reservation:
    reservation = _store[reservation_id]
    if idempotency_key in _by_idempotency_key:
        return reservation
    reservation.upsells.append(upsell)
    _by_idempotency_key[idempotency_key] = reservation_id
    return reservation


def cancel(*, idempotency_key: str, reservation_id: str) -> Reservation:
    reservation = _store[reservation_id]
    if idempotency_key not in _by_idempotency_key:
        reservation.status = "CANCELLED"
        _by_idempotency_key[idempotency_key] = reservation_id
    return reservation


def read(reservation_id: str) -> Reservation | None:
    """Read-after-write. A receipt without this is a promise, not a fact."""
    return _store.get(reservation_id)


def for_guest(guest_id: str) -> list[Reservation]:
    return [r for r in _store.values() if r.guest_id == guest_id]


GUESTS = {
    "G-2001": {"name": "Ada", "tier": "PLATINUM"},
    "G-2002": {"name": "Bo", "tier": "GOLD"},
    "G-2003": {"name": "Cass", "tier": "MEMBER"},
    "G-2004": {"name": "Dee", "tier": "MEMBER"},
}
