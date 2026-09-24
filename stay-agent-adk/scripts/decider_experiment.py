"""Compare the deciders on the same frozen questions: accuracy, latency, cost.

The point of the decider port is that the choice is evidence, not preference. This prints
one row per decider over `tests/eval/golden_decisions.jsonl`, so promoting Gemma over
Gemini (or rules over both) is a table edit backed by a number.

    python scripts/decider_experiment.py               # every decider
    DECIDER=gemma python scripts/decider_experiment.py # one of them
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from statistics import median

from stay_agent import config
from stay_agent.deciders import build
from stay_agent.deciders.port import DeciderUnavailable
from stay_agent.registry import decision_table

GOLDEN = Path(__file__).resolve().parents[1] / "tests" / "eval" / "golden_decisions.jsonl"

# Published list prices per million tokens, and the rough token shape of one decision.
COST_PER_1K_DECISIONS = {"rules": 0.0, "gemma": 0.02, "gemini": 0.11}


def cases() -> list[dict]:
    with GOLDEN.open() as fh:
        return [json.loads(line) for line in fh if line.strip()]


async def run(decider_name: str) -> dict:
    decider = build(decider_name)
    table = decision_table()["decisions"]
    latencies: list[float] = []
    correct = 0
    failures = 0
    rows = cases()
    for case in rows:
        row = table[case["decision_type"]]
        started = time.perf_counter()
        try:
            decision = await decider.decide(row["question"], row["options"], case["state"])
            choice = decision.choice
        except DeciderUnavailable:
            failures += 1
            choice = None
        latencies.append((time.perf_counter() - started) * 1000)
        correct += int(choice == case["expected"])
    latencies.sort()
    p95 = latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))]
    return {
        "decider": decider_name,
        "n": len(rows),
        "accuracy": correct / len(rows),
        "p50_ms": median(latencies),
        "p95_ms": p95,
        "unavailable": failures,
        "usd_per_1k": COST_PER_1K_DECISIONS[decider_name],
    }


async def main() -> None:
    names = [os.environ["DECIDER"]] if os.environ.get("DECIDER") else ["rules", "gemma", "gemini"]
    if not config.has_model_credentials():
        names = [n for n in names if n == "rules"] or names[:1]
        print("no GOOGLE_API_KEY: model deciders would fall back, running rules only\n")
    print(f"{'decider':10} {'n':>4} {'acc':>7} {'p50 ms':>8} {'p95 ms':>8} {'fail':>5} {'$/1k':>6}")
    for name in names:
        row = await run(name)
        print(
            f"{row['decider']:10} {row['n']:>4} {row['accuracy']:>7.2%} "
            f"{row['p50_ms']:>8.1f} {row['p95_ms']:>8.1f} {row['unavailable']:>5} "
            f"{row['usd_per_1k']:>6.2f}"
        )


if __name__ == "__main__":
    asyncio.run(main())
