"""The hosted transport for the typed port: same questions, same answers, no new authority.

Every call here is served by a stub: the suite proves the mapping and the failure
behaviour, never the vendor's accuracy.
"""
import io
import json
import urllib.error

import pytest

from app import decider as decider_port
from app import jev, screens
from app.audit import read as read_audit
from app.nodes import planner
from tests.conftest import make_state, run_turn

SEARCH = "family suite near the park, under $300, with breakfast"


def reply(answers: dict, model: str = jev.DEFAULT_MODEL, usage: dict | None = None) -> bytes:
    return json.dumps({"model": model, "answers": answers,
                       "usage": usage or {"input_tokens": 62, "cost_usd": 0.000026}}).encode()


@pytest.fixture
def hosted(monkeypatch):
    """A keyed environment plus a recording stub in place of the network."""
    monkeypatch.setenv("JEV_API_KEY", "jv_live_test")
    sent: list[dict] = []

    typed = {
        "choice": {"type": "choice", "choice": "property_search", "confidence": 0.96,
                   "probabilities": {"property_search": 0.96, "stay_quote": 0.04}},
        "noul": {"type": "noul", "noul": 0.77},
        "score": {"type": "score", "score": 2.0, "probabilities": {}},
    }

    def serve(body: dict) -> bytes:
        return reply({qid: typed[q["type"]] for qid, q in body["questions"].items()})

    responder = {"serve": serve}

    def fake_urlopen(request, timeout=None):
        sent.append({"url": request.full_url, "headers": dict(request.header_items()),
                     "body": json.loads(request.data)})
        return io.BytesIO(responder["serve"](sent[-1]["body"]))

    monkeypatch.setattr(jev.urllib.request, "urlopen", fake_urlopen)
    return {"sent": sent, "responder": responder}


def test_a_key_swaps_the_transport_not_the_name(hosted, monkeypatch):
    model = decider_port.get("jev_api")
    assert isinstance(model, jev.JevApi) and model.name == "jev_api"

    monkeypatch.delenv("JEV_API_KEY")
    assert not isinstance(decider_port.get("jev_api"), jev.JevApi)


def test_the_request_is_the_documented_shape(hosted):
    decider_port.get("jev_api").answer(SEARCH, {
        "intent": decider_port.choice_question("Which journey?", planner.choice_set()),
    })

    call = hosted["sent"][0]
    assert call["url"] == jev.DEFAULT_URL
    assert call["headers"]["Authorization"] == "Bearer jv_live_test"
    assert call["body"]["model"] == jev.DEFAULT_MODEL  # pinned, never jev-latest
    question = call["body"]["questions"]["intent"]
    assert question["type"] == "choice"
    assert list(question["criteria"]) == planner.choice_set()
    assert set(question["criteria"].values()) == {None}  # labels only: no facts in criteria


def test_the_state_is_redacted_before_it_leaves(hosted):
    decider_port.get("jev_api").answer(
        "book it for guest@example.com", {
            "writes": decider_port.noul_question("A state change?", screens.STATE_CHANGE_CUES),
        })

    state = hosted["sent"][0]["body"]["state"]
    assert "guest@example.com" not in state and "<redacted>" in state


def test_a_noul_question_carries_no_local_cues(hosted):
    hosted["responder"]["serve"] = lambda body: reply({"writes": {"type": "noul", "noul": 0.91}})
    answer = decider_port.get("jev_api").answer("book it", {
        "writes": decider_port.noul_question("A state change?", screens.STATE_CHANGE_CUES),
    })["writes"]

    assert "criteria" not in hosted["sent"][0]["body"]["questions"]["writes"]
    assert answer["probability_yes"] == 0.91


def test_a_score_question_sends_ordered_levels(hosted):
    hosted["responder"]["serve"] = lambda body: reply(
        {"effort": {"type": "score", "score": 2.0, "probabilities": {}}})
    answer = decider_port.get("jev_api").answer(SEARCH, {
        "effort": decider_port.score_question("How specific?", 4),
    })["effort"]

    assert len(hosted["sent"][0]["body"]["questions"]["effort"]["criteria"]) == 4
    assert answer["value"] == 2.0


def test_an_unmeasured_model_version_cannot_reach_the_high_band(hosted):
    """The pin names the stand-in, so a live version is uncalibrated until it is measured."""
    answer = decider_port.get("jev_api").answer(SEARCH, {
        "intent": decider_port.choice_question("Which journey?", planner.choice_set()),
    })["intent"]

    assert answer["calibrated"] is False
    assert answer["probabilities"]["property_search"] == decider_port.UNCALIBRATED_CEILING
    assert planner.band_for(answer["probabilities"]["property_search"]) == "MEDIUM"


def test_a_measured_version_keeps_its_probability(hosted, monkeypatch):
    pinned = decider_port.calibration()
    pinned["jev_api"][decider_port.catalog_version()]["measured_version"] = jev.DEFAULT_MODEL
    monkeypatch.setattr(decider_port, "calibration", lambda: pinned)
    monkeypatch.setattr(jev, "calibration", lambda: pinned)

    answer = decider_port.get("jev_api").answer(SEARCH, {
        "intent": decider_port.choice_question("Which journey?", planner.choice_set()),
    })["intent"]
    assert answer["calibrated"] is True and answer["probabilities"]["property_search"] == 0.96


def test_a_dropped_call_is_answered_by_the_incumbent(hosted, monkeypatch):
    def refuse(request, timeout=None):
        raise urllib.error.URLError("connection reset")

    monkeypatch.setattr(jev.urllib.request, "urlopen", refuse)
    answer = decider_port.get("jev_api").answer(SEARCH, {
        "intent": decider_port.choice_question("Which journey?", planner.choice_set()),
    })["intent"]

    assert answer["decider"] == "slm_incumbent"
    assert answer["criterion"] == "property_search"


def test_an_answer_the_port_cannot_read_falls_back_instead_of_raising(hosted):
    hosted["responder"]["serve"] = lambda body: reply({"intent": {"type": "choice"}})
    answer = decider_port.get("jev_api").answer(SEARCH, {
        "intent": decider_port.choice_question("Which journey?", planner.choice_set()),
    })["intent"]

    assert answer["decider"] == "slm_incumbent"


def test_a_choice_without_a_vector_still_answers(hosted):
    hosted["responder"]["serve"] = lambda body: reply(
        {"intent": {"type": "choice", "choice": "stay_quote", "confidence": 0.99}})
    answer = decider_port.get("jev_api").answer(SEARCH, {
        "intent": decider_port.choice_question("Which journey?", planner.choice_set()),
    })["intent"]

    assert answer["criterion"] == "stay_quote"
    assert answer["probabilities"]["stay_quote"] == decider_port.UNCALIBRATED_CEILING


def test_a_retryable_status_is_retried_and_a_refusal_is_not(hosted, monkeypatch):
    calls = {"n": 0}

    def fail(status):
        def raise_it(request, timeout=None):
            calls["n"] += 1
            raise urllib.error.HTTPError(jev.DEFAULT_URL, status, "no", {}, None)
        return raise_it

    monkeypatch.setattr(jev.urllib.request, "urlopen", fail(502))
    with pytest.raises(jev.JevUnavailable):
        jev._post({"state": "x", "questions": {}})
    assert calls["n"] == jev.ATTEMPTS

    calls["n"] = 0
    monkeypatch.setattr(jev.urllib.request, "urlopen", fail(401))
    with pytest.raises(jev.JevUnavailable):
        jev._post({"state": "x", "questions": {}})
    assert calls["n"] == 1


def test_the_vendors_own_reason_survives_the_fallback(hosted, monkeypatch):
    """'insufficient credits' and 'bad key' need different fixes, so keep the words."""
    body = io.BytesIO(b'{"error":"Insufficient credits - top up to continue."}')
    monkeypatch.setattr(jev.urllib.request, "urlopen", lambda request, timeout=None: (
        _ for _ in ()).throw(urllib.error.HTTPError(jev.DEFAULT_URL, 402, "no", {}, body)))

    model = decider_port.get("jev_api")
    model.answer(SEARCH, {
        "intent": decider_port.choice_question("Which journey?", planner.choice_set()),
    })
    assert model.last_error == "HTTP 402 Insufficient credits - top up to continue."


def test_the_turn_records_which_model_actually_decided(graph, hosted, monkeypatch):
    monkeypatch.setenv("DECIDER", "jev_api")
    state = make_state("how much for the family suite for three nights")
    hosted["responder"]["serve"] = lambda body: reply(
        {"intent": {"type": "choice", "choice": "stay_quote", "confidence": 0.93,
                    "probabilities": {"stay_quote": 0.93, "property_search": 0.07}}})

    run_turn(graph, state)
    planned = [e for e in read_audit(state.conversation_id) if e["node"] == "planner"][0]
    assert planned["decider"] == "jev_api"
    assert planned["decider_version"] == jev.DEFAULT_MODEL


def test_a_fallback_says_so_in_the_trace(graph, hosted, monkeypatch):
    monkeypatch.setenv("DECIDER", "jev_api")
    monkeypatch.setattr(jev.urllib.request, "urlopen",
                        lambda request, timeout=None: (_ for _ in ()).throw(TimeoutError()))
    state = make_state("how much for the family suite for three nights")

    result = run_turn(graph, state)
    planned = [e for e in read_audit(state.conversation_id) if e["node"] == "planner"][0]
    assert planned["decider"] == "slm_incumbent"
    incumbent = planner.stage1_classifier(state.utterance)  # the turn still answers
    assert result["intent"].name == incumbent.name
