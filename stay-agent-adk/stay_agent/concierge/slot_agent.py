"""The slot extractor: an LlmAgent with an output schema and therefore no tools.

Keeping it toolless is the point. It converts a sentence into `SearchSlots` and writes
them to `slots`; every read of the world happens in a tool the concierge calls next,
under the registry gate.
"""

from __future__ import annotations

from datetime import date

from google.adk.agents import LlmAgent
from google.adk.agents.readonly_context import ReadonlyContext

from .. import config
from ..contracts import SearchSlots
from .corridor import SLOTS_KEY
from .prompts import SLOT_PROMPT


def _instruction(_: ReadonlyContext) -> str:
    """Resolved per invocation: "12 June" is only a date if the model knows today's."""
    return SLOT_PROMPT.format(today=date.today().isoformat())


def build_slot_agent() -> LlmAgent:
    return LlmAgent(
        name="slot_extractor",
        model=config.gemini_model(),
        description="Turns a stay request into typed SearchSlots.",
        instruction=_instruction,
        output_schema=SearchSlots,
        output_key=SLOTS_KEY,
    )
