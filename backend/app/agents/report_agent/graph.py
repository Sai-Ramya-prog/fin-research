"""
app/agents/report_agent/graph.py
───────────────────────────────────────────────────────────────────────────────
LangGraph wiring for the Report Agent.

  [load] → [snapshot] → [outlook] → [finish]
                (any hard error → [fail])

Each section node saves its section to ``reports.sections`` the moment it is
done, so the screen can show a real "N of 5 sections ready" while it runs.
A model failure in one written section does not stop the others: that section
is marked unavailable and the report finishes as ``partial``.

Public API
    start_report(session_id, company, fiscal_year, regenerate=False) -> (report_id, should_run)
    run_report(report_id)            run it (call from a background task)
    get_report(session_id, company, fiscal_year) -> dict | None
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, List, Literal, Optional
from typing_extensions import TypedDict

from langgraph.graph import END, StateGraph

from app import llm
from app.agents.report_agent import data
from app.agents.report_agent.sections import (
    SECTION_ORDER,
    build_flags_section,
    build_grounded_section,
    build_metrics_section,
    build_peers_section,
    unavailable,
)
from app.company import normalize_company
from app.config import settings
from app.db.engine import session_scope
from app.db.models import Document, Report
from app.pages import load_page_texts, load_pages
from app import retrieval

logger = logging.getLogger(__name__)

STALE_AFTER = timedelta(minutes=10)     # a "running" report older than this was interrupted
_RETRIEVAL_QUERIES = {
    "snapshot": "company overview business segments products services operations geography",
    "outlook": "outlook strategy risks opportunities future growth priorities",
}
_MDNA_PAGES = {"snapshot": 3, "outlook": 5}
_EXTRA_HITS = {"snapshot": 3, "outlook": 3}


class NotFound(Exception):
    """No document for that company / fiscal year in the session."""


class NotReady(Exception):
    """The document exists but is not processed / analysed yet."""


# ── start / read ──────────────────────────────────────────────────────────────

def _aware(dt: Optional[datetime]) -> datetime:
    if dt is None:
        return datetime.now(timezone.utc)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def start_report(session_id: uuid.UUID, company: str, fiscal_year: int,
                 regenerate: bool = False) -> tuple[uuid.UUID, bool]:
    """Create (or reset) the report row. Returns ``(report_id, should_run)``."""
    name = normalize_company(company)
    with session_scope() as db:
        doc = data.find_document(db, session_id, name or "", fiscal_year)
        if doc is None:
            raise NotFound(f"No document for {name} FY{fiscal_year} in this session.")
        ready = data.readiness(doc)
        if not ready["ready"]:
            raise NotReady(ready["reason"])
        row = (db.query(Report)
               .filter(Report.session_id == session_id, Report.company == name,
                       Report.fiscal_year == fiscal_year).first())
        if row is not None:
            fresh_running = row.status == "running" and _aware(row.updated_at) > datetime.now(timezone.utc) - STALE_AFTER
            if fresh_running:
                return row.id, False
            if row.status in ("ready", "partial") and not regenerate:
                return row.id, False
        else:
            row = Report(session_id=session_id, company=name, fiscal_year=fiscal_year)
            db.add(row)
        row.status, row.sections, row.error = "running", [], None
        row.model = settings.llm_model
        db.flush()
        return row.id, True


def _friendly_error(status: str, raw: Optional[str]) -> Optional[str]:
    """What the screen shows. The raw model/network error stays in the database and the logs."""
    if status not in ("partial", "failed"):
        return None
    if raw and "quota" in raw.lower():
        return "The AI model's usage limit has been reached, so the report could not be finished. Try Regenerate later."
    if status == "partial":
        return "Some sections could not be written because the AI model did not respond properly. Click Regenerate to try again."
    if raw and "interrupted" in raw.lower():
        return "Report generation was interrupted (the server may have restarted). Click Regenerate."
    return "The report could not be generated. Click Regenerate to try again."


def _to_dict(row: Report) -> dict[str, Any]:
    keep = {k for k, _ in SECTION_ORDER}          # reports saved earlier may hold removed sections
    sections = [x for x in (row.sections or []) if x["key"] in keep]
    return {
        "id": str(row.id), "company": row.company, "fiscal_year": row.fiscal_year,
        "status": row.status, "sections": sections, "error": _friendly_error(row.status, row.error),
        "model": row.model,
        "progress": {"done": len(sections), "total": len(SECTION_ORDER)},
        "updated_at": _aware(row.updated_at).isoformat(),
    }


def get_report(session_id: uuid.UUID, company: str, fiscal_year: int) -> Optional[dict[str, Any]]:
    name = normalize_company(company)
    with session_scope() as db:
        row = (db.query(Report)
               .filter(Report.session_id == session_id, Report.company == name,
                       Report.fiscal_year == fiscal_year).first())
        if row is None:
            return None
        if row.status == "running" and _aware(row.updated_at) < datetime.now(timezone.utc) - STALE_AFTER:
            row.status = "failed"
            row.error = "Report generation was interrupted (the server may have restarted). Click Regenerate."
        return _to_dict(row)


PDF_ORDER = ["snapshot", "metrics", "peers", "red_flags", "outlook"]


def get_report_for_pdf(session_id: uuid.UUID, company: str, fiscal_year: int) -> Optional[dict[str, Any]]:
    """The saved report plus the number-based sections (metrics, peers, red flags),
    built fresh from the database. The screen does not show those; the PDF does."""
    rep = get_report(session_id, company, fiscal_year)
    if rep is None:
        return None
    with session_scope() as db:
        doc = data.find_document(db, session_id, rep["company"], fiscal_year)
        if doc is None:
            return rep
        metrics = data.load_metrics(db, doc.id, doc.fiscal_year)
        flags = data.load_flags(db, doc.id)
        peers = data.load_peers(db, session_id, rep["company"], fiscal_year)
        flag_status = doc.red_flag_status
    extra = {"metrics": build_metrics_section(metrics),
             "red_flags": build_flags_section(flags, flag_status)}
    if peers:                                    # nothing to compare with: leave the section out
        extra["peers"] = build_peers_section(rep["company"], fiscal_year, metrics, peers)
    by_key = {**{s["key"]: s for s in rep["sections"]}, **extra}
    return {**rep, "sections": [by_key[k] for k in PDF_ORDER if k in by_key]}


# ── graph ─────────────────────────────────────────────────────────────────────

class ReportState(TypedDict, total=False):
    report_id: uuid.UUID
    session_id: uuid.UUID
    company: str
    fiscal_year: int
    doc_id: uuid.UUID
    metrics: List[dict]
    errors: List[str]
    quota_hit: bool
    error: Optional[str]


def _save_section(report_id: uuid.UUID, section: dict) -> None:
    with session_scope() as db:
        row = db.get(Report, report_id)
        if row is None:
            return
        kept = [s for s in (row.sections or []) if s["key"] != section["key"]]
        row.sections = kept + [section]          # new list so the JSON change is detected
        row.updated_at = datetime.now(timezone.utc)


def _node_load(state: ReportState) -> ReportState:
    try:
        with session_scope() as db:
            doc = db.get(Document, state["doc_id"])
            if doc is None:
                return {**state, "error": "document not found"}
            metrics = data.load_metrics(db, doc.id, doc.fiscal_year)   # verified figures for the {placeholders}
            return {**state, "metrics": metrics, "errors": [], "quota_hit": False}
    except Exception as exc:
        logger.exception("[report:load] FAILED: %s", exc)
        return {**state, "error": f"load failed: {exc}"}


def _context_pages(state: ReportState, key: str) -> dict[int, str]:
    """MD&A pages plus the best semantic hits for this section's topic."""
    doc_id = state["doc_id"]
    numbers: list[int] = [p["pdf_page"] for p in
                          load_pages(doc_id, ["mdna_narrative"], max_per_section=_MDNA_PAGES[key],
                                     prefer_consolidated=False)]
    try:
        hits = retrieval.search(_RETRIEVAL_QUERIES[key], document_id=str(doc_id), k=_EXTRA_HITS[key] * 2)
        for h in hits:
            pg = h.metadata.get("pdf_page")
            if pg is not None and str(pg).isdigit() and int(pg) not in numbers:
                numbers.append(int(pg))
            if len(numbers) >= _MDNA_PAGES[key] + _EXTRA_HITS[key]:
                break
    except Exception as exc:                      # the vector store being down must not kill the report
        logger.warning("[report:%s] retrieval skipped: %s", key, exc)
    return load_page_texts(doc_id, numbers)


def _llm_section(key: str):
    def node(state: ReportState) -> ReportState:
        if state.get("error"):
            return state
        errors = list(state.get("errors", []))
        if state.get("quota_hit"):
            section = unavailable(key, "Skipped: the model's daily quota is used up. Regenerate later.")
            errors.append(f"{key}: quota exhausted")
        else:
            try:
                pages = _context_pages(state, key)
                section = build_grounded_section(key, state["company"], state["fiscal_year"], pages,
                                                 state["metrics"])
            except llm.QuotaExhaustedError as exc:
                logger.warning("[report:%s] quota exhausted: %s", key, exc)
                section = unavailable(key, "The model's daily quota is used up. Regenerate later.")
                errors.append(f"{key}: quota exhausted")
                state = {**state, "quota_hit": True}
            except Exception as exc:
                logger.exception("[report:%s] FAILED: %s", key, exc)
                section = unavailable(key, "This section could not be written. Click Regenerate to retry.")
                errors.append(f"{key}: {exc}")
        _save_section(state["report_id"], section)
        return {**state, "errors": errors}
    node.__name__ = f"node_{key}"
    return node


def _node_finish(state: ReportState) -> ReportState:
    errors = state.get("errors", [])
    with session_scope() as db:
        row = db.get(Report, state["report_id"])
        if row is not None:
            order = {k: i for i, (k, _) in enumerate(SECTION_ORDER)}
            row.sections = sorted(row.sections or [], key=lambda s: order.get(s["key"], 99))
            row.status = "partial" if errors else "ready"
            row.error = "; ".join(errors)[:2000] or None
            row.updated_at = datetime.now(timezone.utc)
    return state


def _node_fail(state: ReportState) -> ReportState:
    error = state.get("error", "unknown error")
    logger.error("[report:fail] report=%s %s", state.get("report_id"), error)
    try:
        with session_scope() as db:
            row = db.get(Report, state["report_id"])
            if row is not None:
                row.status, row.error = "failed", error[:2000]
                row.updated_at = datetime.now(timezone.utc)
    except Exception as exc:  # pragma: no cover
        logger.error("could not persist report error: %s", exc)
    return state


def _route(state: ReportState) -> Literal["continue", "fail"]:
    return "fail" if state.get("error") else "continue"


def _build_graph() -> StateGraph:
    g = StateGraph(ReportState)
    g.add_node("load", _node_load)
    g.add_node("snapshot", _llm_section("snapshot"))
    g.add_node("outlook", _llm_section("outlook"))
    g.add_node("finish", _node_finish)
    g.add_node("fail", _node_fail)
    g.set_entry_point("load")
    g.add_conditional_edges("load", _route, {"continue": "snapshot", "fail": "fail"})
    for a, b in (("snapshot", "outlook"), ("outlook", "finish")):
        g.add_edge(a, b)
    g.add_edge("finish", END)
    g.add_edge("fail", END)
    return g


_graph = _build_graph().compile()


def run_report(report_id: uuid.UUID) -> bool:
    """Generate the report. Returns True unless it failed outright."""
    with session_scope() as db:
        row = db.get(Report, report_id)
        if row is None:
            raise RuntimeError(f"Report {report_id} not found")
        doc = data.find_document(db, row.session_id, row.company, row.fiscal_year)
        if doc is None:
            row.status, row.error = "failed", "document no longer exists"
            return False
        state: ReportState = {"report_id": row.id, "session_id": row.session_id,
                              "company": row.company, "fiscal_year": row.fiscal_year, "doc_id": doc.id}
    try:
        final = _graph.invoke(state)
    except Exception as exc:                       # never leave a report stuck on "running"
        logger.exception("[report] crashed: %s", exc)
        _node_fail({**state, "error": f"report crashed: {exc}"})
        return False
    return not final.get("error")
