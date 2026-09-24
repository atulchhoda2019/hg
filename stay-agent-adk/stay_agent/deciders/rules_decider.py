"""Deterministic decider. When the table has an exact answer, it wins.

This is also the floor under the other two: every model-backed decider falls back here, so
an outage degrades the decision quality, never the governance.
"""

from __future__ import annotations

import re
from typing import Any

from ..contracts import Decision

_SLOT_SIGNALS = ("property_id", "check_in", "check_out", "guests", "view", "min_floor")

_INTENT_PATTERNS: list[tuple[str, str]] = [
    (r"\bcancel\b", "CANCEL"),
    (r"\b(connecting|upgrade|upsell|higher floor|quieter room)\b", "UPSELL"),
    (r"\b(book|reserve|take it|confirm)\b", "BOOK"),
    (r"\b(how much|price|rate|cost|quote)\b", "QUOTE"),
    (r"\b(why|how do you know|provenance|source)\b", "EXPLAIN"),
    (r"\b(room|stay|night|suite|available|park view|floor)\b", "SEARCH"),
]


class RulesDecider:
    name = "rules"

    async def decide(self, question: str, options: list[str], state: dict[str, Any]) -> Decision:
        text = (state.get("utterance") or "").lower()

        if set(options) == {"HIGH", "MEDIUM", "LOW"}:
            slots = state.get("slots") or {}
            filled = sum(1 for key in _SLOT_SIGNALS if slots.get(key) not in (None, "any"))
            choice = "HIGH" if filled >= 4 else "MEDIUM" if filled >= 2 else "LOW"
            return Decision(
                choice=choice,
                confidence=0.99,
                decider=self.name,
                reason=f"{filled} of {len(_SLOT_SIGNALS)} slot signals present",
            )

        if "ELIGIBLE" in options:
            reservation = state.get("reservation") or {}
            eligible = bool(reservation) and reservation.get("status") == "CONFIRMED"
            return Decision(
                choice="ELIGIBLE" if eligible else "NOT_ELIGIBLE",
                confidence=1.0,
                decider=self.name,
                reason="an upsell needs a confirmed reservation to attach to",
            )

        for pattern, intent in _INTENT_PATTERNS:
            if intent in options and re.search(pattern, text):
                return Decision(
                    choice=intent,
                    confidence=0.90,
                    decider=self.name,
                    reason=f"matched /{pattern}/",
                )

        fallback = options[-1] if options else ""
        return Decision(
            choice=fallback, confidence=0.40, decider=self.name, reason="no rule matched"
        )
