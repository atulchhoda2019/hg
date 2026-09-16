"""The booking corridor: propose, preview, nonce-bound confirmation, revalidate,
idempotent write, read-after-write verify, receipt.

A booking is a typed proposal that the guest confirms, not a sentence the model emits.
The nonce is checked inside `wait_confirmation`, not in the API handler, so nothing that
resumes the thread can skip it. Rung 3 and 4 keep every step except the human pause - and
if the rate or the cancellation terms move under them, they fall back to asking.
"""
import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from langgraph.types import interrupt

from app.audit import node_event
from app.mocks import calculator, content, crs, gdl, sor
from app.query import search_params, stay_window, target_offer
from app.state import BookingProposal, TurnState

CONFIRMATION_TTL_MINUTES = 10
MAX_CONFIRMATION_ATTEMPTS = 3


def _now() -> datetime:
    return datetime.now(timezone.utc)


def cancellation_terms(offer: dict, service_date: str) -> tuple[str, str]:
    """The cancellation sentence shown in the preview comes from property content, versioned."""
    item = content.passage(offer["cancellation_passage"], service_date)
    if item is None:
        return "Cancellation terms are not published for this rate.", "none"
    return item["text"], f"{item['passage_id']}@{item['version']}"


def deterministic_checks(state: TurnState, offer: dict | None,
                         pay_with: str) -> tuple[list[str], str | None]:
    """Returns (passed check names, failure explanation). No model output is involved."""
    passed: list[str] = []
    if offer is None:
        return passed, "That room is no longer offered for these dates."
    if offer["brand_id"] != state.brand_id:
        return passed, "That room belongs to another brand and cannot be booked here."
    passed.append("brand_cell")

    check_in, check_out = stay_window(state)
    if not (offer["bookable_from"] <= check_in and check_out <= offer["bookable_to"]):
        return passed, "That rate is not open for the dates you gave me."
    passed.append("stay_window")

    if offer["rooms_left"] < 1:
        return passed, "The last room on that rate has just gone."
    passed.append("inventory")

    party = int(search_params(state)["party_size"])
    if offer["max_occupancy"] < party:
        return passed, f"That room sleeps {offer['max_occupancy']}, which is fewer than your party."
    passed.append("occupancy")

    if pay_with in ("points", "points_cash"):
        points = calculator.quote_points(state.brand_id, state.guest_ref, offer,
                                         check_in, check_out)["payload"]
        if pay_with == "points" and points["covers_full_stay"] != "yes":
            return passed, (f"That stay needs {points['points_for_full_stay']} points and you "
                            f"have {points['points_balance']}, so points alone will not cover it.")
        if pay_with == "points_cash" and points["points_and_cash_allowed"] != "yes":
            return passed, "Your points balance is below the minimum share for points and cash."
        passed.append("points_balance")

    return passed, None


def build_proposal(state: TurnState, offer: dict, pay_with: str,
                   passed: list[str]) -> BookingProposal:
    check_in, check_out = stay_window(state)
    nights = crs.nights(check_in, check_out)
    price = calculator.price_stay(offer, nights)
    points_applied, cash_due = "0", price["total_price"]
    if pay_with in ("points", "points_cash"):
        points = calculator.quote_points(state.brand_id, state.guest_ref, offer,
                                         check_in, check_out)["payload"]
        points_applied, cash_due = points["points_applied"], points["cash_due"]
    terms, terms_version = cancellation_terms(offer, state.service_date)
    return BookingProposal(
        proposal_id=f"PRP-{secrets.token_hex(6)}",
        action="RoomBooking",
        guest_ref=state.guest_ref,
        property_id=offer["property_id"],
        property_name=offer["property_name"],
        offer_id=offer["offer_id"],
        room_name=offer["room_name"],
        rate_plan=offer["rate_plan"],
        check_in=check_in,
        check_out=check_out,
        nights=nights,
        nightly_rate=price["nightly_rate"],
        total_price=price["total_price"],
        pay_with=pay_with,
        points_applied=points_applied,
        cash_due=cash_due,
        cancellation=f"{terms} ({terms_version})",
        validations=passed,
        nonce=secrets.token_urlsafe(16),
        expires_at=(_now() + timedelta(minutes=CONFIRMATION_TTL_MINUTES)).isoformat(),
    )


def propose(state: TurnState) -> dict:
    slots = state.intent.slots
    pay_with = slots.get("pay_with", "cash")
    offer_id = slots.get("offer_id") or (target_offer(state, state.offers) or {}).get("offer_id")

    if not offer_id:
        node_event(state, "corridor_propose", ok=False, reason="no_offer_selected")
        return {"response": {
            "kind": "clarify",
            "question": "Which of these rooms would you like me to book?",
            "options": [
                {"intent": "booking_create",
                 "label": f"book offer {offer['offer_id']}",
                 "detail": f"{offer['property_name']} {offer['room_name']} at "
                           f"{offer['nightly_rate']} a night"}
                for offer in state.offers[:3]
            ],
        }}

    offer = crs.get_offer(offer_id)
    passed, failure = deterministic_checks(state, offer, pay_with)
    if failure:
        node_event(state, "corridor_propose", ok=False, reason="validation_failed", checks=passed)
        return {"response": {
            "kind": "answer",
            "text": failure,
            "citations": [item.item_id for item in state.envelope],
            "failed_validation": True,
        }}

    proposal = build_proposal(state, offer, pay_with, passed)
    node_event(state, "corridor_propose", ok=True, proposal_id=proposal.proposal_id, checks=passed)
    return {"proposal": proposal}


def preview(state: TurnState) -> dict:
    """The exact stay, price and terms that will be written if the guest says yes."""
    proposal = state.proposal
    response = {
        "kind": "preview",
        "proposal": proposal.model_dump(),
        "nonce": proposal.nonce,
        "expires_at": proposal.expires_at,
        "undo_window": "This rate can be cancelled under the terms above from this conversation.",
        "citations": [item.item_id for item in state.envelope],
    }
    if state.retries.get("rate_refresh"):
        response["rate_changed"] = True
        response["notice"] = ("The rate moved while we were talking, so this is the current "
                              "price. Nothing has been booked yet.")
    node_event(state, "corridor_preview", proposal_id=proposal.proposal_id,
               refreshed=bool(state.retries.get("rate_refresh")))
    return {"response": response}


def wait_confirmation(state: TurnState) -> dict:
    """Pauses the run. Resumes only for this proposal id with this nonce, before expiry.

    A wrong nonce does not book and does not throw the proposal away: the run stays parked,
    so a mistyped confirmation cannot be turned into either a booking or a lost hold. Expiry
    and repeated rejection both end the corridor without a write.
    """
    proposal = state.proposal
    rejected = 0
    while True:
        answer = interrupt({
            "kind": "await_confirmation",
            "proposal_id": proposal.proposal_id,
            "expires_at": proposal.expires_at,
            "rejected": rejected,
        }) or {}

        if _now() > datetime.fromisoformat(proposal.expires_at):
            node_event(state, "corridor_wait_confirmation", confirmed=False, reason="expired")
            return {"confirmed": False, "response": {
                "kind": "answer",
                "text": "That price is no longer held, so I have not booked anything. "
                        "Ask me again and I will price the same stay fresh.",
                "citations": [],
                "expired": True,
            }}

        matches = (answer.get("proposal_id") == proposal.proposal_id
                   and secrets.compare_digest(str(answer.get("nonce", "")), proposal.nonce))
        if matches:
            node_event(state, "corridor_wait_confirmation", confirmed=True,
                       proposal_id=proposal.proposal_id)
            return {"confirmed": True}

        rejected += 1
        node_event(state, "corridor_wait_confirmation", confirmed=False,
                   reason="confirmation_rejected", attempt=rejected)
        if rejected >= MAX_CONFIRMATION_ATTEMPTS:
            return {"confirmed": False, "response": {
                "kind": "answer",
                "text": "I could not match that confirmation to the booking I offered, so "
                        "nothing has been booked. Start the booking again when you are ready.",
                "citations": [],
                "rejected": True,
            }}


def route_after_confirmation(state: TurnState) -> str:
    return "corridor_revalidate" if state.confirmed else "respond"


def revalidate(state: TurnState) -> dict:
    """The rate, the room and the terms are read again at the moment of writing.

    A stale proposal is never written: if anything the guest agreed to has moved, the
    proposal is rebuilt and confirmed again, and an autonomous rung drops to asking.
    """
    proposal = state.proposal
    offer = crs.get_offer(proposal.offer_id)
    passed, failure = deterministic_checks(state, offer, proposal.pay_with)
    if failure:
        node_event(state, "corridor_revalidate", ok=False, reason="validation_failed")
        return {"response": {
            "kind": "answer",
            "text": f"I did not book it: {failure}",
            "citations": [item.item_id for item in state.envelope],
            "failed_validation": True,
        }, "confirmed": False}

    terms, terms_version = cancellation_terms(offer, state.service_date)
    moved = (Decimal(offer["nightly_rate"]) != Decimal(proposal.nightly_rate)
             or f"{terms} ({terms_version})" != proposal.cancellation)
    if moved:
        if state.retries.get("rate_refresh", 0) >= 1:
            node_event(state, "corridor_revalidate", ok=False, reason="rate_unstable")
            return {"response": {
                "kind": "answer",
                "text": "The price for that room keeps changing, so I have not booked it. "
                        "The booking page holds a live rate while you complete it.",
                "citations": [item.item_id for item in state.envelope],
            }, "confirmed": False}
        refreshed = build_proposal(state, offer, proposal.pay_with, passed)
        node_event(state, "corridor_revalidate", ok=False, reason="rate_changed",
                   was=proposal.nightly_rate, now=offer["nightly_rate"])
        return {
            "proposal": refreshed,
            "confirmed": False,
            # An autonomous rung that finds a moved price asks a human rather than paying it.
            "plan": state.plan.model_copy(update={"rung": min(state.plan.rung, 2)}),
            "retries": {**state.retries, "rate_refresh": state.retries.get("rate_refresh", 0) + 1},
        }

    node_event(state, "corridor_revalidate", ok=True, checks=passed)
    return {}


def execute(state: TurnState) -> dict:
    """Idempotent write keyed by proposal id. An unknown outcome is a state, not a crash."""
    proposal = state.proposal
    command = {
        "idempotency_key": proposal.proposal_id,
        "action": proposal.action,
        "guest_ref": proposal.guest_ref,
        "params": {
            "property_id": proposal.property_id,
            "offer_id": proposal.offer_id,
            "room_name": proposal.room_name,
            "rate_plan": proposal.rate_plan,
            "check_in": proposal.check_in,
            "check_out": proposal.check_out,
            "nights": str(proposal.nights),
            "total_price": proposal.total_price,
            "points_applied": proposal.points_applied,
            "cash_due": proposal.cash_due,
        },
    }
    try:
        receipt = sor.book(command)
        node_event(state, "corridor_execute", outcome="known", proposal_id=proposal.proposal_id)
        return {"execution": {"outcome": "known", "receipt": receipt}}
    except sor.SorUnknownOutcome:
        # Never retried blindly: the next node reconciles by the same idempotency key.
        node_event(state, "corridor_execute", outcome="unknown", proposal_id=proposal.proposal_id)
        return {"execution": {"outcome": "unknown", "receipt": None}}


def verify(state: TurnState) -> dict:
    """Read after write, reconciling by idempotency key when the write outcome was unknown."""
    proposal = state.proposal
    receipt = (state.execution or {}).get("receipt")
    if receipt is None:
        receipt = sor.lookup_by_key(proposal.proposal_id)
    if receipt is None:
        node_event(state, "corridor_verify", verified=False, reason="no_receipt")
        return {"response": {
            "kind": "answer",
            "text": "I could not confirm the reservation was created, so I am not giving you a "
                    "confirmation number. The hotel team is reconciling it now.",
            "citations": [],
        }}

    confirmation = receipt["reservation"]["confirmation_number"]
    on_file = gdl.get_reservations(proposal.guest_ref)["payload"]["reservations"]
    verified = confirmation in on_file and on_file[confirmation]["status"] == "confirmed"
    node_event(state, "corridor_verify", verified=verified, receipt_id=receipt["receipt_id"])
    if not verified:
        return {"response": {
            "kind": "answer",
            "text": "The reservation system does not show that booking yet, so I will not claim "
                    "it is done.",
            "citations": [],
        }}
    return {"receipt": {**receipt,
                        "verified_confirmation_number": confirmation,
                        "reconciled": (state.execution or {}).get("outcome") == "unknown",
                        "notified": state.plan.rung >= 3}}
