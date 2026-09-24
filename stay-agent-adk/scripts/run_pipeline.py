"""Run the room truth pipeline over one or more fixture floor plans.

    python scripts/run_pipeline.py PR-1 PR-2
"""

from __future__ import annotations

import asyncio
import json
import sys

from stay_agent.mocks import attribute_store
from stay_agent.room_truth import pipeline
from stay_agent.tracing import setup_tracing


async def main(doc_ids: list[str]) -> None:
    setup_tracing()
    for doc_id in doc_ids:
        result = await pipeline.run_job(doc_id)
        print(
            json.dumps(
                {k: v for k, v in result.items() if k != "rooms"},
                indent=2,
                default=str,
            )
        )
    print(f"current attribute version: {attribute_store.current_version()}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or ["PR-1"]))
