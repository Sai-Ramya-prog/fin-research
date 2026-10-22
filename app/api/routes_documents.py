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
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession

from app.db.engine import get_session
from app.db.models import Document
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

    model_config = {"from_attributes": True}


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
