"""
app/agents/red_flag_agent/graph.py
───────────────────────────────────────────────────────────────────────────────
LangGraph wiring for the Red Flag Agent.

  [load] → [rules] → [auditor] → [save] → END        (any error → [fail] → END)

load     reads the saved metrics + the auditor's-report pages
rules    numeric checks (falling margins, rising debt, weak cash ...)
auditor  heading scan of the auditor's report (qualified opinion, going concern ...)
save     replaces the document's rows in ``red_flags``

No LLM is used: every flag is traceable to a number or a quoted sentence.

Public API:  run_red_flag_agent(doc_id) -> bool
"""

from __future__ import annotations

import logging
import uuid
from typing import List, Literal, Optional
from typing_extensions import TypedDict

from langgraph.graph import END, StateGraph

from app.agents.red_flag_agent.auditor import scan_auditor_pages
from app.agents.red_flag_agent.rules import run_rules
from app.db.engine import session_scope
from app.db.models import Document, Metric, RedFlag
from app.pages import load_pages

logger = logging.getLogger(__name__)

_SEVERITY_ORDER = {"High": 0, "Medium": 1, "Low": 2}


class RedFlagState(TypedDict, total=False):
    doc_id: uuid.UUID
    metrics: List[dict]
    auditor_pages: List[dict]
    flags: List[dict]
    error: Optional[str]


def _node_load(state: RedFlagState) -> RedFlagState:
    doc_id = state["doc_id"]
    try:
        with session_scope() as db:
            doc = db.get(Document, doc_id)
            fy = doc.fiscal_year if doc else None
            rows = db.query(Metric).filter(Metric.document_id == doc_id).all()
            years = [r.year for r in rows if r.year is not None]
            current = fy if fy is not None else (max(years) if years else None)
            metrics = [
                {
                    "name": r.name,
                    "period": "current" if (r.year is None or r.year == current) else "prior",
                    "value": None if r.value is None else float(r.value),
                    "unit": r.unit, "page": r.source_page, "status": r.status,
                }
                for r in rows
            ]
        if not metrics:
            return {**state, "error": "no metrics available (extraction did not produce any)"}
        auditor = load_pages(doc_id, ["auditor_report"], max_per_section=60, prefer_consolidated=False)
        return {**state, "metrics": metrics, "auditor_pages": auditor}
    except Exception as exc:
        logger.exception("[redflag:load] FAILED: %s", exc)
        return {**state, "error": f"load failed: {exc}"}


def _node_rules(state: RedFlagState) -> RedFlagState:
    return {**state, "flags": run_rules(state["metrics"])}


def _node_auditor(state: RedFlagState) -> RedFlagState:
    flags = state["flags"] + scan_auditor_pages(state.get("auditor_pages", []))
    flags.sort(key=lambda f: _SEVERITY_ORDER.get(f["severity"], 3))
    return {**state, "flags": flags}


def _node_save(state: RedFlagState) -> RedFlagState:
    doc_id = state["doc_id"]
    try:
        with session_scope() as db:
            db.query(RedFlag).filter(RedFlag.document_id == doc_id).delete()
            for f in state["flags"]:
                db.add(RedFlag(document_id=doc_id, type=f["type"], severity=f["severity"],
                               title=f["title"], description=f["description"],
                               evidence=f["evidence"], source_page=f["source_page"]))
            doc = db.get(Document, doc_id)
            if doc is not None:
                doc.red_flag_status = "done"
        logger.info("[redflag:save] doc=%s flags=%d", doc_id, len(state["flags"]))
        return state
    except Exception as exc:
        logger.exception("[redflag:save] FAILED: %s", exc)
        return {**state, "error": f"saving red flags failed: {exc}"}


def _node_fail(state: RedFlagState) -> RedFlagState:
    error = state.get("error", "unknown error")
    logger.error("[redflag:fail] doc=%s %s", state["doc_id"], error)
    try:
        with session_scope() as db:
            doc = db.get(Document, state["doc_id"])
            if doc is not None:
                doc.red_flag_status = "failed"
                doc.analysis_error = ((doc.analysis_error or "") + " " + error)[:2000].strip()
    except Exception as exc:  # pragma: no cover
        logger.error("could not persist red-flag error: %s", exc)
    return state


def _route(state: RedFlagState) -> Literal["continue", "fail"]:
    return "fail" if state.get("error") else "continue"


def _build_graph() -> StateGraph:
    g = StateGraph(RedFlagState)
    for name, fn in (("load", _node_load), ("rules", _node_rules), ("auditor", _node_auditor),
                     ("save", _node_save), ("fail", _node_fail)):
        g.add_node(name, fn)
    g.set_entry_point("load")
    g.add_conditional_edges("load", _route, {"continue": "rules", "fail": "fail"})
    g.add_edge("rules", "auditor")
    g.add_edge("auditor", "save")
    g.add_conditional_edges("save", _route, {"continue": END, "fail": "fail"})
    g.add_edge("fail", END)
    return g


_graph = _build_graph().compile()


def run_red_flag_agent(doc_id: uuid.UUID) -> bool:
    with session_scope() as db:
        doc = db.get(Document, doc_id)
        if doc is None:
            raise RuntimeError(f"Document {doc_id} not found")
        doc.red_flag_status = "running"
    final = _graph.invoke({"doc_id": doc_id, "error": None})
    return not final.get("error")
