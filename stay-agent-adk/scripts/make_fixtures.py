"""Regenerate the floor-plan fixtures and their PNG renders.

The JSON is what the deterministic extractor reads; the PNG is what Gemini reads when
`STAY_EXTRACTOR=model`. They describe the same building on purpose, so a run with a model
and a run without one can be compared field by field.

    pip install pillow && python scripts/make_fixtures.py
"""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "stay_agent" / "mocks" / "floorplans"
VIEWS = ["park", "park", "street", "street", "courtyard", "courtyard", "park"]


def room(room_id: str, floor: int, idx: int, per_floor: int, *, quality: str) -> dict:
    view = VIEWS[idx % len(VIEWS)]
    corner = idx in (0, per_floor - 1)
    confidence = {"view": 0.93, "distance_to_elevator_m": 0.90, "accessible": 0.95}
    if quality == "FAX":
        confidence = {"view": 0.58, "distance_to_elevator_m": 0.52, "accessible": 0.61}
    elif quality == "SCANNED":
        confidence = {"view": 0.81, "distance_to_elevator_m": 0.79, "accessible": 0.88}
    return {
        "room_id": room_id,
        "floor": floor,
        "view": view,
        "connecting_with": [],
        "distance_to_elevator_m": round(6.0 + idx * 4.5, 1),
        "corner": corner,
        "accessible": idx == 1,
        "area_sqm": 28.0 + (idx % 3) * 6.0,
        "confidence": confidence,
    }


def floor_page(floor: int, per_floor: int, *, quality: str) -> dict:
    rooms = [
        room(f"{floor}{n:02d}", floor, n - 1, per_floor, quality=quality)
        for n in range(1, per_floor + 1)
    ]
    # Connecting pairs are declared both ways in a clean plan.
    for a, b in zip(rooms[1::4], rooms[2::4], strict=False):
        a["connecting_with"] = [b["room_id"]]
        b["connecting_with"] = [a["room_id"]]
    return {"floor": floor, "render": None, "rooms": rooms}


def doc(doc_id: str, property_id: str, floors: list[int], per_floor: int, quality: str, **extra) -> dict:
    pages = [floor_page(f, per_floor, quality=quality) for f in floors]
    for page in pages:
        page["render"] = f"{doc_id}-f{page['floor']}.png"
    return {
        "doc_id": doc_id,
        "property_id": property_id,
        "quality_band": quality,
        "doc_type": "FLOOR_PLAN",
        "floors": floors,
        "pages": pages,
        **extra,
    }


def build() -> dict[str, dict]:
    pr1 = doc("PR-1", "H-201", [2, 3, 4, 5, 6, 7], 7, "CLEAN", note="clean architect plan, 42 rooms")

    pr2 = doc("PR-2", "H-204", [1, 2], 6, "FAX", note="1987 fax; two rooms need a human")
    # The fax lost two fields outright and broke one connecting pair.
    pr2["pages"][0]["rooms"][2]["view"] = None
    pr2["pages"][0]["rooms"][2]["confidence"]["view"] = 0.20
    pr2["pages"][1]["rooms"][3]["distance_to_elevator_m"] = None
    pr2["pages"][1]["rooms"][3]["confidence"]["distance_to_elevator_m"] = 0.18
    pr2["pages"][0]["rooms"][1]["connecting_with"] = ["103"]
    pr2["pages"][0]["rooms"][2]["connecting_with"] = []

    pr3 = doc("PR-3", "H-202", [1, 2, 3], 8, "SCANNED", note="extractor invents room 314")
    invented = room("314", 3, 5, 8, quality="SCANNED")
    invented["confidence"] = {k: 0.88 for k in invented["confidence"]}
    pr3["pages"][2]["rooms"].append(invented)

    pr4 = doc(
        "PR-4",
        "H-201",
        [2, 3, 4, 5, 6, 7],
        7,
        "CLEAN",
        note="post-renovation replan, declared",
        renovation_declared=True,
    )
    for page in pr4["pages"]:  # the renovation moved the core; most rooms changed
        for r in page["rooms"]:
            r["distance_to_elevator_m"] = round(max(3.0, r["distance_to_elevator_m"] - 3.5), 1)
            if r["view"] == "street":
                r["view"] = "courtyard"

    return {d["doc_id"]: d for d in (pr1, pr2, pr3, pr4)}


def seed(docs: dict[str, dict]) -> dict:
    """av-0: the properties that were already commissioned before this app existed."""
    rows = []
    for doc_id, property_id in (("PR-1", "H-201"), ("PR-3", "H-202")):
        for page in docs[doc_id]["pages"]:
            for r in page["rooms"]:
                if r["room_id"] == "314":  # the invented room never made it into truth
                    continue
                rows.append(
                    {
                        "room_id": r["room_id"],
                        "property_id": property_id,
                        "floor": r["floor"],
                        "view": r["view"],
                        "connecting_with": r["connecting_with"],
                        "distance_to_elevator_m": r["distance_to_elevator_m"],
                        "corner": r["corner"],
                        "accessible": r["accessible"],
                        "area_sqm": r["area_sqm"],
                        "provenance": {
                            "source_doc_id": "LEGACY-IMPORT",
                            "extractor_version": "legacy-import",
                            "approver": "commissioning-team",
                        },
                    }
                )
    return {
        "attribute_version": "av-0",
        "committed_at": "2026-01-05T00:00:00+00:00",
        "rooms": rows,
    }


def render(docs: dict[str, dict]) -> None:
    try:
        from PIL import Image, ImageDraw
    except ImportError:  # renders are committed; pillow is only needed to regenerate them
        print("pillow not installed, skipping PNG renders")
        return
    for doc_id, document in docs.items():
        fax = document["quality_band"] == "FAX"
        for page in document["pages"]:
            img = Image.new("L", (760, 300), color=235 if fax else 255)
            draw = ImageDraw.Draw(img)
            draw.text((16, 12), f"{document['property_id']}  {doc_id}  floor {page['floor']}", fill=0)
            for i, r in enumerate(page["rooms"]):
                x = 16 + (i % 6) * 122
                y = 44 + (i // 6) * 110
                draw.rectangle([x, y, x + 110, y + 92], outline=0, width=2 if not fax else 1)
                draw.text((x + 8, y + 10), r["room_id"], fill=0)
                draw.text((x + 8, y + 30), (r["view"] or "?")[:9], fill=60 if fax else 0)
                dist = r["distance_to_elevator_m"]
                draw.text((x + 8, y + 50), f"{dist if dist is not None else '?'} m", fill=60 if fax else 0)
                if r["connecting_with"]:
                    draw.text((x + 8, y + 70), f"<-> {r['connecting_with'][0]}", fill=0)
            draw.text((16, 272), "ELEVATOR CORE: LEFT", fill=0)
            if fax:
                img = _faxify(img)
            img.save(OUT / page["render"])


def _faxify(img):
    """Make a FAX page look like one: low contrast, speckle, skew, 1-bit dither, scan edge.

    The classifier is a model looking at pixels, so the quality band has to be visible in
    the pixels rather than asserted in the JSON beside them.
    """
    import random

    from PIL import Image, ImageDraw, ImageFilter

    rng = random.Random(7)
    img = img.filter(ImageFilter.GaussianBlur(0.7))  # soft, out-of-focus strokes
    img = img.point(lambda v: 255 if v > 205 else int(70 + v * 0.45))  # grey, broken blacks
    px = img.load()
    w, h = img.size
    for _ in range(w * h // 260):  # toner speckle
        x, y = rng.randrange(w), rng.randrange(h)
        px[x, y] = rng.choice((0, 0, 255))
    draw = ImageDraw.Draw(img)
    for y in range(rng.randrange(20, 40), h, rng.randrange(37, 53)):  # dropped scan lines
        draw.line([(0, y), (w, y)], fill=rng.randrange(120, 190), width=1)
    img = img.rotate(-1.1, resample=Image.BILINEAR, fillcolor=255, expand=False)
    img = img.convert("1").convert("L")  # 1-bit dither, the actual fax indignity
    edge = ImageDraw.Draw(img)
    edge.rectangle([0, 0, 9, h], fill=0)  # the black band a scanner leaves
    return img


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    docs = build()
    for doc_id, document in docs.items():
        (OUT / f"{doc_id}.json").write_text(json.dumps(document, indent=2) + "\n")
    (OUT / "attributes_seed.json").write_text(json.dumps(seed(docs), indent=2) + "\n")
    render(docs)
    print(f"wrote {len(docs)} floor plans to {OUT}")


if __name__ == "__main__":
    main()
