"""The only writer of room attributes. Plain Python, never exposed to a model (I6)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .. import config
from ..contracts import CommitReceipt, ExtractedRoom
from ..mocks import attribute_store
from ..registry import attribute_schema


def _row(room: ExtractedRoom) -> dict[str, Any]:
    allowed = set(attribute_schema()["fields"]) - {"room_id"}
    row = {"room_id": room.room_id}
    for field in allowed:
        row[field] = getattr(room, field, None)
    return row


def commit(
    job: dict[str, Any],
    rooms: list[ExtractedRoom],
    *,
    approver: str | None = None,
) -> CommitReceipt:
    version = attribute_store.commit_version(
        property_id=job["property_id"],
        rooms_payload=[_row(room) for room in rooms],
        source_doc_id=job["doc_id"],
        extractor_version=config.EXTRACTOR_VERSION,
        approver=approver,
    )
    return CommitReceipt(
        job_id=job["job_id"],
        property_id=job["property_id"],
        attribute_version=version,
        rooms_written=len(rooms),
        approver=approver,
        at=datetime.now(timezone.utc),
    )
