import os
import pathlib
import sys
import uuid

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.graph_build import build_graph  # noqa: E402
from app.mocks import crs, gdl, model_gateway, sor  # noqa: E402
from app.registry import versions  # noqa: E402
from app.state import TurnState  # noqa: E402
from app.tracing import run_config  # noqa: E402

FAULT_FLAGS = ["MODEL_UNCITED_CLAIM", "MODEL_TIMEOUT_ONCE", "CONTENT_EMPTY",
               "_CONTENT_EMPTY_CONSUMED", "SOR_TIMEOUT_ONCE", "RATE_CHANGE_ONCE"]

# A key in the ambient environment would send the suite's questions to a vendor.
OFFLINE = ["JEV_API_KEY", "TYPESAFE_API_KEY"]


def _reset() -> None:
    crs.reset()
    gdl.reset()
    sor.reset()
    model_gateway.reset()


@pytest.fixture(autouse=True)
def clean_world(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    for flag in [*FAULT_FLAGS, *OFFLINE]:
        monkeypatch.delenv(flag, raising=False)
    _reset()
    yield
    _reset()


@pytest.fixture
def graph(tmp_path):
    return build_graph(str(tmp_path / "checkpoints.sqlite"))


def make_state(utterance: str, guest_ref: str = "G-7001", brand_id: str = "B-LUX",
               conversation_id: str | None = None, **kwargs) -> TurnState:
    return TurnState(
        conversation_id=conversation_id or f"C-{uuid.uuid4().hex[:8]}",
        turn_id=uuid.uuid4().hex[:8],
        brand_id=brand_id,
        guest_ref=guest_ref,
        utterance=utterance,
        **kwargs,
    )


def config_for(state: TurnState) -> dict:
    return run_config(state.conversation_id, state.brand_id, versions())


def run_turn(graph, state: TurnState) -> dict:
    return graph.invoke(state, config_for(state))


os.environ.setdefault("LANGSMITH_TRACING", "false")
