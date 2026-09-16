"""FastAPI surface: POST /turn, POST /confirm, GET /trace/{conversation_id}, and the chat UI.

The UI is a client of the routing decision, not a planner: it posts an utterance plus the
session context it already holds (brand, guest, stay dates) and renders whatever the graph
returns. It never chooses a graph, a tool or a price.
"""
import os
import pathlib
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from langgraph.types import Command
from pydantic import BaseModel

from app import audit
from app.graph_build import build_graph
from app.registry import versions
from app.state import TurnState
from app.tracing import capture_run, run_config

app = FastAPI(title="guest-agent-flow")
GRAPH = build_graph(os.environ.get("CHECKPOINT_PATH", "var/checkpoints.sqlite"))

RUN_URLS: dict[str, str] = {}

STATIC = pathlib.Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


class TurnRequest(BaseModel):
    conversationId: str
    brandId: str
    guestRef: str
    utterance: str
    uiContext: dict = {}


class ConfirmRequest(BaseModel):
    conversationId: str
    proposalId: str
    nonce: str


def _config(conversation_id: str, brand_id: str) -> dict:
    return run_config(conversation_id, brand_id, versions())


def pending(config: dict) -> dict | None:
    """A thread parked at the confirmation gate: the next utterance must not resume it.

    A rejected confirmation re-parks the same task, so the gate is read from the live
    interrupts rather than from `next`, which is empty between resumes.
    """
    snapshot = GRAPH.get_state(config)
    if not (snapshot.next or snapshot.interrupts):
        return None
    proposal = (snapshot.values or {}).get("proposal")
    return proposal.model_dump() if hasattr(proposal, "model_dump") else proposal


@app.post("/turn")
def turn(req: TurnRequest) -> dict:
    config = _config(req.conversationId, req.brandId)
    parked = pending(config)
    if parked is not None:
        raise HTTPException(status_code=409, detail={
            "kind": "pending_confirmation",
            "message": "A booking is awaiting confirmation on this conversation. "
                       "Confirm it or let it expire before starting another turn.",
            "proposal_id": parked["proposal_id"],
            "expires_at": parked["expires_at"],
        })

    state = TurnState(
        conversation_id=req.conversationId,
        turn_id=uuid.uuid4().hex[:12],
        brand_id=req.brandId,
        guest_ref=req.guestRef,
        utterance=req.utterance,
        ui_context=req.uiContext,
        check_in=req.uiContext.get("check_in", ""),
        check_out=req.uiContext.get("check_out", ""),
        clarify_rounds=int(req.uiContext.get("clarify_rounds", 0)),
    )
    with capture_run() as run:
        result = GRAPH.invoke(state, config)
    if run.url:
        RUN_URLS[req.conversationId] = run.url
    return result.get("response") or {"kind": "answer", "text": "", "citations": []}


@app.post("/confirm")
def confirm(req: ConfirmRequest) -> dict:
    config = _config(req.conversationId, "")
    if pending(config) is None:
        raise HTTPException(status_code=409, detail="no booking is awaiting confirmation")
    with capture_run() as run:
        result = GRAPH.invoke(
            Command(resume={"proposal_id": req.proposalId, "nonce": req.nonce}), config
        )
    if run.url:
        RUN_URLS[req.conversationId] = run.url
    response = result.get("response") or {}
    # The run parks again both when the nonce did not match and when the rate moved and the
    # corridor rebuilt the proposal, so the refreshed preview decides which of the two it is.
    if result.get("__interrupt__") and response.get("proposal", {}).get(
        "proposal_id"
    ) in (None, req.proposalId):
        raise HTTPException(status_code=409, detail={
            "kind": "confirmation_rejected",
            "message": "That confirmation does not match the booking I offered. "
                       "Nothing has been booked.",
        })
    # A refreshed preview is a valid outcome of confirming: the rate moved, so we ask again,
    # and a plain answer is the corridor refusing in words (expired, sold out, unstable rate).
    if response.get("kind") not in ("receipt", "preview", "answer"):
        raise HTTPException(status_code=409, detail=response)
    return response


@app.get("/trace/{conversation_id}")
def trace(conversation_id: str) -> dict:
    return {
        "conversation_id": conversation_id,
        "events": audit.read(conversation_id),
        "langsmith_run_url": RUN_URLS.get(conversation_id),
        "versions": versions(),
    }
