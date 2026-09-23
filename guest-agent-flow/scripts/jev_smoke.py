#!/usr/bin/env python
"""One live question to the hosted Jev API: endpoint, auth, payload, answer shape.

The golden-set harness sends thirty utterances and spends real credit; this sends one, so
a new key or a new pinned version is proved for a fraction of a cent before anything else
runs. Nothing here is part of a turn.

    JEV_API_KEY=jv_live_... python scripts/jev_smoke.py
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import decider as decider_port  # noqa: E402
from app import jev  # noqa: E402
from app.nodes import planner  # noqa: E402

UTTERANCE = "family suite near the park, under $300, with breakfast"


def main() -> int:
    if not jev.configured():
        print("no JEV_API_KEY / TYPESAFE_API_KEY: nothing to call")
        return 2

    model = jev.JevApi()
    print(f"POST {jev.endpoint()}  model={model.version}")
    answer = model.answer(UTTERANCE, {
        "intent": decider_port.choice_question(
            "Which guest journey is this utterance asking for?", planner.choice_set()),
    })["intent"]

    if answer["decider"] != jev.JevApi.name:
        print(f"the hosted call did not answer; {answer['decider']} did. See the audit log.")
        return 1

    top = sorted(answer["probabilities"].items(), key=lambda kv: -kv[1])[:3]
    print(f"answered by {answer['decider_version']}  calibrated={answer['calibrated']}")
    print(f"criterion   {answer['criterion']}")
    print(f"top 3       {json.dumps(dict(top))}")
    print(f"usage       {json.dumps(model.last_usage)}")
    if not answer["calibrated"]:
        print(f"\nthis version has no calibration for {answer['catalog_version']}: capped below "
              f"HIGH ({decider_port.UNCALIBRATED_CEILING}) until the harness measures it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
