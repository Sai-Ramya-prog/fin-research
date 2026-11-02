"""
app/db/models.py
─────────────────────────────────────────────────────────────────────────────
SQLAlchemy 2.x ORM models.

All primary keys are UUIDs so IDs are safe to expose in URLs / APIs.
Tables are created / versioned by Alembic – never call Base.metadata.create_all()
in application code.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


# ── helpers ───────────────────────────────────────────────────────────────────

def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── base ──────────────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


# ── Session ───────────────────────────────────────────────────────────────────

class Session(Base):
    """
    A user-created research session that groups one or more Documents.
    """

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    # relationships
    documents: Mapped[list[Document]] = relationship(
        "Document", back_populates="session", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Session id={self.id!s} name={self.name!r}>"


# ── Document ──────────────────────────────────────────────────────────────────

DocumentStatus = Enum(
    "uploaded",
    "parsing",
    "tagging",
    "chunking",
    "indexing",
    "ready",
    "failed",
    name="document_status",
)


class Document(Base):
    """
    Represents a single uploaded PDF.  status tracks pipeline progress.
    """

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    company: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fiscal_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    doc_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(
        DocumentStatus, nullable=False, default="uploaded", server_default="uploaded"
    )
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Milestone 2 agents. Values: queued | running | done | failed | skipped
    # (None for documents processed before Milestone 2).
    extraction_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    red_flag_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    analysis_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    # relationships
    session: Mapped[Session] = relationship("Session", back_populates="documents")
    page_tags: Mapped[list[PageTag]] = relationship(
        "PageTag", back_populates="document", cascade="all, delete-orphan"
    )
    metrics: Mapped[list[Metric]] = relationship(
        "Metric", back_populates="document", cascade="all, delete-orphan"
    )
    red_flags: Mapped[list[RedFlag]] = relationship(
        "RedFlag", back_populates="document", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<Document id={self.id!s} filename={self.filename!r} status={self.status!r}>"
        )


# ── PageTag ───────────────────────────────────────────────────────────────────

class PageTag(Base):
    """
    Tagging metadata produced by the Document Agent (Task 5).
    One row per PDF page that was tagged.
    """

    __tablename__ = "page_tags"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    pdf_page: Mapped[int] = mapped_column(Integer, nullable=False)        # 1-indexed
    printed_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section: Mapped[str | None] = mapped_column(String(255), nullable=True)
    scope: Mapped[str | None] = mapped_column(String(100), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(50), nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_image_only: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # Tiered indexing: every page's text is stored so tier-2 pages can be found
    # by keyword and embedded later, without re-reading the PDF.
    tier: Mapped[int] = mapped_column(
        Integer, nullable=False, default=2, server_default="2"
    )
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    indexed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    # relationships
    document: Mapped[Document] = relationship("Document", back_populates="page_tags")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<PageTag doc={self.document_id!s} pdf_page={self.pdf_page}>"


# ── Metric ────────────────────────────────────────────────────────────────────

class Metric(Base):
    """
    One financial number for one period, written by the Extraction Agent.

    kind = "extracted"  read from a statement page (source_page + source_text)
    kind = "ratio"      computed in code from other metrics (formula says how)
    status = "verified" passed the source and consistency checks
    status = "needs_review" failed a check; ``note`` says which one
    """

    __tablename__ = "metrics"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    value: Mapped[float | None] = mapped_column(Numeric(20, 4), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(50), nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scope: Mapped[str | None] = mapped_column(String(100), nullable=True)
    kind: Mapped[str] = mapped_column(
        String(20), nullable=False, default="extracted", server_default="extracted"
    )
    source_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    formula: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="verified", server_default="verified"
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # relationships
    document: Mapped[Document] = relationship("Document", back_populates="metrics")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Metric doc={self.document_id!s} name={self.name!r}>"


# ── RedFlag ───────────────────────────────────────────────────────────────────

class RedFlag(Base):
    """
    One warning found by the Red Flag Agent. ``type`` is the rule id
    (e.g. "falling_margin"); ``evidence`` is the numbers or the quoted sentence
    that triggered it, and ``source_page`` is where it came from.
    """

    __tablename__ = "red_flags"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    type: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    severity: Mapped[str] = mapped_column(
        String(10), nullable=False, default="Medium", server_default="Medium"
    )
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)

    # relationships
    document: Mapped[Document] = relationship("Document", back_populates="red_flags")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<RedFlag doc={self.document_id!s} type={self.type!r}>"
