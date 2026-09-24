from __future__ import annotations

import os
from typing import Any

import pytest

# The whole suite runs offline: no key, no model, deterministic fixtures.
os.environ.setdefault("STAY_CLASSIFIER", "fixture")
os.environ.setdefault("STAY_EXTRACTOR", "fixture")
os.environ.setdefault("STAY_DECIDER_MODE", "rules_only")

from stay_agent.mocks import attribute_store, faults, rate_engine, reservations  # noqa: E402
from stay_agent.room_truth import queue  # noqa: E402


class FakeToolContext:
    """Stands in for ADK's ToolContext: the tools only ever touch `.state`."""

    def __init__(self, state: dict[str, Any] | None = None) -> None:
        self.state: dict[str, Any] = state or {}


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("STAY_STATE_DIR", str(tmp_path / "state"))
    attribute_store.reset()
    queue.reset()
    reservations.reset()
    rate_engine.reset()
    faults.reset()
    yield
    faults.reset()


@pytest.fixture
def tool_context() -> FakeToolContext:
    return FakeToolContext(
        {
            "slots": {
                "property_id": "H-201",
                "check_in": "2026-06-12",
                "check_out": "2026-06-14",
                "guests": 2,
            },
            "user:guest_id": "G-2001",
        }
    )
