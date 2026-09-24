"""Versioned room-attribute store with provenance on every row.

Semi-static data, the middle temperature (I5). Writes are append-only: a commit creates a
new version and the previous one stays queryable, which is what makes "why did the agent
say room 512 is park view last Tuesday" an answerable question.

State lives in `STAY_STATE_DIR` (default `.stay_state/`) as JSON so that `adk web` and the
review UI, which are separate processes, see the same store.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..contracts import Provenance, RoomAttributes

_LOCK = threading.Lock()
_SEED = Path(__file__).parent / "floorplans" / "attributes_seed.json"


def state_dir() -> Path:
    path = Path(os.environ.get("STAY_STATE_DIR", ".stay_state"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _path() -> Path:
    return state_dir() / "attribute_store.json"


def _blank() -> dict[str, Any]:
    with _SEED.open() as fh:
        seed = json.load(fh)
    return {"versions": [seed], "current": seed["attribute_version"]}


def _read() -> dict[str, Any]:
    path = _path()
    if not path.exists():
        data = _blank()
        path.write_text(json.dumps(data, indent=2))
        return data
    with path.open() as fh:
        return json.load(fh)


def _write(data: dict[str, Any]) -> None:
    _path().write_text(json.dumps(data, indent=2, default=str))


def reset() -> None:
    with _LOCK:
        _write(_blank())


def current_version() -> str:
    return _read()["current"]


def versions() -> list[str]:
    return [v["attribute_version"] for v in _read()["versions"]]


def _version_block(data: dict[str, Any], version: str | None) -> dict[str, Any]:
    version = version or data["current"]
    for block in data["versions"]:
        if block["attribute_version"] == version:
            return block
    raise KeyError(f"unknown attribute version {version!r}")


def rooms(property_id: str, version: str | None = None) -> list[RoomAttributes]:
    data = _read()
    block = _version_block(data, version)
    out: list[RoomAttributes] = []
    for row in block["rooms"]:
        if row["property_id"] != property_id:
            continue
        out.append(
            RoomAttributes(
                **{k: v for k, v in row.items() if k != "provenance"},
                provenance=Provenance(
                    attribute_version=block["attribute_version"],
                    source_doc_id=row["provenance"]["source_doc_id"],
                    extractor_version=row["provenance"]["extractor_version"],
                    approver=row["provenance"].get("approver"),
                    committed_at=block["committed_at"],
                ),
            )
        )
    return out


def room(property_id: str, room_id: str, version: str | None = None) -> RoomAttributes | None:
    for candidate in rooms(property_id, version):
        if candidate.room_id == room_id:
            return candidate
    return None


def next_version() -> str:
    existing = versions()
    return f"av-{len(existing) + 1}"


def commit_version(
    *,
    property_id: str,
    rooms_payload: list[dict[str, Any]],
    source_doc_id: str,
    extractor_version: str,
    approver: str | None,
) -> str:
    """Append a new version. Only `room_truth.commit` calls this; no model ever does (I6)."""
    with _LOCK:
        data = _read()
        previous = _version_block(data, data["current"])
        carried = [r for r in previous["rooms"] if r["property_id"] != property_id]
        provenance = {
            "source_doc_id": source_doc_id,
            "extractor_version": extractor_version,
            "approver": approver,
        }
        fresh = [{**row, "property_id": property_id, "provenance": provenance} for row in rooms_payload]
        version = f"av-{len(data['versions']) + 1}"
        data["versions"].append(
            {
                "attribute_version": version,
                "committed_at": datetime.now(timezone.utc).isoformat(),
                "rooms": carried + fresh,
            }
        )
        data["current"] = version
        _write(data)
        return version
