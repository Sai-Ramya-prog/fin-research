"""
app/api/routes_ask.py
───────────────────────────────────────────────────────────────────────────────
Ask & report endpoints. Mounted under /sessions.

  GET    /sessions/{id}/companies                    companies + fiscal years in the session
  GET    /sessions/{id}/report?company=&fiscal_year= readiness + the saved report (or null)
  POST   /sessions/{id}/report/generate              start (or, with regenerate=true, redo) it
  GET    /sessions/{id}/report/pdf?company=&fiscal_year=   download the report as PDF
  GET    /sessions/{id}/chat?company=&fiscal_year=   chat history
  POST   /sessions/{id}/chat                         ask a question (strict, one company)
  DELETE /sessions/{id}/chat?company=&fiscal_year=   clear the chat

The company is always upper-cased, so "infosys", "Infosys" and "INFOSYS" are
the same company everywhere.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DBSession

from app.agents.chat_agent import graph as chat
from app.agents.report_agent import data
from app.agents.report_agent import graph as report_graph
from app.agents.report_agent.graph import NotFound, NotReady
from app.agents.report_agent.pdf import build_pdf
from app.company import normalize_company
from app.db.engine import get_session, session_scope
from app.db.models import Document, Session
from app.embeddings import QuotaExhaustedError

logger = logging.getLogger(__name__)
router = APIRouter()


def _db():
    yield from get_session()


# ── schemas ───────────────────────────────────────────────────────────────────

class YearOut(BaseModel):
    fiscal_year: int
    document_id: str
    filename: str
    ready: bool
    reason: Optional[str] = None


class CompanyOut(BaseModel):
    company: str
    years: List[YearOut]


class ReadinessOut(BaseModel):
    ready: bool
    reason: Optional[str] = None


class ReportStateOut(BaseModel):
    company: str
    fiscal_year: int
    readiness: ReadinessOut
    report: Optional[dict[str, Any]] = None


class ChatIn(BaseModel):
    company: str = Field(..., min_length=1)
    fiscal_year: int
    question: str = Field(..., min_length=1, max_length=2000)


# ── helpers ───────────────────────────────────────────────────────────────────

def _require_session(db: DBSession, session_id: uuid.UUID) -> None:
    if db.get(Session, session_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Session {session_id} not found")


def _readiness(session_id: uuid.UUID, company: str, fiscal_year: int) -> dict[str, Any]:
    with session_scope() as db:
        doc = data.find_document(db, session_id, company, fiscal_year)
        if doc is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND,
                                f"No document for {normalize_company(company)} FY{fiscal_year} in this session.")
        return data.readiness(doc)


def _http(exc: Exception) -> HTTPException:
    if isinstance(exc, NotFound):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    return HTTPException(status.HTTP_409_CONFLICT, str(exc))


# ── companies ─────────────────────────────────────────────────────────────────

@router.get("/{session_id}/companies", response_model=List[CompanyOut],
            summary="Companies (and fiscal years) available in this session")
def list_companies(session_id: uuid.UUID, db: DBSession = Depends(_db)) -> List[dict]:
    _require_session(db, session_id)
    return data.list_companies(session_id)


# ── report ────────────────────────────────────────────────────────────────────

@router.get("/{session_id}/report", response_model=ReportStateOut,
            summary="Readiness and the saved report (null when none yet)")
def get_report(session_id: uuid.UUID, company: str = Query(..., min_length=1), fiscal_year: int = Query(...),
               db: DBSession = Depends(_db)) -> dict:
    _require_session(db, session_id)
    ready = _readiness(session_id, company, fiscal_year)
    return {"company": normalize_company(company), "fiscal_year": fiscal_year, "readiness": ready,
            "report": report_graph.get_report(session_id, company, fiscal_year)}


@router.post("/{session_id}/report/generate", response_model=ReportStateOut,
             status_code=status.HTTP_202_ACCEPTED, summary="Generate the report (cached unless regenerate=true)")
def generate_report(session_id: uuid.UUID, background_tasks: BackgroundTasks,
                    company: str = Query(..., min_length=1), fiscal_year: int = Query(...),
                    regenerate: bool = Query(False), db: DBSession = Depends(_db)) -> dict:
    _require_session(db, session_id)
    try:
        report_id, should_run = report_graph.start_report(session_id, company, fiscal_year, regenerate)
    except (NotFound, NotReady) as exc:
        raise _http(exc)
    if should_run:
        background_tasks.add_task(report_graph.run_report, report_id)
    return {"company": normalize_company(company), "fiscal_year": fiscal_year,
            "readiness": {"ready": True, "reason": None},
            "report": report_graph.get_report(session_id, company, fiscal_year)}


@router.get("/{session_id}/report/pdf", summary="Download the report as a PDF")
def download_report_pdf(session_id: uuid.UUID, company: str = Query(..., min_length=1),
                        fiscal_year: int = Query(...), db: DBSession = Depends(_db)) -> Response:
    _require_session(db, session_id)
    rep = report_graph.get_report(session_id, company, fiscal_year)
    if rep is None or rep["status"] not in ("ready", "partial"):
        raise HTTPException(status.HTTP_409_CONFLICT, "The report is not ready yet.")
    name = re.sub(r"[^A-Za-z0-9]+", "_", rep["company"]).strip("_") or "report"
    return Response(build_pdf(rep), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{name}_FY{fiscal_year}_report.pdf"'})


# ── chat ──────────────────────────────────────────────────────────────────────

@router.get("/{session_id}/chat", summary="Chat history for one company and year")
def chat_history(session_id: uuid.UUID, company: str = Query(..., min_length=1), fiscal_year: int = Query(...),
                 db: DBSession = Depends(_db)) -> List[dict]:
    _require_session(db, session_id)
    return chat.history(session_id, company, fiscal_year)


@router.post("/{session_id}/chat", summary="Ask a question about the selected company's document")
def chat_ask(session_id: uuid.UUID, body: ChatIn, db: DBSession = Depends(_db)) -> dict:
    _require_session(db, session_id)
    try:
        return chat.ask(session_id, body.company, body.fiscal_year, body.question)
    except (NotFound, NotReady) as exc:
        raise _http(exc)
    except QuotaExhaustedError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "The AI model's daily usage limit has been reached. Please try again later.")
    except Exception as exc:  # model/network failure: nothing was saved
        logger.exception("chat failed: %s", exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "The AI model couldn't answer right now. Please try again in a moment.")


@router.delete("/{session_id}/chat", status_code=status.HTTP_204_NO_CONTENT, summary="Clear the chat")
def chat_clear(session_id: uuid.UUID, company: str = Query(..., min_length=1), fiscal_year: int = Query(...),
               db: DBSession = Depends(_db)) -> Response:
    _require_session(db, session_id)
    chat.clear_history(session_id, company, fiscal_year)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
