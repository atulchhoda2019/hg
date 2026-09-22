"""The typed-decision port: three implementations, one interface, one versioned choice set."""
import re

import pytest

from app import decider as decider_port
from app import screens
from app.audit import read as read_audit
from app.mocks import decider as implementations
from app.nodes import planner
from tests.conftest import make_state, run_turn

SEARCH = "family suite near the park, under $300, with breakfast"
NAMES = list(implementations.IMPLEMENTATIONS)


@pytest.fixture(autouse=True)
def no_env_override(monkeypatch):
    monkeypatch.delenv("DECIDER", raising=False)


@pytest.mark.parametrize("name", NAMES)
def test_decider_port_every_implementation_answers_every_question_type(name):
    model = decider_port.get(name)
    answers = model.answer(SEARCH, {
        "intent": decider_port.choice_question("Which journey?", planner.choice_set()),
        "writes": decider_port.noul_question("A state change?", screens.STATE_CHANGE_CUES),
        "effort": decider_port.score_question("How specific?", 5),
    })
    assert answers["intent"]["criterion"] == "property_search"
    assert 0.0 <= answers["writes"]["probability_yes"] <= 1.0
    assert 1 <= answers["effort"]["value"] <= 5
    for answer in answers.values():
        assert answer["decider"] == name
        assert answer["catalog_version"] == decider_port.catalog_version()


@pytest.mark.parametrize("name", NAMES)
def test_decider_port_the_three_swap_with_zero_graph_changes(graph, monkeypatch, name):
    monkeypatch.setenv("DECIDER", name)
    result = run_turn(graph, make_state(SEARCH))
    assert result["intent"].name == "property_search"
    assert result["plan"].graph_id == "g_property_search_v1"
    assert result["response"]["kind"] == "answer"
    assert result["intent"].decider == name


def test_the_table_picks_the_decider_and_the_env_overrides_it(monkeypatch):
    assert decider_port.configured("B-LUX") == "slm_incumbent"
    monkeypatch.setenv("DECIDER", "dev_local")
    assert decider_port.configured("B-LUX") == "dev_local"


def test_an_unregistered_decider_is_refused_rather_than_guessed():
    with pytest.raises(ValueError):
        decider_port.get("some_new_model")


def test_catalog_version_pinning_invalidates_calibration_and_blocks_the_high_band(monkeypatch):
    monkeypatch.setattr(implementations, "catalog_version", lambda: "guest-intents-v99")
    intent = planner.stage1_classifier("how many points do i have", "B-LUX")
    assert intent.calibrated is False
    # An uncalibrated pairing cannot act: the ladder asks instead of running the HIGH graph.
    assert intent.band != "HIGH"
    assert planner.stage1_classifier("how many points do i have", "B-LUX").confidence <= \
        implementations.UNCALIBRATED_CEILING


def test_i13_choice_sets_and_cues_are_labels_and_never_carry_facts():
    factish = re.compile(r"\d|\$|available|sold out|per night", re.IGNORECASE)
    for label in [*planner.choice_set(), *screens.STATE_CHANGE_CUES]:
        assert not factish.search(label), label


def test_i14_the_turn_records_which_model_decided_and_on_which_catalog(graph):
    state = make_state(SEARCH)
    run_turn(graph, state)
    planner_events = [e for e in read_audit(state.conversation_id) if e["node"] == "planner"]
    assert planner_events
    event = planner_events[-1]
    assert event["decider"] == "slm_incumbent"
    assert event["decider_version"] == "v3"
    assert event["catalog_version"] == decider_port.catalog_version()
    assert event["calibrated"] is True


def test_the_hosted_decider_redacts_the_state_before_it_leaves_the_boundary():
    decider_port.get("jev_api").answer(
        "book the room, my email is guest@example.com",
        {"intent": decider_port.choice_question("Which journey?", planner.choice_set())})
    assert "guest@example.com" not in implementations.JevApi.last_egress
    assert "<redacted>" in implementations.JevApi.last_egress


def test_the_pre_corridor_screens_are_recorded_and_do_not_decide(graph):
    state = make_state("book offer OF-RIV-FAM")
    run_turn(graph, state)
    screened = [e for e in read_audit(state.conversation_id)
                if e["node"] == "screen.state_change"]
    assert screened and screened[0]["probability_yes"] > 0.5
    # Advisory only: the corridor still ran off the table's posture, not the screen.
    assert any(e["node"] == "corridor_propose" for e in read_audit(state.conversation_id))
