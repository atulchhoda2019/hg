"""Environment-facing configuration, read once and in one place.

The app has two honest modes. With `GOOGLE_API_KEY` set it talks to the Gemini Developer
API (Gemini for language and multimodal extraction, Gemma for typed choices). Without a
key it runs the same graph with the deterministic fixture extractor and the rules decider,
which is why `pytest -q` is green offline and why the demo can be replayed on a train.
"""

from __future__ import annotations

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).parent


def _flag(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def use_vertex() -> bool:
    return _flag("GOOGLE_GENAI_USE_VERTEXAI", "FALSE").upper() in {"1", "TRUE", "YES"}


def gemini_model() -> str:
    # Developer API: an alias rather than a pinned id, because new keys are refused by
    # older explicit versions with "no longer available to new users". Vertex does not
    # serve the aliases, so a deployed agent gets an id it can actually resolve.
    return _flag("STAY_GEMINI_MODEL", "gemini-2.5-flash" if use_vertex() else "gemini-flash-latest")


def gemma_model() -> str:
    # Vertex only serves Gemma from a self-deployed Model Garden endpoint, so the small
    # model on Vertex is the cheap Gemini tier; the decider port does not care which.
    return _flag(
        "STAY_GEMMA_MODEL", "gemini-2.5-flash-lite" if use_vertex() else "gemma-4-31b-it"
    )


def decider_mode() -> str:
    """`table` honours decision_table.yaml; the others pin every decision to one decider."""
    return _flag("STAY_DECIDER_MODE", "table")


def has_model_credentials() -> bool:
    if use_vertex():
        return bool(_flag("GOOGLE_CLOUD_PROJECT"))
    return bool(_flag("GOOGLE_API_KEY") or _flag("GEMINI_API_KEY"))


def classifier_backend() -> str:
    """`model` reads the PNG with Gemini; `fixture` reads the committed JSON plan."""
    return _flag("STAY_CLASSIFIER", "model" if has_model_credentials() else "fixture")


def extractor_backend() -> str:
    return _flag("STAY_EXTRACTOR", "model" if has_model_credentials() else "fixture")


EXTRACTOR_VERSION = "stay-extractor-v1"
