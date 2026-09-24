"""The gate: AUTO_COMMIT or ESCALATE. Never auto-reject.

Rejection would throw away the one artefact worth keeping — a document a human can fix in
thirty seconds, whose fix becomes a labeled example. So the worst outcome here is a queue
entry, never a discarded upload.
"""

from __future__ import annotations

from statistics import mean
from typing import Any

from ..contracts import DocClass, ExtractedRoom, GateDecision, ValidationReport
from ..registry import validation_rules


def mean_confidence(rooms: list[ExtractedRoom]) -> float:
    scores = [score for room in rooms for score in room.confidence.values()]
    return mean(scores) if scores else 0.0


def gate(
    doc_class: DocClass, rooms: list[ExtractedRoom], report: ValidationReport
) -> GateDecision:
    rules = validation_rules()
    if doc_class.quality_band in rules["force_escalate_quality_bands"]:
        return GateDecision(
            decision="ESCALATE",
            reason=f"quality band {doc_class.quality_band} always goes to a human",
            findings=report.findings,
        )
    if report.escalations:
        rule_names = sorted({f.rule for f in report.escalations})
        return GateDecision(
            decision="ESCALATE",
            reason="validation escalations: " + ", ".join(rule_names),
            findings=report.findings,
        )
    confidence = mean_confidence(rooms)
    floor = rules["auto_commit_min_confidence"]
    if confidence < floor:
        return GateDecision(
            decision="ESCALATE",
            reason=f"mean field confidence {confidence:.2f} below {floor:.2f}",
            findings=report.findings,
        )
    return GateDecision(
        decision="AUTO_COMMIT",
        reason=f"clean validation, mean field confidence {confidence:.2f}",
        findings=report.findings,
    )


def waivable(rule: str) -> bool:
    return bool(validation_rules()["rules"].get(rule, {}).get("waivable", True))


def unresolved_blockers(report_findings: list[dict[str, Any]]) -> list[str]:
    """Rules a human is not allowed to wave through in the review UI (I7)."""
    return sorted({f["rule"] for f in report_findings if not waivable(f["rule"])})
