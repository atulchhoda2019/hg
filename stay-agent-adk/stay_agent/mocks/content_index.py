"""Mock prose content index: the coldest data temperature (I5).

Brochure copy is indexed and freely retrievable because being a month stale costs nothing.
It is joined onto search results as a snippet and is never the source of a fact the
corridor depends on.
"""

from __future__ import annotations

_PROPERTY_COPY = {
    "H-201": "Park-side high-rise; upper floors face the park across a quiet boulevard.",
    "H-202": "Bay resort with low-rise wings around the garden courtyard.",
    "H-203": "Airport transit hotel, soundproofed, four minutes from the terminal train.",
    "H-204": "The Legacy: 1920s building, restored lobby, small floor plates.",
}

_VIEW_COPY = {
    "park": "Park-facing; the treetops start two floors below.",
    "street": "Boulevard-facing, with the brand's double glazing.",
    "courtyard": "Courtyard-facing and the quietest side of the building.",
}


def property_snippet(property_id: str) -> str:
    return _PROPERTY_COPY.get(property_id, "")


def room_snippet(property_id: str, view: str | None, floor: int) -> str:
    parts = [_VIEW_COPY.get(view or "", "")]
    if floor >= 5:
        parts.append("High floor.")
    return " ".join(p for p in parts if p).strip()
