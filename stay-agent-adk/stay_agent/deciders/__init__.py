"""Decider selection: the table picks, the code calls, the fallback is always rules."""

from __future__ import annotations

from typing import Any

from ..contracts import Decision
from ..registry import decision_table
from .gemini_decider import GeminiDecider
from .gemma_decider import GemmaDecider
from .port import DeciderUnavailable, TypedDecider
from .rules_decider import RulesDecider

_MODE_PINS = {"gemini_only": "gemini", "gemma_only": "gemma", "rules_only": "rules"}


def build(name: str) -> TypedDecider:
    return {"gemini": GeminiDecider, "gemma": GemmaDecider, "rules": RulesDecider}[name]()


def decider_for(decision_type: str, *, mode: str | None = None) -> tuple[str, str]:
    """Return (primary, fallback) decider names for a decision type."""
    from .. import config

    table = decision_table()
    row = table["decisions"][decision_type]
    mode = mode or config.decider_mode()
    if mode in _MODE_PINS:
        return _MODE_PINS[mode], "rules"
    return row["decider"], row.get("fallback", "rules")


async def decide(decision_type: str, state: dict[str, Any], *, mode: str | None = None) -> Decision:
    """Ask the configured decider; fall back on the table's fallback, never on a guess."""
    row = decision_table()["decisions"][decision_type]
    primary, fallback = decider_for(decision_type, mode=mode)
    try:
        return await build(primary).decide(row["question"], row["options"], state)
    except DeciderUnavailable as exc:
        decision = await build(fallback).decide(row["question"], row["options"], state)
        decision.reason = f"{decision.reason} (after {primary} failed: {exc})"
        return decision


__all__ = [
    "Decision",
    "DeciderUnavailable",
    "GeminiDecider",
    "GemmaDecider",
    "RulesDecider",
    "TypedDecider",
    "build",
    "decide",
    "decider_for",
]
