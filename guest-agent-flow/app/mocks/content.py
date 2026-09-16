"""Mock property content platform: owner-uploaded descriptions and policies.

Everything here carries provenance and a validity window, and the retrieval is filtered
first by brand, then by the property set the caller already authorized, then by the
version effective on the stay date. Reranking only ever reorders that set.
"""
import json
import os
from datetime import datetime, timezone

from app.registry import FIXTURES_DIR
from app.tracing import tool_span


def _passages() -> list[dict]:
    with (FIXTURES_DIR / "content.json").open() as fh:
        return json.load(fh)


def _effective(passage: dict, service_date: str) -> bool:
    if passage["effective_from"] > service_date:
        return False
    return passage["effective_to"] is None or passage["effective_to"] >= service_date


@tool_span("content.retrieve", "v7")
def retrieve(brand_id: str, property_ids: list[str], service_date: str, query: str,
             limit: int = 4) -> dict:
    fault = os.environ.get("CONTENT_EMPTY")
    if fault == "always" or (fault == "1" and not os.environ.get("_CONTENT_EMPTY_CONSUMED")):
        os.environ["_CONTENT_EMPTY_CONSUMED"] = "1"
        hits: list[dict] = []
    else:
        allowed = set(property_ids)
        authorized = [
            p
            for p in _passages()
            if p["brand_id"] == brand_id
            and (p["property_id"] in allowed or p["property_id"] == "*")
            and _effective(p, service_date)
        ]
        text = query.lower()
        terms = {t for t in text.replace("?", " ").replace(",", " ").split() if t}
        scored = [
            (sum(1 for k in p["keywords"] if k in text or k in terms), p["passage_id"], p)
            for p in authorized
        ]
        scored.sort(key=lambda row: (-row[0], row[1]))
        hits = [p for score, _, p in scored if score > 0][:limit] or [p for _, _, p in scored][:limit]

    return {
        "payload": hits,
        "source": "content.property.v7",
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }


@tool_span("content.passage", "v7")
def passage(passage_id: str, service_date: str) -> dict | None:
    """Fetch one passage by id, as it stands on the stay date. Used for cancellation terms."""
    for item in _passages():
        if item["passage_id"] == passage_id and _effective(item, service_date):
            return item
    return None
