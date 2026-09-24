"""Mock CRS inventory: which rooms exist, and which are sellable for a date range.

This is the authority for room identity. Room *attributes* are extracted from floor plans
and can be wrong; room *ids* come from here, which is why reconciliation against this
module is the one validation rule that is never waivable (I7).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from . import faults


@dataclass(frozen=True)
class Property:
    property_id: str
    name: str
    market: str
    floors: tuple[int, ...]
    rooms_per_floor: int
    kind: str


PROPERTIES: dict[str, Property] = {
    "H-201": Property("H-201", "Park Side High-Rise", "urban", (2, 3, 4, 5, 6, 7), 7, "high_rise"),
    "H-202": Property("H-202", "Bay Resort", "resort", (1, 2, 3), 8, "resort"),
    "H-203": Property("H-203", "Airport Transit", "airport", (1, 2), 10, "airport"),
    "H-204": Property("H-204", "The Legacy", "urban", (1, 2), 6, "legacy"),
}


def room_ids(property_id: str, floor: int | None = None) -> list[str]:
    prop = PROPERTIES[property_id]
    floors = prop.floors if floor is None else (floor,)
    out: list[str] = []
    for fl in floors:
        if fl not in prop.floors:
            continue
        out.extend(f"{fl}{n:02d}" for n in range(1, prop.rooms_per_floor + 1))
    return out


def is_available(property_id: str, room_id: str, check_in: date, check_out: date) -> bool:
    """Availability is a live read, never an indexed attribute (I5)."""
    if faults.marked("preview") and faults.fire_once("SOLD_OUT_AFTER_PREVIEW"):
        return False
    if room_id not in room_ids(property_id):
        return False
    # A stable, boring pattern: the last room on each floor is held back for walk-ins.
    return not room_id.endswith(f"{PROPERTIES[property_id].rooms_per_floor:02d}")


def floors(property_id: str) -> tuple[int, ...]:
    return PROPERTIES[property_id].floors
