"""The corridor (I2), written against a plain state mapping so it can be tested without a model.

    typed proposal -> deterministic revalidation -> preview with nonce + expiry ->
    explicit confirm -> idempotent execute -> read-after-write verify -> receipt

Every step here is code. The model's only role is to ask for a proposal and to relay the
preview; it cannot shorten the path, and a confirm that arrives without a live pending
proposal is refused whatever the conversation says.
"""

from __future__ import annotations

import secrets
import uuid
from collections.abc import MutableMapping
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from pydantic import ValidationError

from ..contracts import ActionProposal, Quote, Receipt
from ..mocks import content_index, faults, inventory, rate_engine, reservations
from ..tracing import span

NONCE_TTL = timedelta(minutes=5)
DEFAULT_GUEST = "G-2001"

# Session state, not ADK `temp:` state: a booking corridor spans turns (quote, preview,
# confirm), and temp keys are dropped at the end of the invocation that wrote them.
SLOTS_KEY = "slots"
QUOTES_KEY = "quotes"
PENDING_KEY = "pending_action"
GUEST_KEY = "user:guest_id"
VERSION_KEY = "app:attribute_version"
AUDIT_KEY = "audit"


def slots(state: MutableMapping[str, Any]) -> dict[str, Any]:
    return dict(state.get(SLOTS_KEY) or {})


def stay_dates(state: MutableMapping[str, Any]) -> tuple[date, date]:
    current = slots(state)
    check_in = _as_date(current.get("check_in")) or date.today() + timedelta(days=7)
    check_out = _as_date(current.get("check_out")) or check_in + timedelta(days=2)
    if check_out <= check_in:
        check_out = check_in + timedelta(days=1)
    return check_in, check_out


def _as_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def property_id(state: MutableMapping[str, Any]) -> str:
    return slots(state).get("property_id") or "H-201"


def guest_id(state: MutableMapping[str, Any]) -> str:
    return state.get(GUEST_KEY) or DEFAULT_GUEST


def remember_quote(state: MutableMapping[str, Any], quote: Quote) -> None:
    quotes = dict(state.get(QUOTES_KEY) or {})
    live = {
        qid: payload
        for qid, payload in quotes.items()
        if _as_datetime(payload["expires_at"]) > datetime.now(timezone.utc)
    }
    live[quote.quote_id] = quote.model_dump(mode="json")
    state[QUOTES_KEY] = live


def live_quotes(state: MutableMapping[str, Any]) -> dict[str, Quote]:
    out: dict[str, Quote] = {}
    for qid, payload in (state.get(QUOTES_KEY) or {}).items():
        quote = Quote(**payload)
        if quote.expires_at > datetime.now(timezone.utc):
            out[qid] = quote
    return out


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def money_strings(state: MutableMapping[str, Any]) -> set[str]:
    """Every amount the app is allowed to say out loud right now."""
    allowed: set[str] = set()
    for quote in live_quotes(state).values():
        for amount in (quote.nightly, quote.total):
            allowed.add(f"{amount:.2f}")
            allowed.add(f"{amount:,.2f}")
            if amount == amount.to_integral_value():
                allowed.add(f"{int(amount)}")
                allowed.add(f"{int(amount):,}")
    return allowed


def render_preview(kind: str, quote: Quote | None, extra: dict[str, Any]) -> str:
    """The preview text is rendered here, in code, from the Quote. Never by the model."""
    if kind == "BOOK" and quote is not None:
        return (
            f"Book room {quote.room_id} at {quote.property_id}, "
            f"{extra['check_in']} to {extra['check_out']} ({quote.nights} night"
            f"{'s' if quote.nights != 1 else ''}): {quote.currency} {quote.nightly:.2f} per night, "
            f"{quote.currency} {quote.total:.2f} total. Rate version {quote.rate_version}."
        )
    if kind == "ATTRIBUTE_UPSELL":
        return (
            f"Move reservation {extra['reservation_id']} to room {extra['room_id']}"
            + (
                f" for {quote.currency} {quote.total:.2f} total ({quote.rate_version})."
                if quote is not None
                else " at no change to the rate."
            )
        )
    if kind == "CANCEL":
        return f"Cancel reservation {extra['reservation_id']}. This cannot be undone."
    return f"{kind}: nothing to preview."


def propose(
    state: MutableMapping[str, Any],
    *,
    kind: str,
    room_id: str | None = None,
    quote_id: str | None = None,
    reservation_id: str | None = None,
) -> dict[str, Any]:
    with span(
        "corridor.propose", kind=kind, room_id=room_id, quote_id=quote_id
    ) as current:
        result = _propose(
            state,
            kind=kind,
            room_id=room_id,
            quote_id=quote_id,
            reservation_id=reservation_id,
        )
        current.set_attribute("status", result["status"])
        return result


def _propose(
    state: MutableMapping[str, Any],
    *,
    kind: str,
    room_id: str | None = None,
    quote_id: str | None = None,
    reservation_id: str | None = None,
) -> dict[str, Any]:
    check_in, check_out = stay_dates(state)
    quote: Quote | None = None

    if kind in {"BOOK", "ATTRIBUTE_UPSELL"}:
        if not quote_id:
            return {"status": "NEEDS_QUOTE", "detail": "call get_live_quote first"}
        quote = live_quotes(state).get(quote_id)
        if quote is None:
            return {"status": "QUOTE_EXPIRED", "detail": "re-quote before proposing"}
        if quote.rate_version != rate_engine.rate_version():
            return {
                "status": "REFRESH",
                "detail": "the rate version moved; re-quote before proposing",
            }
        if not inventory.is_available(quote.property_id, quote.room_id, check_in, check_out):
            return {"status": "SOLD_OUT", "detail": f"room {quote.room_id} is no longer sellable"}
        room_id = quote.room_id

    if kind in {"ATTRIBUTE_UPSELL", "CANCEL"}:
        if not reservation_id or reservations.read(reservation_id) is None:
            return {"status": "UNKNOWN_RESERVATION", "detail": "no such reservation"}

    proposal = ActionProposal(
        kind=kind,
        payload={
            "room_id": room_id,
            "reservation_id": reservation_id,
            "check_in": check_in.isoformat(),
            "check_out": check_out.isoformat(),
            "guest_id": guest_id(state),
        },
        quote_id=quote_id,
        preview_text=render_preview(
            kind,
            quote,
            {
                "check_in": check_in.isoformat(),
                "check_out": check_out.isoformat(),
                "reservation_id": reservation_id,
                "room_id": room_id,
            },
        ),
        nonce=secrets.token_urlsafe(12),
        expires_at=datetime.now(timezone.utc) + NONCE_TTL,
    )
    state[PENDING_KEY] = proposal.model_dump(mode="json")
    faults.mark("preview")
    return {"status": "PREVIEW", "proposal": state[PENDING_KEY]}


def confirm(state: MutableMapping[str, Any], *, nonce: str) -> dict[str, Any]:
    with span("corridor.confirm") as current:
        result = _confirm(state, nonce=nonce)
        current.set_attribute("status", result["status"])
        receipt = result.get("receipt")
        if isinstance(receipt, dict):
            current.set_attribute("receipt.verified", bool(receipt.get("verified")))
            current.set_attribute("reservation_id", str(receipt.get("reservation_id")))
        return result


def _confirm(state: MutableMapping[str, Any], *, nonce: str) -> dict[str, Any]:
    pending = state.get(PENDING_KEY)
    if not pending:
        return {"status": "NO_PENDING_ACTION", "detail": "nothing was proposed"}
    try:
        proposal = ActionProposal(**pending)
    except ValidationError:
        state.pop(PENDING_KEY, None)
        return {"status": "NO_PENDING_ACTION", "detail": "no readable proposal to confirm"}
    if not secrets.compare_digest(proposal.nonce, nonce):
        return {"status": "BAD_NONCE", "detail": "that confirmation does not match the preview"}
    if proposal.expires_at <= datetime.now(timezone.utc):
        state.pop(PENDING_KEY, None)
        return {"status": "EXPIRED", "detail": "the preview expired; propose again"}

    payload = proposal.payload
    check_in = date.fromisoformat(payload["check_in"])
    check_out = date.fromisoformat(payload["check_out"])

    if proposal.kind in {"BOOK", "ATTRIBUTE_UPSELL"}:
        quote = live_quotes(state).get(proposal.quote_id or "")
        if quote is None:
            state.pop(PENDING_KEY, None)
            return {"status": "QUOTE_EXPIRED", "detail": "re-quote and propose again"}
        current_version = rate_engine.rate_version()
        if current_version != quote.rate_version:
            fresh = rate_engine.quote(quote.property_id, quote.room_id, check_in, check_out)
            remember_quote(state, fresh)
            state.pop(PENDING_KEY, None)
            return {
                "status": "REFRESH",
                "detail": f"rate moved from {quote.rate_version} to {current_version}; nothing booked",
                "quote": fresh.model_dump(mode="json"),
            }
        if not inventory.is_available(quote.property_id, quote.room_id, check_in, check_out):
            state.pop(PENDING_KEY, None)
            return {"status": "SOLD_OUT", "detail": "the room sold out before confirmation"}

    if proposal.kind == "BOOK":
        quote = live_quotes(state)[proposal.quote_id]  # revalidated immediately above
        reservation = reservations.create(
            idempotency_key=proposal.nonce,
            property_id=quote.property_id,
            room_id=quote.room_id,
            guest_id=payload["guest_id"],
            check_in=check_in,
            check_out=check_out,
            amount=quote.total,
            currency=quote.currency,
            rate_version=quote.rate_version,
        )
        receipt = _verify(proposal.kind, reservation.reservation_id, quote.total, quote)
    elif proposal.kind == "ATTRIBUTE_UPSELL":
        quote = live_quotes(state)[proposal.quote_id]
        reservation = reservations.add_upsell(
            idempotency_key=proposal.nonce,
            reservation_id=payload["reservation_id"],
            upsell=f"room:{quote.room_id}",
        )
        receipt = _verify(proposal.kind, reservation.reservation_id, quote.total, quote)
    else:
        reservation = reservations.cancel(
            idempotency_key=proposal.nonce, reservation_id=payload["reservation_id"]
        )
        receipt = _verify(proposal.kind, reservation.reservation_id, None, None)

    state.pop(PENDING_KEY, None)
    return {"status": "COMMITTED", "receipt": receipt.model_dump(mode="json")}


def _verify(kind: str, reservation_id: str, amount: Decimal | None, quote: Quote | None) -> Receipt:
    written = reservations.read(reservation_id)
    verified = written is not None
    if verified and kind == "BOOK":
        verified = written.amount == amount and written.status == "CONFIRMED"
    if verified and kind == "CANCEL":
        verified = written.status == "CANCELLED"
    return Receipt(
        receipt_id=f"RC-{uuid.uuid4().hex[:8].upper()}",
        kind=kind,
        reservation_id=reservation_id,
        verified=verified,
        amount=amount,
        currency=quote.currency if quote else None,
        rate_version=quote.rate_version if quote else None,
        at=datetime.now(timezone.utc),
    )


def snippet_for(property_id_value: str, view: str | None, floor: int) -> str:
    return " ".join(
        part
        for part in (
            content_index.room_snippet(property_id_value, view, floor),
            content_index.property_snippet(property_id_value),
        )
        if part
    )
