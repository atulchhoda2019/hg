"""Walk the booking corridor without a model, so the controls can be watched directly.

    python scripts/demo_corridor.py                       # happy path
    RATE_CHANGED_ONCE=1 python scripts/demo_corridor.py   # the rate moves after the preview
    SOLD_OUT_AFTER_PREVIEW=1 python scripts/demo_corridor.py
    CRS_TIMEOUT_ONCE=1 python scripts/demo_corridor.py
"""

from __future__ import annotations

import json
from typing import Any

from stay_agent.concierge.tools import (
    confirm_action,
    get_live_quote,
    propose_action,
    search_rooms,
)
from stay_agent.mocks import faults


class Ctx:
    """The two lines of ToolContext the tools actually use."""

    def __init__(self) -> None:
        self.state: dict[str, Any] = {
            "temp:slots": {
                "property_id": "H-201",
                "check_in": "2026-06-12",
                "check_out": "2026-06-14",
                "guests": 2,
                "min_floor": 6,
                "view": "park",
            },
            "user:guest_id": "G-2001",
        }


def show(label: str, payload: Any) -> None:
    print(f"\n=== {label}")
    print(json.dumps(payload, indent=2, default=str)[:900])


def main() -> None:
    if faults.active():
        print(f"fault drills: {', '.join(faults.active())}")
    ctx = Ctx()

    found = search_rooms(ctx, limit=3)
    show("search_rooms (no prices, ever)", found)
    if found["status"] != "OK":
        return
    room_id = found["rooms"][0]["room_id"]

    quoted = get_live_quote(room_id, ctx)
    if quoted["status"] == "UNAVAILABLE":
        show("get_live_quote (CRS timed out, retrying)", quoted)
        quoted = get_live_quote(room_id, ctx)
    show("get_live_quote (the only door money comes through)", quoted)
    if quoted["status"] != "OK":
        return

    preview = propose_action("BOOK", ctx, quote_id=quoted["quote"]["quote_id"])
    show("propose_action (preview rendered in code, nonce minted)", preview)
    if preview["status"] != "PREVIEW":
        return

    outcome = confirm_action(preview["proposal"]["nonce"], ctx)
    show("confirm_action (revalidate, execute, read-after-write)", outcome)

    replay = confirm_action(preview["proposal"]["nonce"], ctx)
    show("confirm_action replayed (nothing double-books)", replay)


if __name__ == "__main__":
    main()
