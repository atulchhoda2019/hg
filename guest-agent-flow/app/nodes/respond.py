"""Final answer, scripted fallback, draft, or handoff. Never a fact that is not in state."""
from app.audit import node_event
from app.state import TurnState

SCRIPTED = (
    "I could not put together an answer I can stand behind for that one. "
    "The hotel team can take it from here, and classic search is still one click away."
)


def run(state: TurnState) -> dict:
    if state.receipt is not None:
        response = {"kind": "receipt", "receipt": state.receipt}
        node_event(state, "respond", kind="receipt")
        return {"response": response}

    if state.response is not None:
        node_event(state, "respond", kind=state.response.get("kind"), preexisting=True)
        return {}

    if state.plan and state.plan.posture == "DRAFT":
        proposal = state.proposal
        response = {
            "kind": "draft",
            "form": {
                "action": "RoomBooking",
                "requested": proposal.model_dump() if proposal else state.intent.slots,
                "instructions": "This brand completes bookings on its own booking page; "
                                "everything above is filled in for you.",
            },
            "citations": (state.validation or {}).get("citations", []),
        }
    elif state.validation and state.validation.get("passed"):
        response = {
            "kind": "answer",
            "text": state.draft,
            "citations": state.validation.get("citations", []),
        }
        if state.offers:
            response["offers"] = state.offers
        if state.validation.get("disclosures"):
            response["disclosures"] = state.validation["disclosures"]
        if state.plan and state.plan.handoff:
            # A change or a service request is shown, then handed to a human to action.
            response["handoff"] = True
            response["next_step"] = ("I can show this, but changing or cancelling it is done "
                                     "by the hotel team; I am passing this along now.")
    else:
        response = {
            "kind": "answer",
            "text": SCRIPTED,
            "citations": [],
            "scripted": True,
            "reasons": (state.validation or {}).get("reasons", []),
        }

    node_event(state, "respond", kind=response["kind"])
    return {"response": response}
