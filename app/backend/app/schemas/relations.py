from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class CanvasSubmitRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=4000)
    note_id: Optional[str] = None
    predicate: Optional[str] = Field(default="supports")
    as_of: Optional[datetime] = None
    budget_cents: Optional[int] = None


class EvidenceSnippet(BaseModel):
    note: str
    span: str
    quote: str
    quote_sha: Optional[str] = None


class CandidatePayload(BaseModel):
    id: str
    subject: str
    predicate: str
    object: str
    claim: str
    explain: str
    confidence: float
    event_time: Optional[datetime]
    valid_from: Optional[datetime]
    valid_to: Optional[datetime]
    evidence: list[EvidenceSnippet]
    scores: dict[str, float]
    degraded: bool = False


class CanvasSubmitResponse(BaseModel):
    run_id: str
    candidate: Optional[CandidatePayload] = None
    guidance: Optional[str] = None
    budget_used: int = 0
    degraded: bool = False


class SuggestResponse(BaseModel):
    candidate: Optional[CandidatePayload]
    degraded: bool = False


class RelationDecision(BaseModel):
    id: str
    action: Literal["verify", "reject", "undo"]
    reason: Optional[str] = None


class DecideRequest(BaseModel):
    ops: list[RelationDecision] = Field(..., min_items=1)


class DecideResponse(BaseModel):
    ok: int
    failed: list[str]
    undo_expires_at: Optional[datetime]


class SwapRequest(BaseModel):
    id: str
    subject: str
    predicate: Optional[str] = "supports"
    as_of: Optional[datetime] = None


class ReviewCard(BaseModel):
    position: int
    total: int
    candidate: CandidatePayload


class ReviewDailyResponse(BaseModel):
    cards: list[ReviewCard]


class AuditArtifact(BaseModel):
    step: str
    path: str


class AuditResponse(BaseModel):
    relation_id: str
    run_id: str
    status: str
    created_at: datetime
    prompt_hash: Optional[str]
    input_hash: Optional[str]
    cost_cents: Optional[int]
    artifacts: list[AuditArtifact]
    notes: Optional[str] = None
