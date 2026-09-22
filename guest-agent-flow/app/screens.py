"""Pre-corridor screens: cheap typed second opinions, never the decision.

Two yes/no questions run through the decider port before a write is proposed and again
before it is written: does this utterance actually ask for a state change, and does the
proposal the guest confirmed still match what they asked for. The deterministic checks
in `gate_plan` and `corridor` decide either way; a screen that disagrees is recorded in
the trace so a disagreement rate can be measured before anyone proposes acting on it.
"""
import re
from typing import Any

from app import decider as decider_port
from app.audit import node_event

STATE_CHANGE_CUES = ("book", "reserve", "confirm", "cancel", "change my", "move my", "rebook")
DISSENT_THRESHOLD = 0.5

_FACTISH = re.compile(r"\d")


def _labels(*values: str) -> list[str]:
    """Cues are labels only: anything carrying digits could be a price or a count (I8)."""
    words: list[str] = []
    for value in values:
        for token in re.split(r"[\s_-]+", (value or "").lower()):
            if token and not _FACTISH.search(token) and token not in words:
                words.append(token)
    return words


def _ask(state: Any, question: dict, screen: str) -> float:
    model = decider_port.for_brand(state.brand_id)
    answer = model.answer(state.utterance, {screen: question})[screen]
    probability = float(answer["probability_yes"])
    node_event(state, f"screen.{screen}", probability_yes=probability,
               decider=answer["decider"], decider_version=answer["decider_version"],
               calibrated=answer["calibrated"], catalog_version=answer["catalog_version"])
    return probability


def asks_for_a_state_change(state: Any) -> float:
    return _ask(state, decider_port.noul_question(
        "Is this utterance asking to create or change a reservation?",
        STATE_CHANGE_CUES), "state_change")


def confirmation_matches_ask(state: Any, proposal: Any) -> float:
    return _ask(state, decider_port.noul_question(
        "Does the confirmed proposal match what this utterance asked for?",
        _labels(proposal.offer_id, proposal.room_name, proposal.rate_plan)),
        "confirmation_match")
