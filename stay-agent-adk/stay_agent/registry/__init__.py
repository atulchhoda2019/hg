"""Registry loader. The YAML in this package is the configuration surface of the app.

Loading asserts the invariants that must hold before a single turn runs: the attribute
schema may not contain a price-shaped field (I5), and every tool the concierge is built
with must be a row in `actions.yaml` (I3).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_DIR = Path(__file__).parent


def _load(name: str) -> dict[str, Any]:
    with (_DIR / name).open() as fh:
        return yaml.safe_load(fh)


class SchemaViolation(AssertionError):
    """Raised at load time. The app must not start with a price in the attribute store."""


@lru_cache(maxsize=1)
def attribute_schema() -> dict[str, Any]:
    schema = _load("attribute_schema.yaml")
    forbidden = tuple(schema["forbidden_field_prefixes"])
    for field in schema["fields"]:
        if field.startswith(forbidden):
            raise SchemaViolation(
                f"attribute_schema.yaml declares '{field}', which is price- or "
                "availability-shaped; those are live reads, never versioned attributes (I5)"
            )
    return schema


@lru_cache(maxsize=1)
def actions() -> dict[str, Any]:
    return _load("actions.yaml")


@lru_cache(maxsize=1)
def validation_rules() -> dict[str, Any]:
    return _load("validation_rules.yaml")


@lru_cache(maxsize=1)
def decision_table() -> dict[str, Any]:
    return _load("decision_table.yaml")


def tool_spec(tool_name: str) -> dict[str, Any] | None:
    return actions()["tools"].get(tool_name)


def is_registered(tool_name: str) -> bool:
    return tool_name in actions()["tools"]


def field_bounds(field: str) -> tuple[float | None, float | None]:
    spec = attribute_schema()["fields"].get(field, {})
    return spec.get("min"), spec.get("max")
