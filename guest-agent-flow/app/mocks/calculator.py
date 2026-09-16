"""Deterministic money. The model never adds, converts or rounds anything.

Nightly rate, nights, taxes, fees, points conversion and the points-and-cash split are all
computed here with exact decimals, and every result carries the inputs it was built from.
"""
from datetime import datetime, timezone
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from app.mocks import crs, gdl
from app.tracing import tool_span

POINTS_GRANULARITY = Decimal("100")


def _money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _points(value: Decimal) -> str:
    return str((value / POINTS_GRANULARITY).quantize(Decimal("1"), rounding=ROUND_CEILING)
               * POINTS_GRANULARITY)


def _receipt(payload: dict, source: str) -> dict:
    return {"payload": payload, "source": source,
            "observed_at": datetime.now(timezone.utc).isoformat()}


def price_stay(offer: dict, nights: int) -> dict:
    """The one place a stay total is produced, used by both the quote and the corridor."""
    nightly = Decimal(offer["nightly_rate"])
    subtotal = nightly * nights
    fees = Decimal(offer["nightly_fee"]) * nights
    taxes = (subtotal + fees) * Decimal(offer["tax_rate"])
    return {
        "property": offer["property_name"],
        "room": offer["room_name"],
        "rate_plan": offer["rate_plan"],
        "nights": str(nights),
        "nightly_rate": _money(nightly),
        "room_subtotal": _money(subtotal),
        "taxes_and_fees": _money(taxes + fees),
        "total_price": _money(subtotal + fees + taxes),
    }


@tool_span("calc.quote_stay", "v3")
def quote_stay(offers: list[dict], check_in: str, check_out: str) -> dict:
    if not offers:
        return _receipt({"applicable": False}, "calc.quote_stay.v3")
    stay_nights = crs.nights(check_in, check_out)
    payload: dict = {
        "applicable": True,
        "check_in": check_in,
        "check_out": check_out,
        "nights": str(stay_nights),
    }
    for offer in offers:
        payload[f"stay {offer['offer_id']}"] = price_stay(offer, stay_nights)
    payload["inputs"] = ["crs.availability.v9"]
    return _receipt(payload, "calc.quote_stay.v3")


@tool_span("calc.quote_points", "v3")
def quote_points(brand_id: str, guest_ref: str, offer: dict | None,
                 check_in: str, check_out: str) -> dict:
    """Points, or points and cash, for one stay. Redemption terms come from the brand row."""
    if offer is None:
        return _receipt({"applicable": False}, "calc.quote_points.v3")
    program = gdl.brand(brand_id)
    balance = Decimal(gdl.guest(guest_ref)["points_balance"])
    per_dollar = Decimal(program["points_per_dollar"])
    min_share = Decimal(program["min_points_share"])

    total = Decimal(price_stay(offer, crs.nights(check_in, check_out))["total_price"])
    required = Decimal(_points(total * per_dollar))
    applied = Decimal(_points(min(balance, required)))
    if applied > balance:  # rounding up may overshoot the balance by less than one step
        applied = (balance / POINTS_GRANULARITY).to_integral_value(rounding="ROUND_FLOOR") \
            * POINTS_GRANULARITY
    cash_due = max(total - applied / per_dollar, Decimal("0"))
    share = applied / required if required else Decimal("0")

    return _receipt(
        {
            "applicable": True,
            "offer_id": offer["offer_id"],
            "total_price": _money(total),
            "points_balance": str(balance),
            "points_per_dollar": str(per_dollar),
            "points_for_full_stay": str(required),
            "points_applied": str(applied),
            "cash_due": _money(cash_due),
            "covers_full_stay": "yes" if applied >= required else "no",
            "points_and_cash_allowed": "yes" if share >= min_share else "no",
            "inputs": ["crs.availability.v9", "gdl.loyalty.v4"],
        },
        "calc.quote_points.v3",
    )
