"""The HTTP surface, including the checkpointed pause that survives a process restart."""
import importlib

import pytest
from fastapi.testclient import TestClient

from app.mocks import sor


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CHECKPOINT_PATH", str(tmp_path / "checkpoints.sqlite"))
    main = importlib.reload(importlib.import_module("app.main"))
    with TestClient(main.app) as client:
        client.main = main
        yield client


def _turn(client, utterance, conversation_id="C-API-1"):
    return client.post("/turn", json={
        "conversationId": conversation_id,
        "brandId": "B-LUX",
        "guestRef": "G-7001",
        "utterance": utterance,
        "uiContext": {"check_in": "2026-06-12", "check_out": "2026-06-14"},
    })


def test_the_ui_is_served_and_never_chooses_a_graph(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "Guest assistant" in page.text
    app_js = client.get("/static/app.js").text
    # The UI posts context and renders decisions; it never names a graph or a table row.
    assert 'fetch("/turn"' in app_js and 'fetch("/confirm"' in app_js
    for word in ("graph_id:", "table_row", "posture:", "rung:"):
        assert word not in app_js, f"the UI must not decide {word}"


def test_a_read_turn_answers_with_citations(client):
    body = _turn(client, "family suite near the park, under $300, with breakfast").json()
    assert body["kind"] == "answer"
    assert body["citations"]
    assert body["offers"]


def test_a_second_utterance_cannot_slip_past_a_pending_confirmation(client):
    assert _turn(client, "book offer OF-RIV-FAM").json()["kind"] == "preview"
    blocked = _turn(client, "how many points do I have")
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["kind"] == "pending_confirmation"
    assert sor.receipts() == {}


def test_confirm_rejects_a_bad_nonce_and_accepts_the_real_one(client):
    preview = _turn(client, "book offer OF-RIV-FAM").json()
    proposal_id = preview["proposal"]["proposal_id"]

    bad = client.post("/confirm", json={"conversationId": "C-API-1",
                                        "proposalId": proposal_id, "nonce": "wrong"})
    assert bad.status_code == 409
    assert sor.receipts() == {}

    good = client.post("/confirm", json={"conversationId": "C-API-1",
                                         "proposalId": proposal_id,
                                         "nonce": preview["proposal"]["nonce"]})
    assert good.status_code == 200
    assert good.json()["receipt"]["verified_confirmation_number"]


def test_a_moved_rate_comes_back_as_a_fresh_preview_not_a_refusal(client, monkeypatch):
    """The rebuilt proposal must reach the guest: parking again is not a rejected nonce."""
    monkeypatch.setenv("RATE_CHANGE_ONCE", "1")
    first = _turn(client, "book offer OF-RIV-FAM", "C-API-RATE").json()["proposal"]

    refreshed = client.post("/confirm", json={"conversationId": "C-API-RATE",
                                              "proposalId": first["proposal_id"],
                                              "nonce": first["nonce"]})
    assert refreshed.status_code == 200
    body = refreshed.json()
    assert body["kind"] == "preview"
    assert body["proposal"]["proposal_id"] != first["proposal_id"]
    assert body["proposal"]["nightly_rate"] != first["nightly_rate"]
    assert sor.receipts() == {}, "a moved rate is never paid without a second confirmation"

    booked = client.post("/confirm", json={"conversationId": "C-API-RATE",
                                           "proposalId": body["proposal"]["proposal_id"],
                                           "nonce": body["proposal"]["nonce"]})
    assert booked.status_code == 200
    assert booked.json()["receipt"]["verified_confirmation_number"]
    assert len(sor.receipts()) == 1


def test_confirming_nothing_is_a_409(client):
    assert client.post("/confirm", json={"conversationId": "C-API-EMPTY",
                                         "proposalId": "PRP-x", "nonce": "y"}).status_code == 409


def test_a_pause_survives_the_process_restarting(client, tmp_path, monkeypatch):
    preview = _turn(client, "book offer OF-RIV-FAM", "C-API-KILL").json()
    proposal = preview["proposal"]

    # Same checkpoint file, fresh process: the thread is still parked at the gate.
    restarted = importlib.reload(importlib.import_module("app.main"))
    with TestClient(restarted.app) as after:
        receipt = after.post("/confirm", json={"conversationId": "C-API-KILL",
                                               "proposalId": proposal["proposal_id"],
                                               "nonce": proposal["nonce"]}).json()
    assert receipt["receipt"]["verified_confirmation_number"]
    assert len(sor.receipts()) == 1


def test_the_trace_endpoint_returns_the_nodes_and_the_versions(client):
    _turn(client, "how many points do I have", "C-API-TRACE")
    trace = client.get("/trace/C-API-TRACE").json()
    nodes = [event["node"] for event in trace["events"] if "." not in event["node"]]
    assert nodes[0] == "ingress" and nodes[-1] == "respond"
    assert set(trace["versions"]) >= {"catalog_version", "table_version",
                                      "bundle_version", "freshness_version"}
    assert "@example.com" not in str(trace["events"]), "no guest email may reach the trace"
