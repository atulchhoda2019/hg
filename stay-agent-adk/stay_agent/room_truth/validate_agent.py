"""Validation: deterministic, no model, rules from `validation_rules.yaml`.

Reconciliation against CRS inventory is the rule that cannot be waived (I7): if the
extractor's room ids are not exactly the inventory's room ids for that property and floor,
the job escalates, no matter how confident anything was.
"""

from __future__ import annotations

from typing import Any

from ..contracts import ExtractedRoom, Finding, ValidationReport
from ..mocks import attribute_store, inventory
from ..registry import attribute_schema, validation_rules


def _severity(rule: str) -> str:
    return validation_rules()["rules"][rule]["severity"]


def _reconcile(job: dict[str, Any], rooms: list[ExtractedRoom]) -> list[Finding]:
    findings: list[Finding] = []
    property_id = job["property_id"]
    by_floor: dict[int, set[str]] = {}
    for room in rooms:
        by_floor.setdefault(room.floor, set()).add(room.room_id)
    for floor, extracted in sorted(by_floor.items()):
        expected = set(inventory.room_ids(property_id, floor))
        for room_id in sorted(extracted - expected):
            findings.append(
                Finding(
                    rule="room_id_reconciliation",
                    severity="ESCALATE",
                    room_id=room_id,
                    detail=f"{room_id} is not in CRS inventory for {property_id} floor {floor}",
                )
            )
        for room_id in sorted(expected - extracted):
            findings.append(
                Finding(
                    rule="room_id_reconciliation",
                    severity="ESCALATE",
                    room_id=room_id,
                    detail=f"{room_id} exists in CRS inventory but was not extracted",
                )
            )
    return findings


def _symmetry(rooms: list[ExtractedRoom]) -> list[Finding]:
    links = {room.room_id: set(room.connecting_with) for room in rooms}
    findings: list[Finding] = []
    for room_id, partners in links.items():
        for partner in sorted(partners):
            if room_id not in links.get(partner, set()):
                findings.append(
                    Finding(
                        rule="connecting_symmetry",
                        severity=_severity("connecting_symmetry"),
                        room_id=room_id,
                        detail=f"{room_id} connects with {partner} but not the other way round",
                    )
                )
    return findings


def _floor_prefix(rooms: list[ExtractedRoom]) -> list[Finding]:
    return [
        Finding(
            rule="floor_prefix_consistency",
            severity=_severity("floor_prefix_consistency"),
            room_id=room.room_id,
            detail=f"{room.room_id} was extracted from floor {room.floor}",
        )
        for room in rooms
        if not room.room_id.startswith(str(room.floor))
    ]


def _bounds(rooms: list[ExtractedRoom]) -> list[Finding]:
    findings: list[Finding] = []
    fields = attribute_schema()["fields"]
    for room in rooms:
        for field, rule in (("area_sqm", "area_sanity"), ("distance_to_elevator_m", "distance_sanity")):
            value = getattr(room, field)
            spec = fields[field]
            if value is None:
                continue
            if value < spec["min"] or value > spec["max"]:
                findings.append(
                    Finding(
                        rule=rule,
                        severity=_severity(rule),
                        room_id=room.room_id,
                        detail=f"{field}={value} outside [{spec['min']}, {spec['max']}]",
                    )
                )
    return findings


def _missing_and_low_confidence(rooms: list[ExtractedRoom]) -> list[Finding]:
    threshold = validation_rules()["rules"]["low_confidence_field"]["threshold"]
    findings: list[Finding] = []
    for room in rooms:
        for field, score in sorted(room.confidence.items()):
            if score < threshold:
                findings.append(
                    Finding(
                        rule="low_confidence_field",
                        severity="WARN",
                        room_id=room.room_id,
                        detail=f"{field} confidence {score:.2f} below {threshold:.2f}"
                        + (" and the value is missing" if getattr(room, field, None) is None else ""),
                    )
                )
    return findings


def _drift(job: dict[str, Any], rooms: list[ExtractedRoom]) -> list[Finding]:
    rule = validation_rules()["rules"]["cross_version_drift"]
    previous = {room.room_id: room for room in attribute_store.rooms(job["property_id"])}
    if not previous:
        return []
    changed = 0
    for room in rooms:
        before = previous.get(room.room_id)
        if before is None:
            changed += 1
            continue
        if (before.view, before.distance_to_elevator_m, sorted(before.connecting_with)) != (
            room.view,
            room.distance_to_elevator_m,
            sorted(room.connecting_with),
        ):
            changed += 1
    share = changed / max(len(rooms), 1)
    if share <= rule["threshold"]:
        return []
    severity = "WARN" if job.get("renovation_declared") else rule["severity"]
    return [
        Finding(
            rule="cross_version_drift",
            severity=severity,
            detail=f"{share:.0%} of rooms changed against {attribute_store.current_version()}"
            + (" (renovation declared)" if job.get("renovation_declared") else ""),
        )
    ]


def validate(job: dict[str, Any], rooms: list[ExtractedRoom]) -> ValidationReport:
    findings: list[Finding] = []
    findings += _reconcile(job, rooms)
    findings += _symmetry(rooms)
    findings += _floor_prefix(rooms)
    findings += _bounds(rooms)
    findings += _missing_and_low_confidence(rooms)
    findings += _drift(job, rooms)
    return ValidationReport(job_id=job["job_id"], findings=findings)
