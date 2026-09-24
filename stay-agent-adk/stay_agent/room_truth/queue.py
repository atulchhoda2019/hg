"""The escalation queue, and the golden labels it produces.

The queue is not an error log. A job lands here whenever the pipeline is not entitled to
write on its own, a human fixes the rooms the validator named, and the fix is appended to
`golden_labels.json` as a labeled example. The escalation queue IS the training pipeline.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..mocks.attribute_store import state_dir


def _queue_path() -> Path:
    return state_dir() / "escalation_queue.json"


def _labels_path() -> Path:
    return state_dir() / "golden_labels.json"


def _read(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open() as fh:
        return json.load(fh)


def _write(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(json.dumps(rows, indent=2, default=str))


def reset() -> None:
    _write(_queue_path(), [])
    _write(_labels_path(), [])


def enqueue(job: dict[str, Any]) -> str:
    rows = _read(_queue_path())
    job = {**job, "status": "OPEN", "enqueued_at": datetime.now(timezone.utc).isoformat()}
    rows.append(job)
    _write(_queue_path(), rows)
    return job["job_id"]


def open_jobs() -> list[dict[str, Any]]:
    return [row for row in _read(_queue_path()) if row["status"] == "OPEN"]


def get(job_id: str) -> dict[str, Any] | None:
    for row in _read(_queue_path()):
        if row["job_id"] == job_id:
            return row
    return None


def resolve(job_id: str, *, approver: str, fixes: list[dict[str, Any]]) -> dict[str, Any]:
    rows = _read(_queue_path())
    for row in rows:
        if row["job_id"] != job_id:
            continue
        row["status"] = "RESOLVED"
        row["approver"] = approver
        row["fixes"] = fixes
        row["resolved_at"] = datetime.now(timezone.utc).isoformat()
        _write(_queue_path(), rows)
        _append_labels(row, fixes)
        return row
    raise KeyError(f"unknown job {job_id!r}")


def _append_labels(job: dict[str, Any], fixes: list[dict[str, Any]]) -> None:
    labels = _read(_labels_path())
    for fix in fixes:
        labels.append(
            {
                "doc_id": job["doc_id"],
                "property_id": job["property_id"],
                "room_id": fix["room_id"],
                "field": fix["field"],
                "extracted": fix.get("extracted"),
                "label": fix["value"],
                "approver": job["approver"],
                "at": job["resolved_at"],
            }
        )
    _write(_labels_path(), labels)


def golden_labels() -> list[dict[str, Any]]:
    return _read(_labels_path())
