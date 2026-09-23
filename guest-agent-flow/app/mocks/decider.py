"""Deterministic stand-ins for the three decision models behind the typed port.

All three answer the same questions with the same shape and no network, so the tests
prove the swap rather than the vendor. They differ only in how sharply they separate
candidates (a temperature) and in what leaves the boundary: `jev_api` is hosted, so the
state it sends is redacted to refs first.

Calibration is pinned per (implementation, catalog version). Asking a model to choose
from a choice set it was never calibrated on returns `calibrated: false`, and an
uncalibrated answer is capped below the HIGH band: the ladder asks instead of acting.
"""
import json
import re

from app.audit import redact
from app.decider import (
    CHOICE,
    NOUL,
    SCORE,
    UNCALIBRATED_CEILING,
    calibration,
    catalog_version,
)
from app.registry import catalog


def _keywords(criterion: str) -> list[str]:
    """A criterion is a label; the catalog supplies the words that label is known by."""
    for spec in catalog()["intents"]:
        if spec["name"] == criterion:
            return [*spec["keywords"], criterion.replace("_", " ")]
    return [criterion.replace("_", " ")]


def _score(state: str, criterion: str) -> float:
    text = state.lower()
    # A longer phrase is stronger evidence than a bare word: "family suite" beats "suite".
    return sum(1 + 0.4 * (len(kw.split()) - 1) for kw in _keywords(criterion) if kw in text)


def _probabilities(scores: dict[str, float], confidence: float) -> dict[str, float]:
    """The winner carries the calibrated confidence; the rest split what is left by score."""
    ranked = sorted(scores.items(), key=lambda row: (-row[1], row[0]))
    if not ranked:
        return {}
    rest = ranked[1:]
    total = sum(score for _, score in rest)
    out = {ranked[0][0]: round(confidence, 4)}
    for name, score in rest:
        share = (score / total) if total else 0.0
        out[name] = round((1 - confidence) * share, 4)
    return out


class _Decider:
    """Shared mock body. Subclasses only set identity and temperature."""

    name = "mock"
    version = "0"
    temperature = 1.0

    def _confidence(self, top: float, runner: float) -> float:
        if runner >= 0.7 * top:
            # A near tie is a question, not a decision: a sharper model is still only surer
            # that it cannot separate the two, so this is capped below the HIGH edge.
            return min(0.55 + 0.17 / self.temperature, UNCALIBRATED_CEILING)
        margin = 0.20 * top + 0.12 * (top - runner)
        return min(0.55 + margin / self.temperature, 0.97)

    def _calibrated(self) -> bool:
        return catalog_version() in calibration().get(self.name, {})

    def _prepare(self, state: str) -> str:
        return state

    def _choice(self, state: str, question: dict) -> dict:
        scores = {c: _score(state, c) for c in question["criteria"]}
        ranked = sorted(scores.items(), key=lambda row: (-row[1], row[0]))
        calibrated = self._calibrated()
        if not ranked or ranked[0][1] == 0:
            return {"criterion": None, "probabilities": {}, "calibrated": calibrated,
                    "catalog_version": catalog_version()}
        runner = ranked[1][1] if len(ranked) > 1 else 0.0
        confidence = self._confidence(ranked[0][1], runner)
        if not calibrated:
            confidence = min(confidence, UNCALIBRATED_CEILING)
        return {
            "criterion": ranked[0][0],
            "probabilities": _probabilities({k: v for k, v in scores.items() if v}, confidence),
            "calibrated": calibrated,
            "catalog_version": catalog_version(),
        }

    def _noul(self, state: str, question: dict) -> dict:
        text = state.lower()
        hits = sum(1 for cue in question["criteria"] if cue in text)
        if not question["criteria"]:
            probability = 0.5
        else:
            probability = min(0.5 + 0.22 * hits / self.temperature, 0.98) if hits else 0.12
        return {"probability_yes": round(probability, 4), "probabilities": {},
                "calibrated": self._calibrated(), "catalog_version": catalog_version()}

    def _score_question(self, state: str, question: dict) -> dict:
        levels = question["levels"]
        words = len(re.findall(r"\w+", state))
        value = min(levels, 1 + words // 4)
        return {"value": value, "probabilities": {}, "calibrated": self._calibrated(),
                "catalog_version": catalog_version()}

    def answer(self, state: str, questions: dict[str, dict]) -> dict[str, dict]:
        prepared = self._prepare(state)
        answers: dict[str, dict] = {}
        for qid, question in questions.items():
            if question["type"] == CHOICE:
                answers[qid] = self._choice(prepared, question)
            elif question["type"] == NOUL:
                answers[qid] = self._noul(prepared, question)
            elif question["type"] == SCORE:
                answers[qid] = self._score_question(prepared, question)
            else:
                raise ValueError(f"unknown question type {question['type']}")
            answers[qid]["decider"] = self.name
            answers[qid]["decider_version"] = self.version
        return answers


class SlmIncumbent(_Decider):
    """The trained classifier already in production. Stays warm as the fallback rung."""

    name = "slm_incumbent"
    version = "v3"
    temperature = 1.0


class DevLocal(_Decider):
    """dev-0.4b self-hosted in-VPC: nothing leaves the boundary, sharper than the incumbent."""

    name = "dev_local"
    version = "dev-0.4b"
    temperature = 0.85


class JevApi(_Decider):
    """Offline stand-in for the hosted decider (`app.jev` answers once a key is set).

    The state is redacted to refs before it crosses the boundary, here as there.
    """

    name = "jev_api"
    version = "jev-2026-09"
    temperature = 0.8
    last_egress: str | None = None

    def _prepare(self, state: str) -> str:
        redacted = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "<redacted>", state)
        JevApi.last_egress = json.dumps(redact({"state": redacted}))
        return redacted


IMPLEMENTATIONS = {
    SlmIncumbent.name: SlmIncumbent,
    DevLocal.name: DevLocal,
    JevApi.name: JevApi,
}


def build(name: str):
    if name not in IMPLEMENTATIONS:
        raise ValueError(f"unknown decider {name}: register it before a table row names it")
    return IMPLEMENTATIONS[name]()
