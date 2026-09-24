"""Slot dates and tracing: the two things a live `adk web` turn broke that offline tests missed.

The dates matter because ADK serializes session state to JSON between turns, so a `date`
object in a slot ends the conversation with a TypeError rather than a booking.
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from stay_agent import tracing
from stay_agent.concierge import corridor
from stay_agent.concierge.tools import get_live_quote, propose_action, search_rooms
from stay_agent.contracts import SearchSlots

# --- slots survive a JSON round trip ----------------------------------------------


def test_slot_dates_are_iso_strings_not_date_objects():
    slots = SearchSlots(check_in=date(2026, 6, 12), check_out="2026-06-14")

    assert slots.check_in == "2026-06-12"
    assert slots.model_dump()["check_out"] == "2026-06-14"


def test_a_yearless_date_reads_as_the_next_occurrence():
    yesterday = date.today() - timedelta(days=1)

    slots = SearchSlots(check_in=yesterday.strftime("%d %B"))

    assert slots.check_in == yesterday.replace(year=yesterday.year + 1).isoformat()


def test_an_unreadable_date_is_rejected_rather_than_guessed():
    with pytest.raises(ValueError):
        SearchSlots(check_in="whenever the weather is good")


def test_the_corridor_reads_slot_dates_back(tool_context):
    tool_context.state[corridor.SLOTS_KEY] |= SearchSlots(
        check_in="2026-06-12", check_out="2026-06-14"
    ).model_dump()

    assert corridor.stay_dates(tool_context.state) == (date(2026, 6, 12), date(2026, 6, 14))


def test_corridor_state_keys_are_not_adk_temp_keys():
    # `temp:` state is dropped at the end of the invocation that wrote it, and a booking
    # spans three turns: quote, preview, confirm.
    keys = (corridor.SLOTS_KEY, corridor.QUOTES_KEY, corridor.PENDING_KEY, corridor.AUDIT_KEY)
    assert not [key for key in keys if key.startswith("temp:")]


# --- tracing -----------------------------------------------------------------------


@pytest.fixture
def spans():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    previous = trace.get_tracer_provider()
    trace._TRACER_PROVIDER = provider  # the OTel API refuses a second set_tracer_provider
    yield exporter
    trace._TRACER_PROVIDER = previous


def test_tracing_is_off_unless_asked_for(monkeypatch):
    monkeypatch.delenv("STAY_TRACE", raising=False)
    assert tracing.setup_tracing() is False


def test_an_unknown_exporter_fails_loudly(monkeypatch):
    monkeypatch.setattr(tracing, "_configured", False)
    monkeypatch.setenv("STAY_TRACE", "somewhere-else")
    with pytest.raises(ValueError):
        tracing.setup_tracing()


@pytest.mark.parametrize("unusable", ["projects/ihgapp", "136933198325", ""])
def test_an_unusable_project_env_falls_back_to_the_ambient_credentials(monkeypatch, unusable):
    # Agent Runtime exports the project number, which Cloud Trace rejects outright. The
    # fake stands in for the real `google.auth.default`, which prefers these same env
    # vars over the credentials and would otherwise hand the bad value straight back.
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", unusable)

    def ambient(*_args, **_kwargs):
        overrides = [name for name in tracing._PROJECT_ENV_VARS if name in os.environ]
        assert not overrides, f"env would override the credentials: {overrides}"
        return object(), "ihgapp"

    monkeypatch.setattr("google.auth.default", ambient)

    assert tracing._cloud_project() == "ihgapp"
    assert os.environ["GOOGLE_CLOUD_PROJECT"] == unusable


def test_no_resolvable_project_id_fails_rather_than_dropping_spans(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "136933198325")
    monkeypatch.setattr("google.auth.default", lambda *a, **k: (object(), "136933198325"))

    with pytest.raises(RuntimeError, match="no project id"):
        tracing._cloud_project()


def test_search_and_corridor_steps_are_spans_with_the_attributes_an_auditor_wants(
    tool_context, spans
):
    search_rooms(tool_context)
    quote = get_live_quote("704", tool_context)["quote"]
    propose_action("BOOK", tool_context, room_id="704", quote_id=quote["quote_id"])

    recorded = {span.name: span for span in spans.get_finished_spans()}
    assert {"concierge.search_rooms", "concierge.get_live_quote", "corridor.propose"} <= set(
        recorded
    )
    assert recorded["concierge.search_rooms"].attributes["attribute_version"].startswith("av-")
    assert recorded["concierge.get_live_quote"].attributes["status"] == "OK"
    assert recorded["corridor.propose"].attributes["status"] == "PREVIEW"


def test_span_attributes_take_values_otel_would_otherwise_refuse(spans):
    with tracing.span("probe", amount=None, rooms=["704", "706"], when=date(2026, 6, 12)):
        pass

    attributes = spans.get_finished_spans()[0].attributes
    assert "amount" not in attributes
    assert attributes["rooms"] == ("704", "706")
    assert attributes["when"] == "2026-06-12"
