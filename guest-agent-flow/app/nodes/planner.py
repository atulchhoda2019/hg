"""Stage 0 rules, stage 1 classifier mock, stage 2 decision table, stage 3 ambiguity ladder.

Slots are typed and canonical here ("under $300" -> max_nightly_rate "300", "family
suite" -> room_type "family_suite"), because everything downstream - the availability
predicate, the cache key, the proposal - compares values, not phrasings.
"""
import json
import os
import pathlib
import re
from typing import Optional

from app import decider as decider_port
from app.audit import emit, node_event
from app.registry import catalog, decision_table, intent_names
from app.state import Intent, PlanDecision, TurnState

WORD_NUMBERS = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
    "seven": "7", "eight": "8", "nine": "9", "ten": "10",
}

ROOM_TYPES = [
    (r"family\s+suites?", "family_suite"),
    (r"two\s+bedroom\s+suites?", "family_suite"),
    (r"king\s+rooms?", "king"),
    (r"(two|double)\s+queens?", "double_queen"),
]

LANDMARKS = ["park", "riverwalk", "lake", "harbor", "airport", "station", "downtown"]
MARKETS = ["chicago"]

STAY_INTENTS = {"property_search", "stay_quote", "booking_create"}


def label_for(intent: str) -> str:
    return intent.replace("_", " ")


def _digits(text: str) -> str:
    for word, digit in WORD_NUMBERS.items():
        text = re.sub(rf"\b{word}\b", digit, text)
    return text


def extract_slots(intent: str, utterance: str) -> dict[str, str]:
    text = _digits(utterance.lower())
    slots: dict[str, str] = {}

    offer = re.search(r"\b(OF-[A-Z0-9-]+)\b", utterance, flags=re.IGNORECASE)
    if offer:
        slots["offer_id"] = offer.group(1).upper()

    if intent not in STAY_INTENTS:
        return slots

    ceiling = re.search(r"(?:under|below|less than|up to|max(?:imum)?)\s*\$?\s*(\d{2,5})", text) \
        or re.search(r"\$\s*(\d{2,5})\s*(?:a|per)\s*night", text)
    if ceiling:
        slots["max_nightly_rate"] = ceiling.group(1)

    for pattern, room_type in ROOM_TYPES:
        if re.search(pattern, text):
            slots["room_type"] = room_type
            break

    if "breakfast" in text:
        slots["amenities"] = "breakfast"

    landmark = re.search(r"(?:near|close to|by|next to|overlooking)\s+(?:the\s+)?(\w+)", text)
    if landmark and landmark.group(1) in LANDMARKS:
        slots["landmark"] = landmark.group(1)

    party = re.search(r"(?:family of|party of|for|sleeps|sleeping)\s+(\d{1,2})\b", text) \
        or re.search(r"\b(\d{1,2})\s+(?:guests|adults|people|of us)\b", text)
    if party:
        slots["party_size"] = party.group(1)

    for market in MARKETS:
        if market in text:
            slots["market"] = market

    if re.search(r"points\s+(?:and|\+|plus)\s+cash|cash\s+(?:and|\+|plus)\s+points", text):
        slots["pay_with"] = "points_cash"
    elif re.search(r"\b(with|in|using)\s+points\b|\bpoints\b", text) and intent != "property_search":
        slots["pay_with"] = "points"

    return slots


def stage0_rules(utterance: str) -> Optional[Intent]:
    for rule in catalog()["rules"]:
        match = re.match(rule["pattern"], utterance, flags=re.IGNORECASE)
        if match:
            slots = {**extract_slots(rule["intent"], utterance), **{
                k: v for k, v in (match.groupdict() or {}).items() if v
            }}
            return Intent(name=rule["intent"], confidence=1.0, band="HIGH", slots=slots,
                          alternates=[], source="rule")
    return None


def band_for(confidence: float) -> str:
    for band, (low, high) in catalog()["bands"].items():
        if low <= confidence < high:
            return band
    return "LOW"


def choice_set() -> list[str]:
    """The versioned choice set: every in-scope catalog label, in catalog order.

    Order and membership are part of the model input (I8), so this is built from the
    catalog file alone and never assembled ad hoc at the call site.
    """
    return [spec["name"] for spec in catalog()["intents"] if spec["name"] != "__out_of_scope__"]


def stage1_classifier(utterance: str, brand_id: str = "") -> Intent:
    """One typed `choice` over the closed catalog. Never invents a label.

    The band reads off the calibrated probability, and the clarifying options are the
    top three of the same probability vector, so what the ladder asks and what the
    model believed cannot drift apart.
    """
    model = decider_port.for_brand(brand_id)
    answer = model.answer(utterance, {
        "intent": decider_port.choice_question(
            "Which guest journey is this utterance asking for?", choice_set()),
    })["intent"]

    probabilities: dict[str, float] = answer["probabilities"]
    if not answer["criterion"]:
        return Intent(name="__out_of_scope__", confidence=0.2, band="LOW", slots={},
                      alternates=[], decider=model.name, decider_version=model.version,
                      calibrated=answer["calibrated"])

    ranked = sorted(probabilities.items(), key=lambda row: (-row[1], row[0]))
    confidence = round(probabilities[answer["criterion"]], 2)
    name = answer["criterion"] if answer["criterion"] in intent_names() else "__out_of_scope__"
    return Intent(
        name=name,
        confidence=confidence,
        band=band_for(confidence),
        slots=extract_slots(name, utterance),
        alternates=[n for n, _ in ranked[1:3]],
        decider=model.name,
        decider_version=model.version,
        calibrated=answer["calibrated"],
    )


def resolve_clarification(state: TurnState) -> Optional[Intent]:
    """A clarify answer re-enters the planner exactly once and resolves by exact option match."""
    if state.clarify_rounds < 1:
        return None
    offered = state.ui_context.get("clarify_options") or []
    answer = state.utterance.strip().lower()
    for option in offered:
        if answer == option["label"].lower() or answer == option["intent"].lower():
            slots = {
                **extract_slots(option["intent"], state.ui_context.get("clarify_utterance", "")),
                **extract_slots(option["intent"], state.utterance),
            }
            return Intent(name=option["intent"], confidence=1.0, band="HIGH", slots=slots,
                          alternates=[], source="rule")
    return None


def stage2_table(intent: Intent) -> dict:
    for row in decision_table()["rows"]:
        if row["intent"] not in ("any", intent.name):
            continue
        if row["band"] not in ("any", intent.band):
            continue
        return row
    raise RuntimeError(f"no decision table row for {intent.name}/{intent.band}: the table must be total")


def fallback_queue_path() -> pathlib.Path:
    default = pathlib.Path(__file__).parent.parent.parent / "var" / "fallback_queue.jsonl"
    path = pathlib.Path(os.environ.get("FALLBACK_QUEUE", default))
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def log_fallback(state: TurnState, intent: Intent) -> None:
    with fallback_queue_path().open("a") as fh:
        fh.write(json.dumps({
            "conversation_id": state.conversation_id,
            "turn_id": state.turn_id,
            "brand_id": state.brand_id,
            "utterance": state.utterance,
            "top_guesses": [intent.name, *intent.alternates],
            "confidence": intent.confidence,
        }) + "\n")


def run(state: TurnState) -> dict:
    intent = resolve_clarification(state) or stage0_rules(state.utterance) \
        or stage1_classifier(state.utterance, state.brand_id)

    # Stage 3: a second unresolved ambiguity is not asked again, it drops to LOW.
    if intent.band == "MEDIUM" and state.clarify_rounds >= 1:
        intent = intent.model_copy(update={"band": "LOW"})

    row = stage2_table(intent)
    plan = PlanDecision(
        graph_id=row["graph"],
        table_row_id=row["row"],
        rung=row.get("rung_max", 0),
        budgets=row.get("budgets", {}),
        required_evidence=row.get("evidence", []),
        posture=row.get("posture", "READ"),
        requires_capability=row.get("requires_capability"),
        clarify=bool(row.get("clarify")),
        handoff=bool(row.get("handoff")),
    )

    updates: dict = {"intent": intent, "plan": plan}
    if intent.band == "LOW":
        log_fallback(state, intent)
        updates["response"] = {
            "kind": "handoff",
            "summary": "I could not place that request confidently, so I am handing it to the "
                       "hotel team. Classic search is still available beside this assistant.",
            "top_intents": [intent.name, *intent.alternates],
            "transcript": [state.utterance],
            "slots": intent.slots,
            "row": row["row"],
        }
    elif plan.clarify:
        options = [{"intent": name, "label": label_for(name)}
                   for name in [intent.name, *intent.alternates][:3]]
        updates["response"] = {
            "kind": "clarify",
            "question": "Which of these did you mean?",
            "options": options,
            "row": row["row"],
        }
        updates["clarify_rounds"] = state.clarify_rounds + 1

    node_event(state, "planner", source=intent.source, confidence=intent.confidence,
               row=row["row"], graph_id=plan.graph_id, posture=plan.posture, slots=intent.slots,
               decider=intent.decider, decider_version=intent.decider_version,
               calibrated=intent.calibrated, catalog_version=decider_port.catalog_version())
    emit({"conversation_id": state.conversation_id, "node": "planner.trace_metadata",
          "graph_id": plan.graph_id, "table_row_id": plan.table_row_id})
    return updates
