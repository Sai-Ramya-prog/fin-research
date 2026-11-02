"""
app/agents/extraction_agent/graph.py
───────────────────────────────────────────────────────────────────────────────
LangGraph wiring for the Extraction Agent.

  [load] → [extract] → [verify] → [ratios] → [save] → END
     └──────────┴─────────┴──────────┴─────────┘
                 (any error → [fail] → END)

load     reads the stored statement pages of the document (page store, no Chroma)
extract  one Gemini call per statement (P&L, balance sheet, cash flow); the model
         only copies numbers + the line it saw them on
verify   source check (number really on the cited page) + consistency checks
ratios   margins, debt/equity, growth ... computed in code
save     replaces the document's rows in ``metrics``

Public API:  run_extraction_agent(doc_id) -> bool   (True = metrics saved)
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict, List, Literal, Optional
from typing_extensions import TypedDict

from langgraph.graph import END, StateGraph

from app import llm
from app.agents.extraction_agent.metrics import (
    PER_SHARE,
    STATEMENTS,
    SYSTEM_PROMPT,
    StatementAnswer,
    build_user_prompt,
)
from app.agents.extraction_agent.ratios import compute_ratios
from app.agents.extraction_agent.verify import consistency_check, source_check
from app.db.engine import session_scope
from app.db.models import Document, Metric
from app.pages import load_pages

logger = logging.getLogger(__name__)


class ExtractionState(TypedDict, total=False):
    doc_id: uuid.UUID
    fiscal_year: Optional[int]
    pages: Dict[str, List[dict]]      # section -> stored pages
    rows: List[dict]                  # metric rows (see _new_row)
    missing: List[str]                # statements with no pages found
    error: Optional[str]


def _new_row(name, period, value, unit, page, source_text, *, kind="extracted", formula=None, note=None) -> dict:
    return {
        "name": name, "period": period, "value": value, "unit": unit, "page": page,
        "source_text": source_text, "kind": kind, "formula": formula,
        "status": "verified", "note": note,
    }


def _unit_for(metric: str, page_unit: str) -> str:
    if metric in PER_SHARE:
        symbol = page_unit.split(" ")[0] if page_unit else ""
        return f"{symbol} per share".strip()
    return page_unit or ""


# ── nodes ─────────────────────────────────────────────────────────────────────

def _node_load(state: ExtractionState) -> ExtractionState:
    doc_id = state["doc_id"]
    pages: Dict[str, List[dict]] = {}
    missing: List[str] = []
    for section in STATEMENTS:
        found = load_pages(doc_id, [section])
        if found:
            pages[section] = found
        else:
            missing.append(section)
    if not pages:
        return {**state, "error": "no statement pages found (profit & loss, balance sheet, cash flow)"}
    logger.info("[extract:load] doc=%s sections=%s missing=%s", doc_id, list(pages), missing)
    return {**state, "pages": pages, "missing": missing}


def _node_extract(state: ExtractionState) -> ExtractionState:
    rows: List[dict] = []
    try:
        for section, pages in state["pages"].items():
            t0 = time.perf_counter()
            answer = llm.call_structured(StatementAnswer, SYSTEM_PROMPT, build_user_prompt(section, pages))
            unit = next((p["unit"] for p in pages if p.get("unit")), "") or ""
            allowed = set(STATEMENTS[section])
            for item in answer.items:
                if item.metric not in allowed:
                    continue
                for period, value in (("current", item.current), ("prior", item.prior)):
                    if value is None:
                        continue
                    rows.append(_new_row(item.metric, period, value, _unit_for(item.metric, unit),
                                         item.page, item.source_line))
            if section == "balance_sheet":
                rows.extend(_debt_rows(answer, unit))
            logger.info("[extract] doc=%s section=%s rows=%d %.1fs", state["doc_id"], section,
                        len(rows), time.perf_counter() - t0)
        return {**state, "rows": rows}
    except Exception as exc:  # includes QuotaExhaustedError
        logger.exception("[extract] FAILED: %s", exc)
        return {**state, "error": f"extraction failed: {exc}"}


def _debt_rows(answer: StatementAnswer, unit: str) -> List[dict]:
    """total_debt = sum of the printed borrowing lines (the sum is done here)."""
    out: List[dict] = []
    for period in ("current", "prior"):
        lines = [(l, getattr(l, period)) for l in answer.debt_lines if getattr(l, period) is not None]
        if not lines:
            # Either debt-free or the model missed it: keep 0 but ask for review.
            row = _new_row("total_debt", period, 0.0, unit, None, None, kind="derived",
                           formula="no borrowing line found", note="No borrowing line found; treated as 0.")
            row["status"] = "needs_review"
            out.append(row)
            continue
        total = sum(v for _, v in lines)
        labels = " + ".join(f"{l.label} ({v:,.0f})" for l, v in lines)
        row = _new_row("total_debt", period, total, unit, lines[0][0].page, labels,
                       kind="derived", formula="sum of borrowing lines")
        row["_parts"] = [
            {"value": v, "page": l.page} for l, v in lines
        ]
        out.append(row)
    return out


def _node_verify(state: ExtractionState) -> ExtractionState:
    rows = state["rows"]
    pages_by_no = {p["pdf_page"]: p["text"] for ps in state["pages"].values() for p in ps}
    # Source check for single lines; debt is checked line by line.
    singles = [r for r in rows if r["name"] != "total_debt"]
    source_check(singles, pages_by_no)
    for r in rows:
        if r["name"] != "total_debt":
            continue
        parts = r.pop("_parts", None)
        if not parts:
            continue
        probe = [
            {"name": "debt_part", "period": r["period"], "value": p["value"], "page": p["page"],
             "kind": "extracted", "status": "verified", "note": None}
            for p in parts
        ]
        source_check(probe, pages_by_no)
        bad = [p for p in probe if p["status"] != "verified"]
        if bad:
            r["status"] = "needs_review"
            r["note"] = "A borrowing line was not found on the statement page."
    consistency_check(rows)
    return {**state, "rows": rows}


def _node_ratios(state: ExtractionState) -> ExtractionState:
    rows = state["rows"]
    return {**state, "rows": rows + compute_ratios(rows)}


def _node_save(state: ExtractionState) -> ExtractionState:
    doc_id = state["doc_id"]
    fy = state.get("fiscal_year")
    try:
        with session_scope() as db:
            db.query(Metric).filter(Metric.document_id == doc_id).delete()
            for r in state["rows"]:
                year = None
                if fy:
                    year = fy if r["period"] == "current" else fy - 1
                db.add(Metric(
                    document_id=doc_id, name=r["name"], value=r["value"], unit=r["unit"],
                    year=year, source_page=r["page"], kind=r["kind"], source_text=r["source_text"],
                    formula=r["formula"], status=r["status"], note=(r["note"] or "").strip() or None,
                ))
            doc = db.get(Document, doc_id)
            if doc is not None:
                doc.extraction_status = "done"
        extracted = [r for r in state["rows"] if r["kind"] == "extracted"]
        review = [r for r in state["rows"] if r["status"] != "verified"]
        logger.info("[extract:save] doc=%s saved=%d extracted=%d needs_review=%d",
                    doc_id, len(state["rows"]), len(extracted), len(review))
        return state
    except Exception as exc:
        logger.exception("[extract:save] FAILED: %s", exc)
        return {**state, "error": f"saving metrics failed: {exc}"}


def _node_fail(state: ExtractionState) -> ExtractionState:
    error = state.get("error", "unknown error")
    logger.error("[extract:fail] doc=%s %s", state["doc_id"], error)
    try:
        with session_scope() as db:
            doc = db.get(Document, state["doc_id"])
            if doc is not None:
                doc.extraction_status = "failed"
                doc.analysis_error = error[:2000]
    except Exception as exc:  # pragma: no cover
        logger.error("could not persist extraction error: %s", exc)
    return state


def _route(state: ExtractionState) -> Literal["continue", "fail"]:
    return "fail" if state.get("error") else "continue"


def _build_graph() -> StateGraph:
    g = StateGraph(ExtractionState)
    for name, fn in (("load", _node_load), ("extract", _node_extract), ("verify", _node_verify),
                     ("ratios", _node_ratios), ("save", _node_save), ("fail", _node_fail)):
        g.add_node(name, fn)
    g.set_entry_point("load")
    g.add_conditional_edges("load", _route, {"continue": "extract", "fail": "fail"})
    g.add_conditional_edges("extract", _route, {"continue": "verify", "fail": "fail"})
    g.add_edge("verify", "ratios")
    g.add_edge("ratios", "save")
    g.add_conditional_edges("save", _route, {"continue": END, "fail": "fail"})
    g.add_edge("fail", END)
    return g


_graph = _build_graph().compile()


def run_extraction_agent(doc_id: uuid.UUID) -> bool:
    """Extract metrics for one document. Returns True when metrics were saved."""
    with session_scope() as db:
        doc = db.get(Document, doc_id)
        if doc is None:
            raise RuntimeError(f"Document {doc_id} not found")
        doc.extraction_status = "running"
        doc.analysis_error = None
        fy = doc.fiscal_year
    final = _graph.invoke({"doc_id": doc_id, "fiscal_year": fy, "rows": [], "error": None})
    return not final.get("error")
