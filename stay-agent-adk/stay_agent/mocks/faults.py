"""Fault injection by environment flag.

Drills are part of the product, not a test-only affordance: every flag here corresponds to
a failure a real integration produces on a bad day, and the demo script in the README runs
them on purpose. One-shot faults fire once per process so a retry can succeed.
"""

from __future__ import annotations

import os

FLAGS = (
    "RATE_CHANGED_ONCE",
    "SOLD_OUT_AFTER_PREVIEW",
    "CRS_TIMEOUT_ONCE",
    "MODEL_PRICE_HALLUCINATION",
    "EXTRACTOR_HALLUCINATE_ROOM",
    "CONNECTING_ASYMMETRY",
    "DUPLICATE_CONFIRM",
)

_fired: set[str] = set()
_overrides: dict[str, bool] = {}
_marks: set[str] = set()


def enabled(flag: str) -> bool:
    if flag in _overrides:
        return _overrides[flag]
    return os.environ.get(flag, "").lower() in {"1", "true", "yes"}


def fire_once(flag: str) -> bool:
    """True the first time an enabled one-shot fault is asked about, False after."""
    if not enabled(flag) or flag in _fired:
        return False
    _fired.add(flag)
    return True


def set_flag(flag: str, value: bool) -> None:
    """Test/demo hook. Keeps drills out of the environment when a test wants one fault."""
    if flag not in FLAGS:
        raise KeyError(f"unknown fault flag {flag!r}")
    _overrides[flag] = value


def mark(name: str) -> None:
    """Record that a stage happened, so a fault can fire *after* it rather than before."""
    _marks.add(name)


def marked(name: str) -> bool:
    return name in _marks


def reset() -> None:
    _fired.clear()
    _overrides.clear()
    _marks.clear()


def active() -> list[str]:
    return [f for f in FLAGS if enabled(f)]
