"""Intake: plain code. Hash the document, name the job, refuse a duplicate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

FLOORPLANS = Path(__file__).resolve().parents[1] / "mocks" / "floorplans"


class UnknownDocument(KeyError):
    pass


def load_document(doc_id: str) -> dict[str, Any]:
    path = FLOORPLANS / f"{doc_id}.json"
    if not path.exists():
        raise UnknownDocument(doc_id)
    with path.open() as fh:
        return json.load(fh)


def page_image(doc_id: str, floor: int) -> tuple[str, bytes] | None:
    path = FLOORPLANS / f"{doc_id}-f{floor}.png"
    if not path.exists():
        return None
    return str(path), path.read_bytes()


def intake(doc_id: str) -> dict[str, Any]:
    document = load_document(doc_id)
    digest = hashlib.sha256(json.dumps(document, sort_keys=True).encode()).hexdigest()[:12]
    return {
        "job_id": f"J-{doc_id}-{digest}",
        "doc_id": doc_id,
        "doc_digest": digest,
        "property_id": document["property_id"],
        "renovation_declared": bool(document.get("renovation_declared")),
        "document": document,
    }
