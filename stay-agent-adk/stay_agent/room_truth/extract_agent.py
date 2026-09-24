"""Extraction fan-out: one typed call per floor, concurrently.

Per-floor is the unit because a floor is what fits in one image and because a bad floor
should fail alone. Nothing here writes: the result is a proposal that validation is free
to reject (I6).
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel

from .. import config, models
from ..contracts import ExtractedRoom
from ..mocks import faults

EXTRACT_PROMPT = """You read one hotel floor plan page and list its rooms.
For each room return: room_id, floor, view (park|street|courtyard or null), the rooms it
connects with, distance_to_elevator_m, corner, accessible, area_sqm, and a per-field
confidence between 0 and 1. Report only what the page shows. If a value is illegible,
return null and a low confidence for that field. Never invent a room that is not drawn."""


class FloorExtraction(BaseModel):
    floor: int
    rooms: list[ExtractedRoom]


def _from_fixture(page: dict[str, Any]) -> FloorExtraction:
    rooms = [ExtractedRoom(**room) for room in page["rooms"]]
    if faults.enabled("EXTRACTOR_HALLUCINATE_ROOM") and rooms:
        ghost = rooms[0].model_copy(deep=True)
        ghost.room_id = f"{page['floor']}99"
        ghost.confidence = {k: 0.91 for k in ghost.confidence}
        rooms.append(ghost)
    if faults.enabled("CONNECTING_ASYMMETRY"):
        for room in rooms:
            if room.connecting_with:
                room.connecting_with = []
                break
    return FloorExtraction(floor=page["floor"], rooms=rooms)


async def _extract_page(doc_id: str, page: dict[str, Any]) -> FloorExtraction:
    if config.extractor_backend() != "model":
        return _from_fixture(page)

    from .intake import page_image

    image = page_image(doc_id, page["floor"])
    if image is None:
        return _from_fixture(page)
    parts = [
        models.text_part(f"Floor {page['floor']} of document {doc_id}."),
        models.image_part(*image),
    ]
    try:
        extraction = await models.generate_typed(
            config.gemini_model(), parts, FloorExtraction, system_instruction=EXTRACT_PROMPT
        )
    except models.ModelUnavailable:
        return _from_fixture(page)
    extraction.floor = page["floor"]
    return extraction


async def extract(job: dict[str, Any]) -> list[ExtractedRoom]:
    document = job["document"]
    pages = await asyncio.gather(*(_extract_page(job["doc_id"], p) for p in document["pages"]))
    rooms: list[ExtractedRoom] = []
    for page in pages:
        for room in page.rooms:
            room.floor = room.floor or page.floor
            rooms.append(room)
    return rooms
