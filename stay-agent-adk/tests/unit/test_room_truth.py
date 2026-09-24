"""Room truth: the pipeline is allowed to be wrong, but never allowed to write quietly."""

from __future__ import annotations

import pytest

from stay_agent.contracts import ExtractedRoom
from stay_agent.mocks import attribute_store, faults
from stay_agent.room_truth import pipeline, queue
from stay_agent.room_truth.commit import commit
from stay_agent.room_truth.gate_agent import unresolved_blockers, waivable
from stay_agent.room_truth.intake import intake
from stay_agent.room_truth.validate_agent import validate


async def test_pr1_clean_plan_auto_commits_a_new_version():
    before = attribute_store.current_version()
    result = await pipeline.run_job("PR-1")

    assert result["decision"] == "AUTO_COMMIT"
    assert result["receipt"]["attribute_version"] != before
    assert attribute_store.current_version() == result["receipt"]["attribute_version"]
    assert not queue.open_jobs()


async def test_committed_attributes_carry_provenance_and_no_price_fields():
    await pipeline.run_job("PR-1")
    room = attribute_store.rooms("H-201")[0]

    assert room.provenance is not None
    assert room.provenance.source_doc_id == "PR-1"
    assert room.provenance.extractor_version == "stay-extractor-v1"
    forbidden = ("price", "rate", "avail", "occupanc")
    assert not [f for f in room.model_dump() if f.startswith(forbidden)]


async def test_previous_versions_stay_readable_after_a_commit():
    """Append-only: 'what did we believe last week' has to remain answerable."""
    first = attribute_store.current_version()
    await pipeline.run_job("PR-1")
    second = attribute_store.current_version()

    assert first in attribute_store.versions()
    assert attribute_store.rooms("H-201", version=first)
    assert second != first


async def test_pr2_fax_always_escalates_even_when_extraction_looks_fine():
    result = await pipeline.run_job("PR-2")

    assert result["decision"] == "ESCALATE"
    assert "FAX" in result["reason"] or result["quality_band"] == "FAX"
    assert [job["doc_id"] for job in queue.open_jobs()] == ["PR-2"]


async def test_pr3_invented_room_id_fails_reconciliation_and_is_not_written():
    before = attribute_store.current_version()
    result = await pipeline.run_job("PR-3")

    rules = {f["rule"] for f in result["findings"]}
    assert "room_id_reconciliation" in rules
    assert result["decision"] == "ESCALATE"
    assert attribute_store.current_version() == before


async def test_pr4_declared_renovation_commits_a_drifting_attribute_as_a_new_version():
    """Drift is only suspicious when it is undeclared; a renovation is the legitimate case."""
    result = await pipeline.run_job("PR-4")

    assert result["decision"] == "AUTO_COMMIT", result["reason"]
    assert result["receipt"]["attribute_version"] in attribute_store.versions()


async def test_escalation_is_never_a_rejection_and_keeps_the_rooms():
    await pipeline.run_job("PR-3")
    job = queue.open_jobs()[0]

    assert job["status"] == "OPEN"
    assert job["rooms"], "an escalated document keeps its extraction for the reviewer"
    assert job["findings"]


async def test_human_fix_revalidates_commits_with_approver_and_leaves_a_label():
    await pipeline.run_job("PR-3")
    job = queue.open_jobs()[0]
    invented = [
        f["room_id"] for f in job["findings"] if f["rule"] == "room_id_reconciliation"
    ]

    outcome = pipeline.resume_after_review(
        job["job_id"],
        approver="reviewer@example.com",
        fixes=[{"room_id": invented[0], "field": "__delete__", "value": None}],
    )

    assert outcome["decision"] == "COMMITTED"
    assert attribute_store.rooms("H-202")[0].provenance.approver == "reviewer@example.com"
    assert queue.golden_labels(), "every human fix becomes a labeled example"
    assert not queue.open_jobs()


async def test_reviewer_cannot_waive_reconciliation_by_relabelling_the_room():
    """The non-waivable rule is non-waivable from the review UI too (I7)."""
    await pipeline.run_job("PR-3")
    job = queue.open_jobs()[0]

    outcome = pipeline.resume_after_review(
        job["job_id"],
        approver="reviewer@example.com",
        fixes=[{"room_id": "314", "field": "view", "value": "park"}],
    )

    assert outcome["decision"] == "STILL_ESCALATED"
    assert "room_id_reconciliation" in {f["rule"] for f in outcome["findings"]}
    assert not waivable("room_id_reconciliation")


def test_connecting_asymmetry_is_reported_against_both_rooms():
    job = intake("PR-1")
    rooms = [
        ExtractedRoom(room_id="201", floor=2, connecting_with=["202"], confidence={"view": 0.9}),
        ExtractedRoom(room_id="202", floor=2, connecting_with=[], confidence={"view": 0.9}),
    ]

    report = validate(job, rooms)

    assert "connecting_symmetry" in {f.rule for f in report.findings}


def test_floor_prefix_mismatch_is_caught():
    job = intake("PR-1")
    rooms = [ExtractedRoom(room_id="201", floor=5, confidence={"floor": 0.9})]

    report = validate(job, rooms)

    assert "floor_prefix_consistency" in {f.rule for f in report.findings}


@pytest.mark.parametrize(
    "field,value,rule",
    [("area_sqm", 4.0, "area_sanity"), ("distance_to_elevator_m", 900.0, "distance_sanity")],
)
def test_out_of_band_measurements_are_caught(field, value, rule):
    job = intake("PR-1")
    room = ExtractedRoom(room_id="201", floor=2, confidence={field: 0.95})
    setattr(room, field, value)

    report = validate(job, [room])

    assert rule in {f.rule for f in report.findings}


def test_unknown_room_id_is_the_one_rule_a_human_may_not_wave_through():
    job = intake("PR-1")
    report = validate(job, [ExtractedRoom(room_id="999", floor=9, confidence={"view": 0.99})])

    assert unresolved_blockers([f.model_dump() for f in report.findings]) == [
        "room_id_reconciliation"
    ]


async def test_extractor_hallucination_fault_is_caught_by_reconciliation():
    """The drill: the extractor invents a room, and nothing invented reaches the store."""
    faults.set_flag("EXTRACTOR_HALLUCINATE_ROOM", True)
    before = attribute_store.current_version()

    result = await pipeline.run_job("PR-1")

    assert result["decision"] == "ESCALATE"
    assert "room_id_reconciliation" in {f["rule"] for f in result["findings"]}
    assert attribute_store.current_version() == before


def test_commit_writes_only_schema_declared_fields():
    job = intake("PR-1")
    rooms = [ExtractedRoom(room_id="201", floor=2, view="park", confidence={"view": 0.99})]

    receipt = commit(job, rooms)

    written = attribute_store.rooms("H-201", version=receipt.attribute_version)
    assert [room.room_id for room in written] == ["201"]
    assert receipt.rooms_written == 1
