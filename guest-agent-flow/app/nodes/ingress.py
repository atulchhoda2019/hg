"""Canonicalize the request and attach trusted context.

Identity arrives as a claim (an email in the mock) and is exchanged here for an opaque
guest ref; the brand, the property cell and the stay window come from the session, not
from the guest's sentence. The email never enters TurnState, a prompt or a tool call.
"""
from datetime import datetime, timezone

from app.audit import node_event
from app.mocks import gdl
from app.query import stay_window
from app.state import TurnState


def resolve_guest(claim: str) -> str:
    """Exchange a verified identity claim for a guest ref."""
    if claim in gdl._load():  # already a ref
        return claim
    for ref, record in gdl._load().items():
        if record["email"].lower() == claim.lower():
            return ref
    raise KeyError("unresolvable identity claim")


def fresh_turn() -> dict:
    """Clear everything the previous turn left on the thread.

    The checkpointer keys the thread on the conversation, so without this a second turn
    inherits the first turn's response, envelope and proposal.
    """
    return {
        "intent": None,
        "plan": None,
        "offers": [],
        "evidence_items": [],
        "fact_items": [],
        "calc_items": [],
        "envelope": [],
        "not_applicable": [],
        "abstain_reason": None,
        "draft": None,
        "validation": None,
        "proposal": None,
        "confirmed": False,
        "execution": None,
        "receipt": None,
        "response": None,
        "retries": {},
    }


def run(state: TurnState) -> dict:
    check_in, check_out = stay_window(state)
    utterance = " ".join(state.utterance.split())
    node_event(
        state, "ingress",
        check_in=check_in, check_out=check_out, utterance_len=len(utterance),
        intent=None, band=None, row=None, graph_id=None,
    )
    return {
        **fresh_turn(),
        # The stay date is the service date: content is read as it will be on arrival.
        "service_date": check_in,
        "check_in": check_in,
        "check_out": check_out,
        "utterance": utterance,
        "turn_started_at": datetime.now(timezone.utc).isoformat(),
    }
