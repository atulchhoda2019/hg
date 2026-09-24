"""Gemini behind the same port: structured output, closed options, no prose."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .. import config, models
from ..contracts import Decision
from .port import DeciderUnavailable

DECIDER_PROMPT = """You answer one closed question with one option from the given list.
Return the option verbatim, a calibrated confidence in [0,1], and one short clause of
reasoning. If the options do not cover the case, choose the last option and say so."""


class _Answer(BaseModel):
    choice: str
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""


class GeminiDecider:
    name = "gemini"

    def __init__(self, model_name: str | None = None) -> None:
        self.model_name = model_name or config.gemini_model()

    async def decide(self, question: str, options: list[str], state: dict[str, Any]) -> Decision:
        prompt = (
            f"Question: {question}\nOptions: {options}\n"
            f"Guest said: {state.get('utterance', '')!r}\n"
            f"Known slots: {state.get('slots', {})}"
        )
        try:
            answer = await models.generate_typed(
                self.model_name,
                [models.text_part(prompt)],
                _Answer,
                system_instruction=DECIDER_PROMPT,
            )
        except models.ModelUnavailable as exc:
            raise DeciderUnavailable(str(exc)) from exc
        if options and answer.choice not in options:
            raise DeciderUnavailable(f"off-catalog choice {answer.choice!r}")
        return Decision(
            choice=answer.choice,
            confidence=answer.confidence,
            decider=f"{self.name}:{self.model_name}",
            reason=answer.reason,
        )
