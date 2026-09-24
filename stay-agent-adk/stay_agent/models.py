"""One typed call to a Google model, through ADK's own model classes.

Everything that talks to Gemini or Gemma in this app goes through `generate_typed`, so
there is a single place where structured output is enforced, where a model failure turns
into a typed `ModelUnavailable` instead of a guess, and where the caller can be sure the
result was parsed rather than believed.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import TypeVar

from google.adk.models.llm_request import LlmRequest
from google.adk.models.registry import LLMRegistry
from google.genai import types
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


class ModelUnavailable(RuntimeError):
    """The model did not answer, or did not answer in the shape it was asked for."""


def _llm(model_name: str):
    return LLMRegistry.new_llm(model_name)


async def generate_typed(
    model_name: str,
    parts: Sequence[types.Part],
    schema: type[T],
    *,
    system_instruction: str = "",
    temperature: float = 0.0,
) -> T:
    """Ask `model_name` for exactly one `schema` instance. No prose is accepted."""
    llm = _llm(model_name)
    config = types.GenerateContentConfig(
        temperature=temperature,
        system_instruction=system_instruction or None,
        response_mime_type="application/json",
    )
    # Gemma serves JSON but not response_schema; Gemini takes the schema directly.
    if not model_name.startswith("gemma"):
        config.response_schema = schema
    else:
        config.system_instruction = (
            f"{system_instruction}\n\nReply with JSON only, matching this schema:\n"
            f"{json.dumps(schema.model_json_schema())}"
        ).strip()

    request = LlmRequest(
        model=model_name,
        contents=[types.Content(role="user", parts=list(parts))],
        config=config,
    )

    text = ""
    try:
        async for response in llm.generate_content_async(request, stream=False):
            if response.error_message:
                raise ModelUnavailable(response.error_message)
            for part in (response.content.parts if response.content else []) or []:
                text += part.text or ""
    except ModelUnavailable:
        raise
    except Exception as exc:  # network, auth, quota: all the same to the caller
        raise ModelUnavailable(str(exc)) from exc

    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    if not text:
        raise ModelUnavailable("empty model response")
    try:
        return schema.model_validate_json(_json_object(text))
    except ValidationError as exc:
        raise ModelUnavailable(f"model returned an off-schema object: {exc}") from exc


def _json_object(text: str) -> str:
    """The first balanced ``{...}`` in the text.

    Models without server-side schema enforcement (Gemma) often narrate before the object.
    Narration around a well-formed object is tolerated; a wrong object still fails.
    """
    start = text.find("{")
    if start == -1:
        return text
    depth = 0
    in_string = escaped = False
    for i, char in enumerate(text[start:], start):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return text


def text_part(text: str) -> types.Part:
    return types.Part.from_text(text=text)


def image_part(path: str, data: bytes) -> types.Part:
    mime = "image/png" if path.endswith(".png") else "image/jpeg"
    return types.Part.from_bytes(data=data, mime_type=mime)
