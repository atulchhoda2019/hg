"""The four callbacks, the registry, and the decider port: controls, tested as controls."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from stay_agent.concierge import callbacks
from stay_agent.concierge.tools import get_live_quote
from stay_agent.deciders import build, decide, decider_for
from stay_agent.mocks import faults
from stay_agent.registry import SchemaViolation, attribute_schema, is_registered, tool_spec


@dataclass
class FakeTool:
    name: str


def model_reply(text: str) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=text)]))


# --- price guard (I1) -------------------------------------------------------------


def test_price_guard_lets_through_an_amount_the_session_actually_quoted(tool_context):
    quote = get_live_quote("704", tool_context)["quote"]

    reply = model_reply(f"Room 704 is USD {float(quote['nightly']):.2f} per night.")

    assert callbacks.price_guard(tool_context, reply) is None


def test_price_guard_replaces_an_invented_amount(tool_context):
    get_live_quote("704", tool_context)

    blocked = callbacks.price_guard(tool_context, model_reply("It's about $99.00 a night."))

    assert blocked is not None
    assert callbacks.PRICE_GUARD_REPLY in blocked.content.parts[0].text
    assert blocked.custom_metadata["blocked_amounts"] == ["99.00"]


def test_price_guard_blocks_any_amount_when_nothing_was_quoted(tool_context):
    blocked = callbacks.price_guard(tool_context, model_reply("Totals come to $1,499.00."))

    assert blocked is not None
    assert tool_context.state["temp:audit"][-1]["event"] == "price_guard.blocked"


def test_price_guard_catches_the_hallucination_drill(tool_context):
    """MODEL_PRICE_HALLUCINATION appends a fake total; the guard must eat the whole reply."""
    get_live_quote("704", tool_context)
    faults.set_flag("MODEL_PRICE_HALLUCINATION", True)

    blocked = callbacks.price_guard(tool_context, model_reply("Happy to help."))

    assert blocked is not None and "1,499.00" in str(blocked.custom_metadata["blocked_amounts"])


def test_a_reply_with_no_money_in_it_is_left_alone(tool_context):
    assert callbacks.price_guard(tool_context, model_reply("Room 704 faces the park.")) is None


# --- registry gate (I3, I4) -------------------------------------------------------


def test_unregistered_tool_is_refused(tool_context):
    refusal = callbacks.registry_gate(FakeTool("set_rate"), {"amount": 50}, tool_context)

    assert refusal["status"] == "REFUSED"
    assert "not in the action registry" in refusal["reason"]


def test_unknown_argument_is_refused(tool_context):
    refusal = callbacks.registry_gate(
        FakeTool("get_live_quote"), {"room_id": "704", "discount": "50%"}, tool_context
    )

    assert refusal["status"] == "REFUSED" and "discount" in refusal["reason"]


def test_bad_enum_value_is_refused(tool_context):
    refusal = callbacks.registry_gate(
        FakeTool("propose_action"), {"kind": "REFUND_EVERYTHING"}, tool_context
    )

    assert refusal["status"] == "REFUSED" and "must be one of" in refusal["reason"]


def test_missing_required_argument_is_refused(tool_context):
    refusal = callbacks.registry_gate(FakeTool("get_live_quote"), {}, tool_context)

    assert refusal["status"] == "REFUSED" and "needs room_id" in refusal["reason"]


def test_wrong_type_is_refused(tool_context):
    refusal = callbacks.registry_gate(FakeTool("search_rooms"), {"limit": "lots"}, tool_context)

    assert refusal["status"] == "REFUSED" and "must be int" in refusal["reason"]


def test_confirm_without_a_pending_proposal_never_reaches_the_tool(tool_context):
    """The framework's own confirmation UX is not the control; this is (I4)."""
    refusal = callbacks.registry_gate(FakeTool("confirm_action"), {"nonce": "x"}, tool_context)

    assert refusal["status"] == "REFUSED" and "no pending proposal" in refusal["reason"]


def test_a_well_formed_registered_call_passes(tool_context):
    assert callbacks.registry_gate(FakeTool("search_rooms"), {"limit": 5}, tool_context) is None


# --- redaction, grounding, audit --------------------------------------------------


def test_card_numbers_are_redacted_before_the_model_sees_them(tool_context):
    request = LlmRequest(
        contents=[
            types.Content(role="user", parts=[types.Part(text="my card is 4111 1111 1111 1111")])
        ],
        config=types.GenerateContentConfig(system_instruction="be helpful"),
    )

    callbacks.redact_and_ground(tool_context, request)

    assert "4111" not in request.contents[0].parts[0].text
    assert "[redacted]" in request.contents[0].parts[0].text


def test_the_attribute_version_is_pinned_into_the_system_instruction(tool_context):
    request = LlmRequest(config=types.GenerateContentConfig(system_instruction="be helpful"))

    callbacks.redact_and_ground(tool_context, request)

    version = tool_context.state["app:attribute_version"]
    assert version in request.config.system_instruction


def test_the_audit_trail_hashes_arguments_and_records_receipts(tool_context):
    callbacks.audit_and_receipt(
        FakeTool("confirm_action"),
        {"nonce": "super-secret-nonce"},
        tool_context,
        {"status": "COMMITTED", "receipt": {"receipt_id": "RC-1", "verified": True}},
    )

    entry = tool_context.state["temp:audit"][-1]
    assert entry["tool"] == "confirm_action" and entry["receipt_id"] == "RC-1"
    assert "super-secret-nonce" not in str(entry)
    assert len(entry["args_sha256"]) == 16


# --- registry invariants ----------------------------------------------------------


def test_every_concierge_tool_is_a_registry_row():
    for name in ("search_rooms", "get_live_quote", "explain_room", "propose_action"):
        assert is_registered(name) and tool_spec(name)["purpose"]


def test_the_attribute_schema_cannot_hold_a_price_shaped_field(monkeypatch):
    schema = attribute_schema()
    assert not [f for f in schema["fields"] if f.startswith(tuple(schema["forbidden_field_prefixes"]))]

    import stay_agent.registry as registry

    registry.attribute_schema.cache_clear()
    monkeypatch.setattr(
        registry,
        "_load",
        lambda _: {"fields": {"rate_plan": {}}, "forbidden_field_prefixes": ["rate"]},
    )
    with pytest.raises(SchemaViolation):
        registry.attribute_schema()
    registry.attribute_schema.cache_clear()


# --- decider port (I8) ------------------------------------------------------------


async def test_the_table_chooses_the_decider_and_the_env_can_pin_it():
    assert decider_for("ambiguity_band", mode="table") == ("gemma", "rules")
    assert decider_for("upsell_eligibility", mode="table") == ("rules", "rules")
    assert decider_for("ambiguity_band", mode="rules_only") == ("rules", "rules")


async def test_rules_decider_answers_every_decision_type_offline():
    decision = await decide(
        "amenity_intent_route", {"utterance": "cancel my booking"}, mode="rules_only"
    )

    assert decision.choice == "CANCEL" and decision.decider == "rules"


async def test_an_unavailable_model_decider_degrades_to_rules_not_to_a_guess(monkeypatch):
    """Offline there is no key, so the gemma row must still produce a governed answer."""
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    decision = await decide(
        "ambiguity_band",
        {"utterance": "a room", "slots": {}},
        mode="table",
    )

    assert decision.choice in {"HIGH", "MEDIUM", "LOW"}
    assert decision.decider == "rules"


async def test_the_rules_decider_is_deterministic():
    state = {"utterance": "how much is room 704?", "slots": {}}
    first = await build("rules").decide("q", ["SEARCH", "QUOTE", "BOOK"], state)
    second = await build("rules").decide("q", ["SEARCH", "QUOTE", "BOOK"], state)

    assert first.choice == second.choice == "QUOTE"
