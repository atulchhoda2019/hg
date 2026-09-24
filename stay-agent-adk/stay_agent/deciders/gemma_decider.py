"""Gemma behind the same port.

Same contract as Gemini, different economics: a small typed model for the fast closed
choices (ambiguity band, intent route). It can be served by the Gemini API, by Vertex, or
by vLLM/Ollama near the property through ADK's LiteLlm wrapper — set `STAY_GEMMA_MODEL` to
something like `openai/gemma-4-e4b` and `STAY_GEMMA_API_BASE` to the local server, and the
rest of the app does not notice.
"""

from __future__ import annotations

import json
import os
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from .. import config, models
from ..contracts import Decision
from .port import DeciderUnavailable

GEMMA_PROMPT = """Answer with JSON only: {"choice": <one option verbatim>,
"confidence": <0..1>, "reason": <short clause>}. Choose from the options given."""


class _Answer(BaseModel):
    choice: str
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""


class GemmaDecider:
    name = "gemma"

    def __init__(self, model_name: str | None = None) -> None:
        self.model_name = model_name or config.gemma_model()
        self.api_base = os.environ.get("STAY_GEMMA_API_BASE", "").strip()

    def _llm_name(self) -> str:
        return self.model_name

    async def decide(self, question: str, options: list[str], state: dict[str, Any]) -> Decision:
        prompt = (
            f"Question: {question}\nOptions: {json.dumps(options)}\n"
            f"Guest said: {state.get('utterance', '')!r}\n"
            f"Known slots: {json.dumps(state.get('slots', {}), default=str)}"
        )
        try:
            answer = await models.generate_typed(
                self._llm_name(),
                [models.text_part(prompt)],
                _Answer,
                system_instruction=GEMMA_PROMPT,
            )
        except (models.ModelUnavailable, ValidationError) as exc:
            raise DeciderUnavailable(str(exc)) from exc
        if options and answer.choice not in options:
            raise DeciderUnavailable(f"off-catalog choice {answer.choice!r}")
        return Decision(
            choice=answer.choice,
            confidence=answer.confidence,
            decider=f"{self.name}:{self.model_name}",
            reason=answer.reason,
        )
