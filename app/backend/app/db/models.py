from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


class Relation(Base):
    __tablename__ = "relations"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    subject: Mapped[str] = mapped_column(String, index=True)
    predicate: Mapped[str] = mapped_column(String, index=True)
    object: Mapped[str] = mapped_column(String, index=True)
    claim: Mapped[str] = mapped_column(String)
    reason: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String, index=True, default="proposed")
    model: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    linker_version: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=func.now())
    updated_at: Mapped[datetime] = mapped_column(default=func.now(), onupdate=func.now())

    event_time: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    valid_from: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    valid_to: Mapped[Optional[datetime]] = mapped_column(nullable=True)

    bm25: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    cos: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    npmi: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    time_fresh: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    path2: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    novelty: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    uniq_key: Mapped[Optional[str]] = mapped_column(String, unique=True, nullable=True)
    undo_expires_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)

    evidence: Mapped[list["RelationEvidence"]] = relationship(back_populates="relation", cascade="all, delete-orphan")
    audits: Mapped[list["RelationAudit"]] = relationship(back_populates="relation", cascade="all, delete-orphan")



class Note(Base):
    __tablename__ = "notes"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String)
    summary: Mapped[str] = mapped_column(String, default="")
    tags: Mapped[str] = mapped_column(String, default="")
    content: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(default=func.now())
    updated_at: Mapped[datetime] = mapped_column(default=func.now(), onupdate=func.now())
    deleted: Mapped[int] = mapped_column(Integer, default=0)
class RelationEvidence(Base):
    __tablename__ = "rel_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rel_id: Mapped[str] = mapped_column(ForeignKey("relations.id", ondelete="CASCADE"))
    note_id: Mapped[str] = mapped_column(String)
    span: Mapped[str] = mapped_column(String)
    kind: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    quote: Mapped[str] = mapped_column(String)
    quote_sha: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    loc: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    relation: Mapped[Relation] = relationship(back_populates="evidence")


class RelationReject(Base):
    __tablename__ = "rel_rejects"

    uniq_key: Mapped[str] = mapped_column(String, primary_key=True)
    reason: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    decided_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=func.now())


class AuditRun(Base):
    __tablename__ = "audit_runs"

    run_id: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    prompt_hash: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    input_hash: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    cost_cents: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=func.now())

    relation_audits: Mapped[list["RelationAudit"]] = relationship(back_populates="audit_run", cascade="all, delete-orphan")


class RelationAudit(Base):
    __tablename__ = "rel_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rel_id: Mapped[str] = mapped_column(ForeignKey("relations.id", ondelete="CASCADE"))
    run_id: Mapped[str] = mapped_column(ForeignKey("audit_runs.run_id", ondelete="CASCADE"))
    decision: Mapped[str] = mapped_column(String)
    score_before: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    score_after: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    action_source: Mapped[str] = mapped_column(String, default="canvas")

    relation: Mapped[Relation] = relationship(back_populates="audits")
    audit_run: Mapped[AuditRun] = relationship(back_populates="relation_audits")


class ExperienceMetric(Base):
    __tablename__ = "experience_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[datetime] = mapped_column(default=func.now(), unique=True)
    aha_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    acceptance_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    first_hit_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    interruption_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    evidence_open_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)


class Embedding(Base):
    __tablename__ = "embeddings"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    vec: Mapped[bytes] = mapped_column()
    dim: Mapped[int] = mapped_column(Integer)
    model: Mapped[str] = mapped_column(String)
    updated_at: Mapped[datetime] = mapped_column(default=func.now())


class Neighbor(Base):
    __tablename__ = "neigh"

    note_id: Mapped[str] = mapped_column(String, primary_key=True)
    nbr_id: Mapped[str] = mapped_column(String, primary_key=True)
    rank: Mapped[int] = mapped_column(Integer)
    score: Mapped[float] = mapped_column(Float)


class Entity(Base):
    __tablename__ = "entity"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    df: Mapped[int] = mapped_column(Integer, default=0)


class NoteEntity(Base):
    __tablename__ = "note_entity"

    note_id: Mapped[str] = mapped_column(String, primary_key=True)
    entity_id: Mapped[str] = mapped_column(String, primary_key=True)
    cnt: Mapped[int] = mapped_column(Integer, default=1)
