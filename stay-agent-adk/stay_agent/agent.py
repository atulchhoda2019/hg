"""`adk web` discovers `root_agent` here.

The agent is assembled, not written: the tools are the registry's rows, the controls are
the four callbacks, and the room truth pipeline hangs off the same package so one `adk
web` session can upload a floor plan and then book a room extracted from it.
"""

from __future__ import annotations

import os
from pathlib import Path

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool
from google.adk.tools.agent_tool import AgentTool

from . import config
from .concierge.callbacks import (
    audit_and_receipt,
    price_guard,
    redact_and_ground,
    registry_gate,
)
from .concierge.prompts import CONCIERGE_PROMPT
from .concierge.slot_agent import build_slot_agent
from .concierge.tools import (
    confirm_action,
    explain_room,
    get_live_quote,
    propose_action,
    search_rooms,
)
from .registry import actions, attribute_schema, is_registered
from .tracing import setup_tracing

CONCIERGE_TOOLS = (search_rooms, get_live_quote, explain_room, propose_action, confirm_action)


def _assert_registry_covers_tools() -> None:
    """Fail at import, not mid-conversation, if a tool is not a registry row (I3)."""
    attribute_schema()  # asserts no price-shaped attribute field (I5)
    unregistered = [fn.__name__ for fn in CONCIERGE_TOOLS if not is_registered(fn.__name__)]
    if unregistered:
        raise AssertionError(
            f"tools missing from {actions()['version']}: {', '.join(unregistered)}"
        )


def build_root_agent() -> LlmAgent:
    _assert_registry_covers_tools()
    return LlmAgent(
        name="concierge",
        model=config.gemini_model(),
        description="Room-level search over versioned attributes, with a corridor around money.",
        instruction=CONCIERGE_PROMPT,
        tools=[
            AgentTool(agent=build_slot_agent()),
            *[FunctionTool(fn) for fn in CONCIERGE_TOOLS],
        ],
        before_model_callback=redact_and_ground,
        after_model_callback=price_guard,
        before_tool_callback=registry_gate,
        after_tool_callback=audit_and_receipt,
    )


def _load_dotenv() -> None:
    env_file = Path(__file__).parent / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()
setup_tracing()  # after the .env: STAY_TRACE and GOOGLE_CLOUD_PROJECT may live there
root_agent = build_root_agent()
