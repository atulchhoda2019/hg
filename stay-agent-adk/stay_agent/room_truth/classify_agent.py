"""Classify: what is this document, and how legible is it?

The quality band matters more than the doc type. A FAX band forces ESCALATE at the gate
whatever the per-field confidences say, because confidence on an illegible scan is a
statement about the model's mood, not about the building.
"""

from __future__ import annotations

from typing import Any

from .. import config, models
from ..contracts import DocClass

CLASSIFY_PROMPT = """You classify property documents for a hotel brand.
Return the document type, the legibility band (CLEAN, SCANNED or FAX), the property id
printed on the page, and the floor numbers the pages cover. Never guess a property id that
is not visible; if unsure, repeat the one given in the caption."""


def classify_from_fixture(document: dict[str, Any]) -> DocClass:
    """The offline path: the fixture declares what a perfect classifier would have read."""
    return DocClass(
        doc_type=document.get("doc_type", "FLOOR_PLAN"),
        quality_band=document.get("quality_band", "CLEAN"),
        property_id=document["property_id"],
        floors=list(document["floors"]),
        notes=document.get("note", ""),
    )


async def classify(document: dict[str, Any], doc_id: str) -> DocClass:
    if config.classifier_backend() != "model":
        return classify_from_fixture(document)

    from .intake import page_image

    parts = [models.text_part(
        f"Document {doc_id}, caption says property {document['property_id']}. "
        f"{len(document['pages'])} page(s) follow."
    )]
    for page in document["pages"]:
        image = page_image(doc_id, page["floor"])
        if image:
            parts.append(models.text_part(f"page: floor {page['floor']}"))
            parts.append(models.image_part(*image))
    try:
        return await models.generate_typed(
            config.gemini_model(), parts, DocClass, system_instruction=CLASSIFY_PROMPT
        )
    except models.ModelUnavailable:
        # A classifier outage must not silently become a clean document.
        fallback = classify_from_fixture(document)
        fallback.notes = (fallback.notes + " [classifier unavailable; fixture band used]").strip()
        return fallback
