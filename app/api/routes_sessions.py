"""
app/api/routes_sessions.py
───────────────────────────────────────────────────────────────────────────────
Session CRUD endpoints (Task 10).

Routes
------
  POST   /sessions                         Create a new session
  GET    /sessions                         List all sessions (newest first)
  GET    /sessions/{session_id}            Get a session with its documents
  DELETE /sessions/{session_id}            Delete session + cascade documents
  POST   /sessions/{session_id}/documents  Upload a PDF → background processing
"""

from __future__ import annotations

import io
import logging
import re
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session as DBSession

from app import storage
from app.agents.document_agent.graph import run_document_agent
from app.db.engine import get_session
from app.db.models import Document, Session

logger = logging.getLogger(__name__)

router = APIRouter()

# 50 MB hard limit
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
PDF_MAGIC = b"%PDF"


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic schemas
# ─────────────────────────────────────────────────────────────────────────────

class SessionCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, description="Session display name")


class DocumentOut(BaseModel):
    id: uuid.UUID
    filename: str
    company: Optional[str]
    fiscal_year: Optional[int]
    doc_type: Optional[str]
    status: str
    page_count: Optional[int]
    error_message: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class SessionOut(BaseModel):
    id: uuid.UUID
    name: str
    created_at: datetime
    document_count: int = 0

    model_config = {"from_attributes": True}


class SessionDetailOut(BaseModel):
    id: uuid.UUID
    name: str
    created_at: datetime
    documents: List[DocumentOut] = []

    model_config = {"from_attributes": True}


# ─────────────────────────────────────────────────────────────────────────────
# Dependency
# ─────────────────────────────────────────────────────────────────────────────

def _db() -> DBSession:
    yield from get_session()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _assert_pdf(data: bytes, filename: str) -> None:
    """Raise 422 if the file doesn't look like a PDF."""
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Only PDF files are accepted (.pdf extension required).",
        )
    if data[:4] != PDF_MAGIC:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="File does not appear to be a valid PDF (bad magic bytes).",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "",
    response_model=SessionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new research session",
)
def create_session(
    body: SessionCreate,
    db: DBSession = Depends(_db),
) -> SessionOut:
    """Create a named research session that will hold uploaded documents."""
    session = Session(name=body.name.strip())
    db.add(session)
    db.flush()  # populate session.id before returning
    db.commit()
    db.refresh(session)
    return SessionOut.model_validate(session)


@router.get(
    "",
    response_model=List[SessionOut],
    summary="List all sessions (newest first)",
)
def list_sessions(db: DBSession = Depends(_db)) -> List[SessionOut]:
    """Return all sessions ordered by creation time descending."""
    sessions = db.query(Session).order_by(Session.created_at.desc()).all()
    counts = dict(
        db.query(Document.session_id, func.count(Document.id))
        .group_by(Document.session_id)
        .all()
    )
    out: List[SessionOut] = []
    for s in sessions:
        item = SessionOut.model_validate(s)
        item.document_count = int(counts.get(s.id, 0))
        out.append(item)
    return out


@router.get(
    "/{session_id}",
    response_model=SessionDetailOut,
    summary="Get a session with its documents",
)
def get_session_detail(
    session_id: uuid.UUID,
    db: DBSession = Depends(_db),
) -> SessionDetailOut:
    """Return a single session including the full list of its documents."""
    session = db.get(Session, session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found",
        )
    return SessionDetailOut.model_validate(session)


@router.delete(
    "/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a session and all its documents",
)
def delete_session(
    session_id: uuid.UUID,
    db: DBSession = Depends(_db),
) -> None:
    """
    Delete the session.  All child documents are cascade-deleted by the
    database (``ondelete=CASCADE``).  Storage files and Chroma chunks are
    NOT removed here – use ``DELETE /documents/{id}`` for each document first.
    """
    session = db.get(Session, session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found",
        )
    db.delete(session)
    db.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Document upload (nested under session)
# Path: POST /sessions/{session_id}/documents
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/{session_id}/documents",
    response_model=DocumentOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload a PDF and start processing",
)
async def upload_document(
    session_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="PDF file to upload"),
    company: str = Form(..., description="Company name (e.g. 'Infosys')"),
    fiscal_year: str = Form(..., description="Fiscal year (e.g. 'FY2024')"),
    db: DBSession = Depends(_db),
) -> DocumentOut:
    """
    Upload a PDF for a session.

    1. Validates the file (PDF, ≤ 50 MB, safe filename).
    2. Uploads to cloud storage.
    3. Creates a ``Document`` DB row with ``status="uploaded"``.
    4. Schedules the Document Agent pipeline as a background task.

    Poll ``GET /documents/{id}`` to track progress.
    """
    # 1. Validate session exists
    session = db.get(Session, session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found",
        )

    # 2. Read and validate file
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large. Maximum size is {MAX_UPLOAD_BYTES // (1024*1024)} MB.",
        )
    original_filename = file.filename or "upload.pdf"
    _assert_pdf(data, original_filename)

    # 3. Upload to storage
    doc_id = uuid.uuid4()
    storage_key = storage.make_storage_key(doc_id, original_filename)
    storage.save(io.BytesIO(data), storage_key)

    # 4. Parse fiscal_year to int if possible
    # Accept "FY2024", "2024", "2024-25" – store the first 4-digit year
    fy_int: Optional[int] = None
    try:
        match = re.search(r"\d{4}", fiscal_year)
        if match:
            fy_int = int(match.group())
    except Exception:
        pass

    # 5. Create Document DB row
    doc = Document(
        id=doc_id,
        session_id=session_id,
        filename=original_filename,
        storage_key=storage_key,
        company=company.strip() or None,
        fiscal_year=fy_int,
        status="uploaded",
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    logger.info(
        "Document %s uploaded: filename=%s  company=%s  fy=%s",
        doc_id, original_filename, company, fiscal_year,
    )

    # 6. Background processing – Document Agent pipeline
    background_tasks.add_task(run_document_agent, doc_id)

    return DocumentOut.model_validate(doc)
