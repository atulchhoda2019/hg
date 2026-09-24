"""The review UI: the human end of the escalation queue.

Deliberately small. It lists escalated jobs, shows the findings the deterministic
validator produced, takes field-level fixes, and re-runs validation before committing with
the approver's name on the version. A reviewer can correct a value; a reviewer cannot
waive room-id reconciliation (I7).

    uvicorn review_ui.app:app --port 8400
"""

from __future__ import annotations

import html
from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from stay_agent.room_truth import pipeline, queue
from stay_agent.room_truth.gate_agent import waivable
from stay_agent.tracing import setup_tracing

setup_tracing("stay-agent-adk-review-ui")
app = FastAPI(title="Room truth review")


class Fix(BaseModel):
    room_id: str
    field: str
    value: Any = None


class Resolution(BaseModel):
    approver: str
    fixes: list[Fix] = []


@app.get("/api/jobs")
def list_jobs() -> list[dict[str, Any]]:
    return queue.open_jobs()


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    job = queue.get(job_id)
    if job is None:
        return {"status": "UNKNOWN_JOB", "job_id": job_id}
    return {
        **job,
        "blocking_rules": sorted(
            {f["rule"] for f in job["findings"] if not waivable(f["rule"])}
        ),
    }


@app.post("/api/jobs/{job_id}/resolve")
def resolve(job_id: str, resolution: Resolution) -> dict[str, Any]:
    """Apply the fixes, re-validate, and commit only if validation is now clean."""
    return pipeline.resume_after_review(
        job_id,
        approver=resolution.approver,
        fixes=[fix.model_dump() for fix in resolution.fixes],
    )


@app.post("/api/jobs/run/{doc_id}")
async def run_doc(doc_id: str) -> dict[str, Any]:
    """Upload equivalent: run the pipeline over a fixture floor plan."""
    return await pipeline.run_job(doc_id)


@app.get("/api/labels")
def labels() -> list[dict[str, Any]]:
    """Every human fix, as a labeled example. The queue is the training pipeline."""
    return queue.golden_labels()


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    rows = []
    for job in queue.open_jobs():
        findings = "".join(
            f"<li><code>{html.escape(f['rule'])}</code> "
            f"{'(not waivable)' if not waivable(f['rule']) else ''} "
            f"{html.escape(f.get('room_id') or '')} — {html.escape(f['detail'])}</li>"
            for f in job["findings"]
            if f["severity"] == "ESCALATE"
        )
        rows.append(
            f"<article><h3>{html.escape(job['job_id'])}</h3>"
            f"<p>{html.escape(job['property_id'])} · {html.escape(job['doc_id'])} · "
            f"{html.escape(job['reason'])}</p><ul>{findings}</ul>"
            f"<pre>curl -X POST localhost:8400/api/jobs/{job['job_id']}/resolve "
            f"-H 'content-type: application/json' -d '{{\"approver\":\"you\",\"fixes\":"
            f"[{{\"room_id\":\"...\",\"field\":\"view\",\"value\":\"park\"}}]}}'</pre></article>"
        )
    body = "".join(rows) or "<p>No open escalations.</p>"
    return (
        "<!doctype html><meta charset='utf-8'><title>Room truth review</title>"
        "<style>body{font:15px/1.5 system-ui;margin:2rem auto;max-width:56rem;color:#111}"
        "article{border:1px solid #ddd;border-radius:8px;padding:1rem;margin:1rem 0}"
        "pre{background:#f6f6f6;padding:.6rem;overflow:auto;font-size:12px}</style>"
        "<h1>Room truth review queue</h1>"
        "<p>Escalations are documents a human can fix, never rejected uploads. "
        "Every fix is appended to <code>golden_labels.json</code>.</p>" + body
    )
