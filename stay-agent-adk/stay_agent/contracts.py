"""Typed contracts. Every value that crosses a boundary in this app is one of these.

Money exists in exactly one shape (`Quote`) and enters the conversation through exactly
one tool (`get_live_quote`). Room attributes are a different temperature entirely: they
are versioned, carry provenance, and may never hold a price-like field (I5).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

Ambiguity = Literal["HIGH", "MEDIUM", "LOW"]
ActionKind = Literal["BOOK", "ATTRIBUTE_UPSELL", "CANCEL"]


class SearchSlots(BaseModel):
    """What the slot extractor is allowed to return. No prose, no prices.

    `ambiguity` is the extractor's read of the request, not of its own confidence:
    HIGH means the request carries enough to act, LOW means one question is owed.
    """

    property_id: str | None = None
    market: str | None = None
    check_in: date | None = None
    check_out: date | None = None
    guests: int = 2
    min_floor: int | None = None
    view: Literal["park", "street", "courtyard", "any"] = "any"
    connecting: bool | None = None
    max_distance_to_elevator_m: float | None = None
    accessible: bool | None = None
    ambiguity: Ambiguity = "MEDIUM"


class Provenance(BaseModel):
    """Why the app believes an attribute. Returned verbatim by `explain_room`."""

    attribute_version: str
    source_doc_id: str
    extractor_version: str
    approver: str | None = None
    committed_at: datetime


class RoomAttributes(BaseModel):
    room_id: str
    property_id: str
    floor: int
    view: str | None = None
    connecting_with: list[str] = Field(default_factory=list)
    distance_to_elevator_m: float | None = None
    corner: bool | None = None
    accessible: bool | None = None
    area_sqm: float | None = None
    provenance: Provenance | None = None


class RoomHit(BaseModel):
    """A search result. Deliberately has no price field at all."""

    room_id: str
    property_id: str
    floor: int
    view: str | None = None
    connecting_with: list[str] = Field(default_factory=list)
    distance_to_elevator_m: float | None = None
    accessible: bool | None = None
    attribute_version: str
    snippet: str | None = None


class Quote(BaseModel):
    """The only source of money in the app."""

    quote_id: str
    property_id: str
    room_id: str
    nightly: Decimal
    total: Decimal
    currency: str = "USD"
    nights: int = 1
    rate_version: str
    expires_at: datetime


class ActionProposal(BaseModel):
    kind: ActionKind
    payload: dict = Field(default_factory=dict)
    quote_id: str | None = None
    preview_text: str
    nonce: str
    expires_at: datetime


class Receipt(BaseModel):
    receipt_id: str
    kind: str
    reservation_id: str
    verified: bool
    amount: Decimal | None = None
    currency: str | None = None
    rate_version: str | None = None
    at: datetime


class ExtractedRoom(BaseModel):
    """What the extractor proposes per room. It never reaches the store directly (I6)."""

    room_id: str
    floor: int
    view: str | None = None
    connecting_with: list[str] = Field(default_factory=list)
    distance_to_elevator_m: float | None = None
    corner: bool | None = None
    accessible: bool | None = None
    area_sqm: float | None = None
    confidence: dict[str, float] = Field(default_factory=dict)


class DocClass(BaseModel):
    """Output schema of the classify step."""

    doc_type: Literal["FLOOR_PLAN", "BROCHURE", "UNKNOWN"]
    quality_band: Literal["CLEAN", "SCANNED", "FAX"]
    property_id: str
    floors: list[int]
    notes: str = ""


class Finding(BaseModel):
    rule: str
    severity: Literal["ESCALATE", "WARN"]
    room_id: str | None = None
    detail: str


class ValidationReport(BaseModel):
    job_id: str
    findings: list[Finding] = Field(default_factory=list)

    @property
    def escalations(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "ESCALATE"]


class GateDecision(BaseModel):
    decision: Literal["AUTO_COMMIT", "ESCALATE"]
    reason: str
    findings: list[Finding] = Field(default_factory=list)


class CommitReceipt(BaseModel):
    job_id: str
    property_id: str
    attribute_version: str
    rooms_written: int
    approver: str | None = None
    at: datetime


class Decision(BaseModel):
    """What every decider returns, whoever the decider is."""

    choice: str
    confidence: float
    decider: str
    reason: str = ""
