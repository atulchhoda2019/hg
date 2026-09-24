"""Mock rate engine. The only component in the app that produces money.

Prices are never indexed and never cached beyond a quote's expiry: a Quote carries the
`rate_version` it was priced under, and the corridor re-reads that version at confirm
time. If it moved, the guest is re-quoted rather than charged the stale number.
"""

from __future__ import annotations

import itertools
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from ..contracts import Quote
from . import faults, inventory

QUOTE_TTL = timedelta(minutes=10)
_BASE_RATE_VERSION = "rv-2026-06-01"
_bumps = itertools.count(1)
_forced_version: str | None = None
_quoted = False


class RateUnavailable(RuntimeError):
    """The CRS did not answer. Callers retry; they never invent a number."""


def rate_version() -> str:
    """The version a quote would be priced under right now."""
    global _forced_version
    # The drill is "the rate moved under the guest", so it only fires once a quote the
    # guest has already seen exists to be invalidated.
    if _quoted and _forced_version is None and faults.fire_once("RATE_CHANGED_ONCE"):
        _forced_version = f"{_BASE_RATE_VERSION}+{next(_bumps)}"
    return _forced_version or _BASE_RATE_VERSION


def reset() -> None:
    global _forced_version, _quoted
    _forced_version = None
    _quoted = False


def _nightly(property_id: str, room_id: str, version: str) -> Decimal:
    prop = inventory.PROPERTIES[property_id]
    base = {"high_rise": 240, "resort": 310, "airport": 150, "legacy": 180}[prop.kind]
    floor = int(room_id[0])
    amount = Decimal(base) + Decimal(floor) * Decimal("12.50")
    if version != _BASE_RATE_VERSION:  # the drill: the rate moved under the guest
        amount += Decimal("35.00")
    return amount.quantize(Decimal("0.01"))


def quote(property_id: str, room_id: str, check_in: date, check_out: date) -> Quote:
    if faults.fire_once("CRS_TIMEOUT_ONCE"):
        raise RateUnavailable("rate engine timed out")
    global _quoted
    nights = max((check_out - check_in).days, 1)
    version = rate_version()
    _quoted = True
    nightly = _nightly(property_id, room_id, version)
    return Quote(
        quote_id=f"Q-{uuid.uuid4().hex[:10]}",
        property_id=property_id,
        room_id=room_id,
        nightly=nightly,
        total=(nightly * nights).quantize(Decimal("0.01")),
        currency="USD",
        nights=nights,
        rate_version=version,
        expires_at=datetime.now(timezone.utc) + QUOTE_TTL,
    )
