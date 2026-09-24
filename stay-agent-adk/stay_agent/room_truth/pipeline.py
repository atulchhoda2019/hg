"""The room truth pipeline, assembled two ways from one set of steps.

`run_job` is the plain async function the review UI, the tests and the CLI call.
`build_pipeline()` is the same five steps as an ADK `SequentialAgent`, so the run is
visible in `adk web` with a trace per step. Neither wraps the other in a special case:
both call the identical step functions below.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from google.adk.agents import BaseAgent, SequentialAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from google.genai import types

from ..contracts import ExtractedRoom, GateDecision
from ..tracing import span
from . import commit as commit_module
from . import queue
from .classify_agent import classify
from .extract_agent import extract
from .gate_agent import gate
from .intake import intake
from .validate_agent import validate


async def run_job(doc_id: str) -> dict[str, Any]:
    """Intake -> classify -> extract -> validate -> gate -> commit or escalate."""
    with span("room_truth.job", doc_id=doc_id) as current:
        job = intake(doc_id)
        with span("room_truth.classify", job_id=job["job_id"]) as classify_span:
            doc_class = await classify(job["document"], doc_id)
            classify_span.set_attribute("quality_band", doc_class.quality_band)
        with span("room_truth.extract", floors=len(job["document"].get("floors", []))) as extract_span:
            rooms = await extract(job)
            extract_span.set_attribute("rooms", len(rooms))
        with span("room_truth.validate") as validate_span:
            report = validate(job, rooms)
            validate_span.set_attribute("findings", len(report.findings))
        decision = gate(doc_class, rooms, report)
        current.set_attribute("quality_band", doc_class.quality_band)
        current.set_attribute("decision", decision.decision)
        current.set_attribute("reason", decision.reason)

    result: dict[str, Any] = {
        "job_id": job["job_id"],
        "doc_id": doc_id,
        "property_id": job["property_id"],
        "quality_band": doc_class.quality_band,
        "decision": decision.decision,
        "reason": decision.reason,
        "rooms": [room.model_dump() for room in rooms],
        "findings": [finding.model_dump() for finding in decision.findings],
    }

    if decision.decision == "AUTO_COMMIT":
        receipt = commit_module.commit(job, rooms)
        result["receipt"] = receipt.model_dump(mode="json")
    else:
        queue.enqueue(
            {
                "job_id": job["job_id"],
                "doc_id": doc_id,
                "property_id": job["property_id"],
                "renovation_declared": job["renovation_declared"],
                "reason": decision.reason,
                "rooms": result["rooms"],
                "findings": result["findings"],
            }
        )
    return result


def resume_after_review(
    job_id: str, *, approver: str, fixes: list[dict[str, Any]]
) -> dict[str, Any]:
    """A human resolved an escalation; re-validate the fixed rooms, then commit.

    The fixes are applied to the extraction and the deterministic validator runs again.
    A human can correct a value; a human cannot wave through a non-waivable rule (I7).
    """
    job_row = queue.get(job_id)
    if job_row is None:
        raise KeyError(job_id)

    rooms = [ExtractedRoom(**room) for room in job_row["rooms"]]
    by_id = {room.room_id: room for room in rooms}
    for fix in fixes:
        room = by_id.get(fix["room_id"])
        if room is None:
            room = ExtractedRoom(room_id=fix["room_id"], floor=int(str(fix["room_id"])[0]))
            by_id[room.room_id] = room
        if fix["field"] == "__delete__":
            by_id.pop(fix["room_id"], None)
            continue
        fix["extracted"] = getattr(room, fix["field"], None)
        setattr(room, fix["field"], fix["value"])
        room.confidence[fix["field"]] = 1.0

    fixed = list(by_id.values())
    job = intake(job_row["doc_id"])
    report = validate(job, fixed)
    if report.escalations:
        return {
            "job_id": job_id,
            "decision": "STILL_ESCALATED",
            "findings": [f.model_dump() for f in report.escalations],
        }

    queue.resolve(job_id, approver=approver, fixes=fixes)
    receipt = commit_module.commit(job, fixed, approver=approver)
    return {
        "job_id": job_id,
        "decision": "COMMITTED",
        "receipt": receipt.model_dump(mode="json"),
    }


def _event(author: str, ctx: InvocationContext, text: str) -> Event:
    return Event(
        author=author,
        invocation_id=ctx.invocation_id,
        content=types.Content(role="model", parts=[types.Part(text=text)]),
    )


class IntakeAgent(BaseAgent):
    """Code, not a model: hash, name, and park the document in state."""

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        doc_id = ctx.session.state.get("doc_id", "PR-1")
        job = intake(doc_id)
        ctx.session.state["job"] = {k: v for k, v in job.items() if k != "document"}
        ctx.session.state["document"] = job["document"]
        yield _event(self.name, ctx, f"intake {job['job_id']} ({job['property_id']})")


class ClassifyAgent(BaseAgent):
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        doc_class = await classify(ctx.session.state["document"], ctx.session.state["job"]["doc_id"])
        ctx.session.state["doc_class"] = doc_class.model_dump()
        yield _event(self.name, ctx, f"{doc_class.doc_type}, band {doc_class.quality_band}")


class ExtractFanoutAgent(BaseAgent):
    """One extraction call per floor, concurrently. A bad floor fails alone."""

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        job = {**ctx.session.state["job"], "document": ctx.session.state["document"]}
        rooms = await extract(job)
        ctx.session.state["extracted_rooms"] = [room.model_dump() for room in rooms]
        yield _event(self.name, ctx, f"extracted {len(rooms)} rooms")


class ValidateAgent(BaseAgent):
    """Deterministic. No model is constructed here, on purpose."""

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        rooms = [ExtractedRoom(**room) for room in ctx.session.state["extracted_rooms"]]
        report = validate(ctx.session.state["job"], rooms)
        ctx.session.state["validation"] = report.model_dump()
        yield _event(
            self.name,
            ctx,
            f"{len(report.escalations)} escalating finding(s), {len(report.findings)} total",
        )


class GateAgent(BaseAgent):
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        from ..contracts import DocClass, ValidationReport

        state = ctx.session.state
        rooms = [ExtractedRoom(**room) for room in state["extracted_rooms"]]
        decision: GateDecision = gate(
            DocClass(**state["doc_class"]), rooms, ValidationReport(**state["validation"])
        )
        state["gate"] = decision.model_dump()
        job = state["job"]
        if decision.decision == "AUTO_COMMIT":
            receipt = commit_module.commit(job, rooms)
            state["commit_receipt"] = receipt.model_dump(mode="json")
            text = f"AUTO_COMMIT -> {receipt.attribute_version} ({receipt.rooms_written} rooms)"
        else:
            queue.enqueue(
                {
                    "job_id": job["job_id"],
                    "doc_id": job["doc_id"],
                    "property_id": job["property_id"],
                    "renovation_declared": job["renovation_declared"],
                    "reason": decision.reason,
                    "rooms": state["extracted_rooms"],
                    "findings": [f.model_dump() for f in decision.findings],
                }
            )
            text = f"ESCALATE -> review queue: {decision.reason}"
        yield _event(self.name, ctx, text)


def build_pipeline() -> SequentialAgent:
    return SequentialAgent(
        name="room_truth_pipeline",
        description="Floor plan to versioned room attributes with provenance.",
        sub_agents=[
            IntakeAgent(name="intake"),
            ClassifyAgent(name="classify"),
            ExtractFanoutAgent(name="extract_fanout"),
            ValidateAgent(name="validate"),
            GateAgent(name="gate"),
        ],
    )
