"""The controls. Not the prompt.

Four callbacks stand between the model and the world: a registry gate before every tool,
a price guard after every model turn, a redactor before every model turn, and an audit
after every tool. Each one is code that runs whatever the model intended.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.tools.base_tool import BaseTool
from google.genai import types

from ..mocks import attribute_store, faults
from ..registry import tool_spec
from . import corridor

logger = logging.getLogger("stay_agent.audit")

CARD_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")
MONEY_RE = re.compile(r"(?:USD|\$|€|£)\s?([0-9][0-9,]*(?:\.[0-9]{2})?)")

PRICE_GUARD_REPLY = (
    "I can't quote a rate I haven't read this session. Let me pull a live quote for that "
    "room and come back with the real number."
)


def _text_of(response: LlmResponse) -> str:
    if not response.content or not response.content.parts:
        return ""
    return "".join(part.text or "" for part in response.content.parts)


def redact_and_ground(callback_context: CallbackContext, llm_request: LlmRequest) -> None:
    """Strip card-like numbers from what reaches the model; pin the attribute version."""
    for content in llm_request.contents or []:
        for part in content.parts or []:
            if part.text and CARD_RE.search(part.text):
                part.text = CARD_RE.sub("[redacted]", part.text)

    version = attribute_store.current_version()
    callback_context.state[corridor.VERSION_KEY] = version
    config = llm_request.config
    grounding = (
        f"\n\nCurrent room attribute version: {version}. Room facts you cite come from this "
        "version; prices come only from get_live_quote in this session."
    )
    if config is not None:
        existing = config.system_instruction
        if isinstance(existing, str) or existing is None:
            config.system_instruction = (existing or "") + grounding
    return None


def price_guard(callback_context: CallbackContext, llm_response: LlmResponse) -> LlmResponse | None:
    """Every amount in a reply must be a number a Quote in this session actually holds (I1)."""
    text = _text_of(llm_response)
    if faults.enabled("MODEL_PRICE_HALLUCINATION"):
        text = f"{text}\n\nYour total comes to $1,499.00 for the stay."

    amounts = MONEY_RE.findall(text)
    if not amounts:
        return None

    allowed = corridor.money_strings(callback_context.state)
    unbacked = [a for a in amounts if a.replace(",", "") not in {x.replace(",", "") for x in allowed}]
    if not unbacked:
        return None

    _audit(
        callback_context,
        {
            "event": "price_guard.blocked",
            "amounts": unbacked,
            "allowed": sorted(allowed),
        },
    )
    return LlmResponse(
        content=types.Content(role="model", parts=[types.Part(text=PRICE_GUARD_REPLY)]),
        custom_metadata={"stay_guard": "price_guard", "blocked_amounts": unbacked},
    )


def registry_gate(
    tool: BaseTool, args: dict[str, Any], tool_context: Any
) -> dict[str, Any] | None:
    """A tool runs only if it is a row in actions.yaml and its args fit that row (I3)."""
    spec = tool_spec(tool.name)
    if spec is None:
        return {
            "status": "REFUSED",
            "reason": f"{tool.name} is not in the action registry",
        }

    declared = spec.get("args") or {}
    for name, value in args.items():
        if name in {"tool_context"}:
            continue
        rule = declared.get(name)
        if rule is None:
            return {"status": "REFUSED", "reason": f"{tool.name} has no argument {name!r}"}
        if value in (None, ""):
            continue
        expected = {"str": str, "int": int, "float": (int, float), "bool": bool}[rule["type"]]
        if not isinstance(value, expected):
            return {
                "status": "REFUSED",
                "reason": f"{tool.name}.{name} must be {rule['type']}",
            }
        if rule.get("enum") and value not in rule["enum"]:
            return {
                "status": "REFUSED",
                "reason": f"{tool.name}.{name} must be one of {rule['enum']}",
            }
    for name, rule in declared.items():
        if rule.get("required") and not args.get(name):
            return {"status": "REFUSED", "reason": f"{tool.name} needs {name}"}

    if spec.get("requires_pending_action") and not tool_context.state.get(corridor.PENDING_KEY):
        return {
            "status": "REFUSED",
            "reason": "there is no pending proposal to confirm",
        }
    return None


def audit_and_receipt(
    tool: BaseTool, args: dict[str, Any], tool_context: Any, tool_response: dict[str, Any]
) -> dict[str, Any] | None:
    """One structured audit line per tool call. Args are hashed, never logged raw."""
    payload = {
        "event": "tool.call",
        "tool": tool.name,
        "args_sha256": hashlib.sha256(
            json.dumps(args, sort_keys=True, default=str).encode()
        ).hexdigest()[:16],
        "status": (tool_response or {}).get("status"),
    }
    if isinstance(tool_response, dict) and "receipt" in tool_response:
        payload["receipt_id"] = tool_response["receipt"]["receipt_id"]
        payload["verified"] = tool_response["receipt"]["verified"]
    _audit(tool_context, payload)
    return None


def _audit(context: Any, payload: dict[str, Any]) -> None:
    record = {**payload, "at": datetime.now(timezone.utc).isoformat()}
    logger.info(json.dumps(record))
    trail = list(context.state.get(corridor.AUDIT_KEY) or [])
    trail.append(record)
    context.state[corridor.AUDIT_KEY] = trail[-50:]
