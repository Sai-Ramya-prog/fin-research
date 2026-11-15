"""
Database reads the report and chat share: which companies exist in a session,
whether their documents are ready, and the saved metrics / red flags.

Everything returns plain dicts so the section builders stay easy to test.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from app.company import normalize_company
from app.db.engine import session_scope
from app.db.models import Document, Metric, RedFlag


def readiness(doc: Document) -> dict[str, Any]:
    """Can a report / chat run on this document? ``reason`` explains a "no"."""
    if doc.status == "failed":
        return {"ready": False, "reason": "This document failed to process. Re-upload it."}
    if doc.status != "ready":
        return {"ready": False, "reason": f"Document is still processing ({doc.status})."}
    if doc.extraction_status in (None, "queued", "running"):
        return {"ready": False, "reason": "The numbers are still being extracted. Try again in a moment."}
    if doc.extraction_status != "done":
        return {"ready": False, "reason": "Number extraction did not finish for this document."}
    if doc.red_flag_status in ("queued", "running"):
        return {"ready": False, "reason": "Red-flag analysis is still running. Try again in a moment."}
    return {"ready": True, "reason": None}


def find_document(db, session_id: uuid.UUID, company: str, fiscal_year: int) -> Optional[Document]:
    """The newest document for (company, year) in the session."""
    name = normalize_company(company)
    if not name:
        return None
    return (
        db.query(Document)
        .filter(Document.session_id == session_id, Document.company == name,
                Document.fiscal_year == fiscal_year)
        .order_by(Document.created_at.desc())
        .first()
    )


def list_companies(session_id: uuid.UUID) -> list[dict[str, Any]]:
    """Companies in a session with their fiscal years (newest first) and readiness.

    Only documents that have both a company and a fiscal year are listed; the
    dropdown never lets the user type a name that is not in the session.
    """
    with session_scope() as db:
        docs = (
            db.query(Document)
            .filter(Document.session_id == session_id, Document.company.isnot(None),
                    Document.fiscal_year.isnot(None))
            .order_by(Document.created_at.desc())
            .all()
        )
        by_company: dict[str, dict[int, dict[str, Any]]] = {}
        for d in docs:
            years = by_company.setdefault(d.company, {})
            if d.fiscal_year in years:           # newest upload per (company, year) wins
                continue
            r = readiness(d)
            years[d.fiscal_year] = {
                "fiscal_year": d.fiscal_year, "document_id": str(d.id), "filename": d.filename,
                "ready": r["ready"], "reason": r["reason"],
            }
        return [
            {"company": c, "years": [years[y] for y in sorted(years, reverse=True)]}
            for c, years in sorted(by_company.items())
        ]


def company_names(db, session_id: uuid.UUID) -> list[str]:
    """Distinct company names in the session."""
    rows = (db.query(Document.company)
            .filter(Document.session_id == session_id, Document.company.isnot(None))
            .distinct().all())
    return sorted(r[0] for r in rows)


def load_metrics(db, document_id: uuid.UUID, fiscal_year: Optional[int]) -> list[dict[str, Any]]:
    """Saved metrics as dicts with ``period`` = current | prior."""
    rows = db.query(Metric).filter(Metric.document_id == document_id).all()
    years = [r.year for r in rows if r.year is not None]
    current = fiscal_year if fiscal_year is not None else (max(years) if years else None)
    return [
        {
            "name": r.name,
            "period": "current" if (r.year is None or r.year == current) else "prior",
            "year": r.year,
            "value": None if r.value is None else float(r.value),
            "unit": r.unit, "page": r.source_page, "status": r.status, "kind": r.kind,
            "formula": r.formula, "source_text": r.source_text, "note": r.note,
        }
        for r in rows
    ]


def load_flags(db, document_id: uuid.UUID) -> list[dict[str, Any]]:
    order = {"High": 0, "Medium": 1, "Low": 2}
    rows = db.query(RedFlag).filter(RedFlag.document_id == document_id).all()
    out = [
        {"type": r.type, "severity": r.severity, "title": r.title or r.type,
         "description": r.description, "evidence": r.evidence, "page": r.source_page}
        for r in rows
    ]
    return sorted(out, key=lambda f: order.get(f["severity"], 3))


def load_peers(db, session_id: uuid.UUID, company: str, fiscal_year: int) -> list[dict[str, Any]]:
    """Other analysed companies in the same session: ``{company, fiscal_year, metrics}``.

    The peer's document for the same fiscal year is used when there is one,
    otherwise its newest analysed year.
    """
    docs = (
        db.query(Document)
        .filter(Document.session_id == session_id, Document.company.isnot(None),
                Document.company != company, Document.fiscal_year.isnot(None),
                Document.status == "ready", Document.extraction_status == "done")
        .order_by(Document.created_at.desc())
        .all()
    )
    best: dict[str, Document] = {}
    for d in docs:
        cur = best.get(d.company)
        if cur is None:
            best[d.company] = d
        elif cur.fiscal_year != fiscal_year and (
            d.fiscal_year == fiscal_year or d.fiscal_year > cur.fiscal_year
        ):
            best[d.company] = d
    return [
        {"company": c, "fiscal_year": d.fiscal_year,
         "metrics": load_metrics(db, d.id, d.fiscal_year)}
        for c, d in sorted(best.items())
    ]
