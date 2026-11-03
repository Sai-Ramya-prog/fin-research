"""
app/api/routes_documents.py
───────────────────────────────────────────────────────────────────────────────
Document status and management endpoints (Task 10).

Routes
------
  GET    /documents/{document_id}   Poll processing status / error
  DELETE /documents/{document_id}   Remove file + DB row + Chroma chunks

The upload route (POST /sessions/{session_id}/documents) lives in
``routes_sessions.py`` so that it is correctly mounted under the /sessions
prefix.

Validation (upload) is handled in routes_sessions.py:
  - Content-Type must be application/pdf (checked via magic bytes and extension).
  - File size limit: MAX_UPLOAD_BYTES (50 MB default).
  - Filename is sanitised with storage._safe_filename before use.

Background processing
---------------------
  After the DB row is created with status="uploaded", the Document Agent is
  scheduled as a FastAPI BackgroundTask.  The agent transitions the status
  through: parsing → tagging → chunking → indexing → ready (or failed).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Optional

from fastapi import (
    BackgroundTasks,
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Response,
    status,
)
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session as DBSession

from app.db.engine import get_session
from app.db.models import Document, Metric, PageTag, RedFlag
from app import storage
from app.index import _get_collection  # for Chroma chunk deletion

logger = logging.getLogger(__name__)

router = APIRouter()


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic schemas
# ─────────────────────────────────────────────────────────────────────────────

class DocumentStatusOut(BaseModel):
    id: uuid.UUID
    status: str
    page_count: Optional[int]
    error_message: Optional[str]
    extraction_status: Optional[str] = None
    red_flag_status: Optional[str] = None

    model_config = {"from_attributes": True}


class DocumentListItem(BaseModel):
    id: uuid.UUID
    filename: str
    company: Optional[str]
    fiscal_year: Optional[int]
    status: str
    extraction_status: Optional[str] = None
    red_flag_status: Optional[str] = None

    model_config = {"from_attributes": True}


class MetricOut(BaseModel):
    name: str
    year: Optional[int]
    value: Optional[float]
    unit: Optional[str]
    kind: str
    status: str
    source_page: Optional[int]
    source_text: Optional[str]
    formula: Optional[str]
    note: Optional[str]

    model_config = {"from_attributes": True}


class RedFlagOut(BaseModel):
    type: str
    severity: str
    title: Optional[str]
    description: str
    evidence: Optional[str]
    source_page: Optional[int]

    model_config = {"from_attributes": True}


class PageOut(BaseModel):
    pdf_page: int
    printed_page: Optional[int]
    section: Optional[str]
    tier: int
    indexed: bool
    is_image_only: bool

    model_config = {"from_attributes": True}


class SectionCount(BaseModel):
    section: str
    pages: int


class PageMapOut(BaseModel):
    page_count: int
    tier1: int
    embedded: int
    sections: list[SectionCount]
    pages: list[PageOut]


class ReviewItem(BaseModel):
    name: str
    year: Optional[int]
    value: Optional[float]
    unit: Optional[str]
    source_page: Optional[int]
    note: Optional[str]


class ValidationOut(BaseModel):
    extracted_total: int
    verified: int
    needs_review: int
    ratios_total: int
    ratios_verified: int
    statement_pages: dict[str, list[int]]
    missing_statements: list[str]
    review_items: list[ReviewItem]
    checks: list[str]
    golden_matched: Optional[int] = None
    golden_total: Optional[int] = None


class IndexMoreIn(BaseModel):
    query: str
    limit: int = 5


class IndexMoreOut(BaseModel):
    embedded_pages: list[int]


# ─────────────────────────────────────────────────────────────────────────────
# Dependency
# ─────────────────────────────────────────────────────────────────────────────

def _db() -> DBSession:
    yield from get_session()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _delete_chroma_chunks(document_id: uuid.UUID) -> None:
    """Remove all Chroma chunks whose doc_id matches document_id."""
    try:
        collection = _get_collection()
        existing = collection.get(
            where={"doc_id": str(document_id)}, include=[]
        )
        if existing and existing.get("ids"):
            collection.delete(ids=existing["ids"])
            logger.info(
                "Deleted %d Chroma chunks for document %s",
                len(existing["ids"]),
                document_id,
            )
    except Exception as exc:
        # Log but don't fail the HTTP response – the DB row is already gone.
        logger.warning(
            "Could not delete Chroma chunks for document %s: %s", document_id, exc
        )


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/{document_id}",
    response_model=DocumentStatusOut,
    summary="Get document processing status",
)
def get_document(
    document_id: uuid.UUID,
    db: DBSession = Depends(_db),
) -> DocumentStatusOut:
    """
    Return the current processing status of a document.

    Poll this endpoint every 2-3 seconds while ``status`` is not ``ready`` or
    ``failed``.  Stop polling once either terminal state is reached.
    """
    doc = db.get(Document, document_id)
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document {document_id} not found",
        )
    backfill_page_counts(db, [doc])
    return DocumentStatusOut.model_validate(doc)


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a document (storage + DB + Chroma chunks)",
)
def delete_document(
    document_id: uuid.UUID,
    db: DBSession = Depends(_db),
) -> None:
    """
    Permanently delete a document:
    1. Remove the file from cloud storage.
    2. Delete Chroma chunks where ``doc_id == document_id``.
    3. Delete the DB row (PageTags and other children cascade).
    """
    doc = db.get(Document, document_id)
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document {document_id} not found",
        )

    storage_key = doc.storage_key

    # 1. Delete from cloud storage (log but don't fail if already gone)
    try:
        storage.delete(storage_key)
    except Exception as exc:
        logger.warning("Could not delete storage file %s: %s", storage_key, exc)

    # 2. Delete Chroma chunks
    _delete_chroma_chunks(document_id)

    # 3. Delete DB row
    db.delete(doc)
    db.commit()
    logger.info("Document %s deleted", document_id)


@router.post(
    "/{document_id}/index-more",
    response_model=IndexMoreOut,
    summary="Embed extra (tier-2) pages that match a question",
)
def index_more_pages(
    document_id: uuid.UUID,
    body: IndexMoreIn,
    db: DBSession = Depends(_db),
) -> IndexMoreOut:
    """
    Tiered indexing embeds only the important pages after upload. Call this
    when a question needs other pages: the best keyword matches among the
    not-yet-embedded pages are embedded and become searchable.
    """
    from app.embeddings import QuotaExhaustedError
    from app.lazy_index import index_more

    doc = db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document {document_id} not found")
    try:
        pages = index_more(document_id, body.query, limit=max(1, min(body.limit, 20)))
    except QuotaExhaustedError as exc:
        raise HTTPException(status_code=429, detail=str(exc))
    return IndexMoreOut(embedded_pages=pages)


_SEVERITY_ORDER = {"High": 0, "Medium": 1, "Low": 2}


@router.get(
    "",
    response_model=list[DocumentListItem],
    summary="All documents with their analysis status",
)
def list_documents(db: DBSession = Depends(_db)) -> list[Document]:
    """Used by the Metrics, Compare and Red flags screens to know which
    companies have results."""
    docs = db.query(Document).order_by(Document.company, Document.fiscal_year).all()
    backfill_page_counts(db, docs)
    return docs


@router.get(
    "/{document_id}/metrics",
    response_model=list[MetricOut],
    summary="Metrics and ratios extracted from the document",
)
def get_metrics(document_id: uuid.UUID, db: DBSession = Depends(_db)) -> list[Metric]:
    """Every extracted number (with its page and source line) and every ratio
    computed from them. ``status`` is ``needs_review`` when a check failed."""
    if db.get(Document, document_id) is None:
        raise HTTPException(status_code=404, detail=f"Document {document_id} not found")
    return (
        db.query(Metric)
        .filter(Metric.document_id == document_id)
        .order_by(Metric.kind, Metric.name, Metric.year.desc())
        .all()
    )


@router.get(
    "/{document_id}/red-flags",
    response_model=list[RedFlagOut],
    summary="Red flags found in the document",
)
def get_red_flags(document_id: uuid.UUID, db: DBSession = Depends(_db)) -> list[RedFlag]:
    if db.get(Document, document_id) is None:
        raise HTTPException(status_code=404, detail=f"Document {document_id} not found")
    flags = db.query(RedFlag).filter(RedFlag.document_id == document_id).all()
    return sorted(flags, key=lambda f: _SEVERITY_ORDER.get(f.severity, 3))


@router.post(
    "/{document_id}/analyze",
    response_model=DocumentStatusOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Re-run the Extraction and Red Flag agents",
)
def analyze_document(
    document_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: DBSession = Depends(_db),
) -> Document:
    """Use after a failure (for example Gemini quota) or after changing a rule.
    The document must already be ``ready``."""
    from app.agents.orchestrator import run_analysis

    doc = db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document {document_id} not found")
    if doc.status != "ready":
        raise HTTPException(status_code=409, detail="Document is not ready yet")
    doc.extraction_status = "queued"
    doc.red_flag_status = "queued"
    db.commit()
    background_tasks.add_task(run_analysis, document_id)
    return doc


# ─────────────────────────────────────────────────────────────────────────────
# Page map, source page image, validation
# ─────────────────────────────────────────────────────────────────────────────

_STATEMENTS = ("profit_loss", "balance_sheet", "cash_flow")
_CHECKS = [
    "Every number appears on the page it cites",
    "Assets = liabilities + equity (within 0.5%)",
    "Current assets, cash and gross profit stay within their totals",
    "Net profit does not exceed revenue",
    "Ratios are computed in code, never by the AI",
]


def backfill_page_counts(db: DBSession, docs: list[Document]) -> None:
    """Older documents were processed before page_count was saved; fill it
    from the stored pages so the Pages column is never empty."""
    missing = [d for d in docs if d.page_count is None]
    if not missing:
        return
    counts = dict(
        db.query(PageTag.document_id, func.count(PageTag.id))
        .filter(PageTag.document_id.in_([d.id for d in missing]))
        .group_by(PageTag.document_id)
        .all()
    )
    changed = False
    for d in missing:
        n = int(counts.get(d.id, 0))
        if n:
            d.page_count = n
            changed = True
    if changed:
        db.commit()


@router.get(
    "/{document_id}/detail",
    response_model=DocumentListItem,
    summary="One document with its analysis status",
)
def document_detail(document_id: uuid.UUID, db: DBSession = Depends(_db)) -> Document:
    doc = db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document {document_id} not found")
    backfill_page_counts(db, [doc])
    return doc


@router.get(
    "/{document_id}/pages",
    response_model=PageMapOut,
    summary="Section tag and tier of every page (the page map)",
)
def get_page_map(document_id: uuid.UUID, db: DBSession = Depends(_db)) -> PageMapOut:
    if db.get(Document, document_id) is None:
        raise HTTPException(status_code=404, detail=f"Document {document_id} not found")
    rows = (
        db.query(PageTag)
        .filter(PageTag.document_id == document_id)
        .order_by(PageTag.pdf_page)
        .all()
    )
    by_section: dict[str, int] = {}
    for r in rows:
        key = r.section or "other"
        by_section[key] = by_section.get(key, 0) + 1
    return PageMapOut(
        page_count=len(rows),
        tier1=sum(1 for r in rows if r.tier == 1),
        embedded=sum(1 for r in rows if r.indexed),
        sections=[SectionCount(section=k, pages=v) for k, v in sorted(by_section.items(), key=lambda kv: -kv[1])],
        pages=[PageOut.model_validate(r) for r in rows],
    )


@router.get(
    "/{document_id}/pages/{page}/image",
    summary="Render one PDF page as a PNG (optionally highlighting a line)",
    responses={200: {"content": {"image/png": {}}}},
)
def get_page_image(
    document_id: uuid.UUID,
    page: int,
    q: Optional[str] = Query(default=None, max_length=300, description="text to highlight"),
    db: DBSession = Depends(_db),
) -> Response:
    from app.page_render import render_page

    doc = db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document {document_id} not found")
    if page < 1 or (doc.page_count and page > doc.page_count):
        raise HTTPException(status_code=404, detail="Page out of range")
    try:
        png = render_page(document_id, doc.storage_key, page, q)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="The PDF file is no longer in storage")
    except IndexError:
        raise HTTPException(status_code=404, detail="Page out of range")
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})


@router.get(
    "/{document_id}/validation",
    response_model=ValidationOut,
    summary="How trustworthy the extracted numbers are",
)
def get_validation(document_id: uuid.UUID, db: DBSession = Depends(_db)) -> ValidationOut:
    doc = db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document {document_id} not found")
    metrics = db.query(Metric).filter(Metric.document_id == document_id).all()
    extracted = [m for m in metrics if m.kind != "ratio"]
    ratios = [m for m in metrics if m.kind == "ratio"]

    stmt_pages: dict[str, list[int]] = {}
    for sec, pg in (
        db.query(PageTag.section, PageTag.pdf_page)
        .filter(PageTag.document_id == document_id, PageTag.section.in_(_STATEMENTS))
        .order_by(PageTag.pdf_page)
        .all()
    ):
        stmt_pages.setdefault(sec, []).append(pg)

    out = ValidationOut(
        extracted_total=len(extracted),
        verified=sum(1 for m in extracted if m.status == "verified"),
        needs_review=sum(1 for m in extracted if m.status != "verified"),
        ratios_total=len(ratios),
        ratios_verified=sum(1 for m in ratios if m.status == "verified"),
        statement_pages=stmt_pages,
        missing_statements=[s for s in _STATEMENTS if s not in stmt_pages],
        review_items=[
            ReviewItem(
                name=m.name, year=m.year, value=float(m.value) if m.value is not None else None,
                unit=m.unit, source_page=m.source_page, note=m.note,
            )
            for m in extracted
            if m.status != "verified"
        ],
        checks=_CHECKS,
    )
    _add_golden_score(out, doc, extracted)
    return out


def _add_golden_score(out: ValidationOut, doc: Document, extracted: list[Metric]) -> None:
    """For documents that have a hand-checked answer file (the Apple extract),
    show how many numbers match it exactly."""
    import json
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "tests" / "golden" / f"{(doc.company or '').lower()}_fy{doc.fiscal_year}.json"
    if not path.exists():
        return
    try:
        from app.validate_agents import _golden_expected

        expected = _golden_expected(json.loads(path.read_text()))
    except Exception:  # pragma: no cover - the score is optional
        return
    got = {
        (m.name, "current" if m.year == doc.fiscal_year else "prior"): float(m.value)
        for m in extracted
        if m.value is not None
    }
    hits = sum(1 for k, v in expected.items() if k in got and abs(got[k] - v) < 1e-6)
    out.golden_total = len(expected)
    out.golden_matched = hits
