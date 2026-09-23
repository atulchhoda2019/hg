"""Run every registered decider against the same frozen golden set and compare.

Promotion is a decision-table edit, so the evidence for one has to be measured here, on
our data and our choice set: top-1, top-3, expected calibration error, abstention
quality, latency and cost per 1k decisions. A calibration number from a launch post is
not evidence about this catalog version.

    python scripts/decider_experiment.py [--json]
"""
import argparse
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from app import decider as decider_port  # noqa: E402
from app import jev  # noqa: E402
from app.mocks import decider as implementations  # noqa: E402
from app.nodes.planner import choice_set  # noqa: E402

GOLDEN = pathlib.Path(__file__).parent.parent / "fixtures" / "golden_intents.jsonl"
BUCKETS = 10
# Published list price per 1k decisions; the point of the column is that it is compared.
COST_PER_1K = {"slm_incumbent": 0.00, "dev_local": 0.06, "jev_api": 0.90}
OUT_OF_SCOPE = "__out_of_scope__"


def golden_set() -> list[dict]:
    with GOLDEN.open() as fh:
        return [json.loads(line) for line in fh if line.strip()]


def expected_calibration_error(pairs: list[tuple[float, bool]]) -> float:
    """Bucketed |confidence - accuracy|, weighted by bucket size."""
    if not pairs:
        return 0.0
    error = 0.0
    for bucket in range(BUCKETS):
        low, high = bucket / BUCKETS, (bucket + 1) / BUCKETS
        members = [p for p in pairs if low <= p[0] < high or (bucket == BUCKETS - 1 and p[0] == 1.0)]
        if not members:
            continue
        mean_confidence = sum(c for c, _ in members) / len(members)
        accuracy = sum(1 for _, hit in members if hit) / len(members)
        error += (len(members) / len(pairs)) * abs(mean_confidence - accuracy)
    return round(error, 4)


def measured_cost_per_1k(spent: dict) -> float | None:
    """A hosted call reports what it cost; list price is only used when nothing did."""
    if not spent["calls"]:
        return None
    return round(1000 * spent["usd"] / spent["calls"], 4)


def evaluate(name: str, rows: list[dict]) -> dict:
    model = decider_port.get(name)
    spent = {"calls": 0, "usd": 0.0}
    question = {"intent": decider_port.choice_question(
        "Which guest journey is this utterance asking for?", choice_set())}
    top1 = top3 = 0
    abstained_right = abstained_wrong = 0
    pairs: list[tuple[float, bool]] = []
    latencies: list[float] = []

    for row in rows:
        started = time.perf_counter()
        answer = model.answer(row["utterance"], question)["intent"]
        latencies.append((time.perf_counter() - started) * 1000)
        call = getattr(model, "last_usage", None) or {}
        if call.get("cost_usd"):
            spent["calls"] += 1
            spent["usd"] += call["cost_usd"]
        ranked = [n for n, _ in sorted(answer["probabilities"].items(),
                                       key=lambda item: (-item[1], item[0]))]
        picked = answer["criterion"] or OUT_OF_SCOPE
        confidence = answer["probabilities"].get(answer["criterion"], 0.0)
        hit = picked == row["intent"]
        top1 += int(hit)
        top3 += int(row["intent"] in ranked[:3] or (not ranked and row["intent"] == OUT_OF_SCOPE))
        pairs.append((confidence, hit))
        if picked == OUT_OF_SCOPE:
            abstained_right += int(row["intent"] == OUT_OF_SCOPE)
            abstained_wrong += int(row["intent"] != OUT_OF_SCOPE)

    latencies.sort()
    total = len(rows)
    return {
        "decider": name,
        "decider_version": model.version,
        "transport": "hosted" if isinstance(model, jev.JevApi) else "local",
        "catalog_version": decider_port.catalog_version(),
        "calibrated": bool(getattr(model, "_calibrated")()),
        "top1": round(top1 / total, 4),
        "top3": round(top3 / total, 4),
        "ece": expected_calibration_error(pairs),
        "abstentions_correct": abstained_right,
        "abstentions_wrong": abstained_wrong,
        "p50_ms": round(latencies[len(latencies) // 2], 3),
        "p95_ms": round(latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))], 3),
        "cost_per_1k": measured_cost_per_1k(spent) or COST_PER_1K.get(name, 0.0),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    rows = golden_set()
    results = [evaluate(name, rows) for name in implementations.IMPLEMENTATIONS]
    if args.json:
        print(json.dumps({"n": len(rows), "results": results}, indent=2))
        return 0

    print(f"golden set: {len(rows)} utterances, catalog {decider_port.catalog_version()}\n")
    header = f"{'decider':<16}{'ver':<14}{'via':<9}{'top1':>7}{'top3':>7}{'ece':>8}" \
             f"{'abst+/-':>10}{'p50ms':>8}{'p95ms':>8}{'$/1k':>8}  calibrated"
    print(header)
    print("-" * len(header))
    for row in results:
        print(f"{row['decider']:<16}{row['decider_version']:<14}{row['transport']:<9}"
              f"{row['top1']:>7.2f}"
              f"{row['top3']:>7.2f}{row['ece']:>8.3f}"
              f"{str(row['abstentions_correct']) + '/' + str(row['abstentions_wrong']):>10}"
              f"{row['p50_ms']:>8.2f}{row['p95_ms']:>8.2f}{row['cost_per_1k']:>8.2f}"
              f"  {row['calibrated']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
