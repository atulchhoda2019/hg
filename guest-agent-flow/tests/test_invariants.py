"""Governance invariants: isolation, freshness, claim support, PII, autonomy ceilings."""
import pytest

from app.nodes import gate_output
from app.registry import bundles, decision_table
from tests.conftest import make_state, run_turn

SEARCH = "family suite near the park, under $300, with breakfast"


def test_i1_a_brand_never_sees_another_brands_property(graph):
    result = run_turn(graph, make_state(SEARCH, guest_ref="G-8001", brand_id="B-HARBOR"))
    offers = result["response"]["offers"]
    assert {offer["property_id"] for offer in offers} == {"PR-HARBORSIDE"}
    assert all(item.source.count("PSG-HAR") or "POINTS" in item.source
               for item in result["envelope"] if item.evidence_type == "property_content")


def test_i1b_a_membership_from_another_brand_is_refused(graph):
    result = run_turn(graph, make_state(SEARCH, guest_ref="G-8001", brand_id="B-LUX"))
    assert result["response"]["kind"] == "handoff"
    assert result["response"]["reason"] == "brand_mismatch"


def test_i2_no_advisory_row_may_carry_an_autonomy_rung():
    for row in decision_table()["rows"]:
        if row["posture"] == "READ":
            assert row["rung_max"] == 0, row["row"]


def test_i3_the_brand_bundle_caps_the_tables_rung(graph):
    # B-CLASSIC bought AI search only: booking is refused even though the table allows rung 4.
    result = run_turn(graph, make_state("book offer OF-CLA-STD", guest_ref="G-9501",
                                        brand_id="B-CLASSIC"))
    assert result["response"]["kind"] == "handoff"
    assert result["response"]["reason"] in ("rung_zero", "capability_not_enabled")
    assert bundles()["brands"]["B-CLASSIC"]["rungs"]["booking_create"] == 0


def test_i3b_a_draft_only_brand_fills_the_form_and_stops(graph):
    result = run_turn(graph, make_state("book offer OF-EXP-STD", guest_ref="G-9001",
                                        brand_id="B-EXPRESS"))
    assert result["response"]["kind"] == "draft"
    assert result["receipt"] is None
    assert result["response"]["form"]["requested"]["offer_id"] == "OF-EXP-STD"


def test_i3c_an_execute_and_notify_brand_books_without_pausing(graph):
    result = run_turn(graph, make_state("book offer OF-HAR-FAM", guest_ref="G-8001",
                                        brand_id="B-HARBOR"))
    receipt = result["response"]["receipt"]
    assert result["response"]["kind"] == "receipt"
    assert receipt["verified_confirmation_number"]
    assert receipt["notified"] is True, "rung 3 books but must tell the guest it did"


def test_i4_content_that_expired_before_the_stay_is_never_quoted(graph):
    result = run_turn(graph, make_state("what is the cancellation policy"))
    quoted = {item.payload["passage_id"] for item in result["envelope"]
              if item.evidence_type == "property_content"}
    assert "PSG-RIV-SEASON-2025" not in quoted


def test_i5_an_uncited_claim_is_cut(graph, monkeypatch):
    monkeypatch.setenv("MODEL_UNCITED_CLAIM", "1")
    result = run_turn(graph, make_state("how many points do I have"))
    assert "rooftop" not in result["response"]["text"], "the invented rate must never reach the guest"
    assert result["retries"]["reason"] == 2, "the gate rejected it and the composer ran again"


def test_i5b_a_composer_that_keeps_inventing_is_handed_off(graph, monkeypatch):
    monkeypatch.setenv("MODEL_UNCITED_CLAIM", "always")
    result = run_turn(graph, make_state("how many points do I have"))
    assert result["response"]["scripted"] is True
    assert any("uncited" in reason for reason in result["response"]["reasons"])


def test_i6_a_number_the_evidence_does_not_state_is_rejected(graph):
    state = run_turn(graph, make_state("how many points do I have"))
    turn = state["__interrupt__"] if "__interrupt__" in state else None
    assert turn is None
    fake = make_state("how many points do I have")
    fake.envelope = state["envelope"]
    fake.draft = "Your balance is 999999 points [ev-1]."
    verdict = gate_output.validate(fake)
    assert not verdict["passed"]
    assert any("unsupported numbers" in reason for reason in verdict["reasons"])


def test_i7_an_email_address_in_a_draft_is_rejected(graph):
    state = run_turn(graph, make_state("how many points do I have"))
    fake = make_state("how many points do I have")
    fake.envelope = state["envelope"]
    fake.draft = "I will send it to rowan.avery@example.com [ev-1]."
    assert "pii_in_output" in gate_output.validate(fake)["reasons"]


def test_i8_a_low_confidence_turn_is_handed_off_and_queued(graph, tmp_path, monkeypatch):
    queue = tmp_path / "fallback.jsonl"
    monkeypatch.setenv("FALLBACK_QUEUE", str(queue))
    result = run_turn(graph, make_state("my trip stuff is wrong"))
    assert result["response"]["kind"] == "handoff"
    assert result["envelope"] == [], "a LOW turn reads nothing"
    assert queue.read_text().strip()


def test_i9_a_near_tie_asks_once_then_drops_to_a_human(graph):
    first = run_turn(graph, make_state("find a hotel, price for it"))
    assert first["response"]["kind"] == "clarify"
    second = run_turn(graph, make_state("neither of those", clarify_rounds=1))
    assert second["response"]["kind"] == "handoff"


@pytest.mark.parametrize("utterance", ["move my reservation to next week",
                                       "cancel my reservation",
                                       "I need extra towels"])
def test_i10_change_journeys_read_then_hand_to_a_human(graph, utterance):
    result = run_turn(graph, make_state(utterance))
    assert result["plan"].posture == "READ"
    assert result["response"]["handoff"] is True
    assert result["receipt"] is None


def test_i11_the_frontier_never_gets_a_tool_that_can_change_anything():
    from app import escalation
    names = escalation.TOOL_NAMES
    assert set(names) == {"list_evidence", "read_evidence_item"}
    forbidden = ("book", "hold", "confirm", "execute", "cancel", "write", "pay")
    assert not [n for n in names if any(word in n for word in forbidden)]
