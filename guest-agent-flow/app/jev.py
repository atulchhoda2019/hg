"""The hosted Jev decision API behind the typed port.

Jev answers the same three question types the port already speaks (`choice`, `score`,
`noul`) in one round trip, so this is a transport, not a second interface: the questions
the planner and the screens ask are unchanged and so is the answer shape they read.

Two things the transport owes the boundary. The state is redacted before it leaves
(I6), and a model version is pinned rather than tracking `jev-latest`, because a
calibration pin is only meaningful for the version it was measured on (I8) - so an
answer from an unpinned catalog/version pairing is capped below the HIGH band exactly
as the local implementations are. If the call fails or runs out of time the incumbent
answers instead: a turn never waits on a vendor, and the trace records which model
actually decided.
"""
import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

from app.audit import emit, redact
from app.decider import CHOICE, NOUL, SCORE, UNCALIBRATED_CEILING, calibration, catalog_version

DEFAULT_URL = "https://jevtypesafeai.com/api/v1/decide"
DEFAULT_MODEL = "jev-1.13.0"
RETRY_STATUS = {429, 502, 503, 529}
ATTEMPTS = 3

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


class JevUnavailable(RuntimeError):
    """The hosted model did not answer: the incumbent takes the question."""


def _detail(error: urllib.error.HTTPError) -> str:
    """The vendor's own words: 'insufficient credits' is a different fix from a bad key."""
    try:
        body = json.loads(error.read())
    except Exception:
        return ""
    if isinstance(body, dict):
        detail = body.get("error") or body.get("detail") or ""
        return detail.get("message", "") if isinstance(detail, dict) else str(detail)
    return ""


def api_key() -> str:
    return os.environ.get("JEV_API_KEY") or os.environ.get("TYPESAFE_API_KEY") or ""


def configured() -> bool:
    return bool(api_key())


def endpoint() -> str:
    return os.environ.get("JEV_BASE_URL", DEFAULT_URL)


def model_version() -> str:
    return os.environ.get("JEV_MODEL", DEFAULT_MODEL)


def _post(body: dict) -> dict:
    payload = json.dumps(body).encode()
    request = urllib.request.Request(endpoint(), data=payload, method="POST", headers={
        "Authorization": f"Bearer {api_key()}",
        "Content-Type": "application/json",
    })
    timeout = float(os.environ.get("JEV_TIMEOUT_S", "10"))
    last = ""
    for attempt in range(ATTEMPTS):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            last = f"HTTP {error.code} {_detail(error)}".strip()
            if error.code not in RETRY_STATUS:
                raise JevUnavailable(last) from error
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
            last = str(error) or error.__class__.__name__
        if attempt + 1 == ATTEMPTS:
            break
    raise JevUnavailable(last)


def _question_body(question: dict) -> dict:
    kind = question["type"]
    if kind == CHOICE:
        # Labels only, with no description: a criterion never carries a fact (I8).
        return {"type": CHOICE, "instructions": question["instructions"],
                "criteria": {name: None for name in question["criteria"]}}
    if kind == SCORE:
        levels = question["criteria"] or [f"level {n + 1}" for n in range(question["levels"])]
        return {"type": SCORE, "instructions": question["instructions"], "criteria": list(levels)}
    if kind == NOUL:
        # The local cues are hints for the keyword stand-ins; the hosted model reads the
        # instruction itself, so sending them would only bias it.
        return {"type": NOUL, "instructions": question["instructions"]}
    raise ValueError(f"unknown question type {kind}")


class JevApi:
    """Hosted typed decider. Same name as the local stand-in: the transport is the difference."""

    name = "jev_api"

    def __init__(self, fallback: Any = None) -> None:
        self.version = model_version()
        self.last_usage: dict | None = None
        self.last_error: str | None = None
        self._fallback = fallback

    def _calibrated(self) -> bool:
        pinned = calibration().get(self.name, {}).get(catalog_version(), {})
        return bool(pinned) and pinned.get("measured_version", self.version) == self.version

    def _redact(self, state: str) -> str:
        return _EMAIL.sub("<redacted>", state)

    def _answer_body(self, qid: str, question: dict, answer: dict, calibrated: bool) -> dict:
        out = {"probabilities": {}, "calibrated": calibrated,
               "catalog_version": catalog_version(),
               "decider": self.name, "decider_version": self.version}
        if question["type"] == CHOICE:
            winner = answer["choice"]
            # A vendor may answer a near-certain choice without the full vector.
            vector = answer.get("probabilities") or {winner: answer.get("confidence", 1.0)}
            probabilities = {k: round(float(v), 4) for k, v in vector.items()}
            if winner is not None and not calibrated:
                probabilities[winner] = min(probabilities.get(winner, 0.0), UNCALIBRATED_CEILING)
            out.update({"criterion": winner, "probabilities": probabilities})
        elif question["type"] == NOUL:
            # Not capped: a noul screen is advisory, it never lifts a band or acts.
            out["probability_yes"] = round(float(answer["noul"]), 4)
        else:
            out["value"] = float(answer["score"])
        return out

    def answer(self, state: str, questions: dict[str, dict]) -> dict[str, dict]:
        """One round trip for the whole batch; anything unusable is the incumbent's question."""
        try:
            response = _post({
                "model": self.version,
                "state": redact({"state": self._redact(state)})["state"],
                "questions": {qid: _question_body(q) for qid, q in questions.items()},
            })
            self.last_usage = response.get("usage")
            self.version = response.get("model", self.version)
            calibrated = self._calibrated()
            self.last_error = None
            return {qid: self._answer_body(qid, question, response["answers"][qid], calibrated)
                    for qid, question in questions.items()}
        except (JevUnavailable, KeyError, TypeError, ValueError) as error:
            reason = str(error) or error.__class__.__name__
            self.last_error = reason
            emit({"node": "decider.fallback", "decider": self.name,
                  "reason": reason, "answered_by": self.fallback().name})
            return self.fallback().answer(state, questions)

    def fallback(self):
        if self._fallback is None:
            from app.mocks import decider as implementations

            self._fallback = implementations.build("slm_incumbent")
        return self._fallback
