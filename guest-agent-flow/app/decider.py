"""The typed-decision port.

A decision is a typed question over a closed answer set: `choice` over candidates,
`score` over ordinal levels, `noul` for yes/no with a probability. Everything the
runtime asks a model to decide goes through this one interface, so swapping the model
behind it is a decision-table field rather than a change to the graph.

The choice set is part of the model input (invariant I8): probabilities only mean
something relative to the catalog version they were calibrated on, so the version
travels with every answer and an uncalibrated pairing can never reach the HIGH band.
"""
import json
import os
from typing import Any, Iterable, Protocol, runtime_checkable

from app.registry import FIXTURES_DIR, catalog, decision_table

CHOICE = "choice"
SCORE = "score"
NOUL = "noul"

UNCALIBRATED_CEILING = 0.84  # just under the HIGH edge: never act on an unpinned pairing


def choice_question(instructions: str, criteria: Iterable[str]) -> dict[str, Any]:
    """Criteria are labels only. Facts belong in `state`; a criterion never carries one."""
    return {"type": CHOICE, "instructions": instructions, "criteria": list(criteria)}


def score_question(instructions: str, levels: int) -> dict[str, Any]:
    if not 2 <= levels <= 10:
        raise ValueError("an ordinal question has between 2 and 10 levels")
    return {"type": SCORE, "instructions": instructions, "criteria": [], "levels": levels}


def noul_question(instructions: str, cues: Iterable[str] = ()) -> dict[str, Any]:
    return {"type": NOUL, "instructions": instructions, "criteria": list(cues)}


@runtime_checkable
class TypedDecider(Protocol):
    """One forward pass answers a batch of typed questions about one state."""

    name: str
    version: str

    def answer(self, state: str, questions: dict[str, dict]) -> dict[str, dict]:
        """{qid: {type, instructions, criteria}} -> {qid: {criterion|value|probability_yes,
        probabilities, calibrated, catalog_version}}"""


def catalog_version() -> str:
    return catalog()["version"]


def calibration() -> dict[str, Any]:
    """Measured quality per (implementation, catalog version). Nothing else may claim it."""
    with (FIXTURES_DIR / "decider_calibration.json").open() as fh:
        return json.load(fh)


def configured(brand_id: str) -> str:
    """Which implementation answers for this brand: env override, brand pin, then default."""
    config = decision_table().get("deciders", {}) or {}
    return (
        os.environ.get("DECIDER")
        or (config.get("by_brand") or {}).get(brand_id)
        or config.get("default", "slm_incumbent")
    )


def get(name: str) -> TypedDecider:
    """The named implementation, hosted when it is configured and local otherwise.

    `jev_api` is one decider with two transports: the hosted model answers when a key is
    present, the deterministic stand-in when there is none, so tests and CI stay offline
    without a second name in the decision table.
    """
    from app import jev
    from app.mocks import decider as implementations

    if name == jev.JevApi.name and jev.configured():
        return jev.JevApi()
    return implementations.build(name)


def for_brand(brand_id: str) -> TypedDecider:
    return get(configured(brand_id))
