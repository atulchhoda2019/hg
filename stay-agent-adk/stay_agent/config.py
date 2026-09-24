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


def gemini_model() -> str:
    # An alias rather than a pinned id: new Developer API keys are refused by older
    # explicit versions with "no longer available to new users".
    return _flag("STAY_GEMINI_MODEL", "gemini-flash-latest")


def gemma_model() -> str:
    return _flag("STAY_GEMMA_MODEL", "gemma-4-31b-it")


def decider_mode() -> str:
    """`table` honours decision_table.yaml; the others pin every decision to one decider."""
    return _flag("STAY_DECIDER_MODE", "table")


def use_vertex() -> bool:
    return _flag("GOOGLE_GENAI_USE_VERTEXAI", "FALSE").upper() in {"1", "TRUE", "YES"}


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
