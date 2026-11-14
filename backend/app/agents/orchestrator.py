"""
app/agents/orchestrator.py
───────────────────────────────────────────────────────────────────────────────
The graph that runs the agents in order for one uploaded document.

  [document] → [extraction] → [red_flag] → END
       │             │
       └─ failed ────┴─ failed → END          (later agents are skipped)

Each node runs one agent (itself a LangGraph graph) and reports whether it
succeeded. The orchestrator holds no business logic; it only decides order,
and what to skip when a step fails:

  * document agent failed   -> extraction and red flags are marked ``skipped``
  * extraction failed       -> red flags are marked ``skipped``; the document
                               stays READY and searchable
  * ``RUN_AGENTS=false``    -> only the document agent runs

Public API:  run_pipeline(doc_id)  (what the upload endpoint and seed call)
"""

from __future__ import annotations

import logging
import uuid
from typing import Literal, Optional
from typing_extensions import TypedDict

from langgraph.graph import END, StateGraph

from app.agents.document_agent.graph import run_document_agent
from app.agents.extraction_agent.graph import run_extraction_agent
from app.agents.red_flag_agent.graph import run_red_flag_agent
from app.config import settings
from app.db.engine import session_scope
from app.db.models import Document

logger = logging.getLogger(__name__)


class PipelineState(TypedDict, total=False):
    doc_id: uuid.UUID
    document_ok: bool
    extraction_ok: bool
    red_flag_ok: bool


def _set(doc_id: uuid.UUID, **fields) -> None:
    with session_scope() as db:
        doc = db.get(Document, doc_id)
        if doc is not None:
            for k, v in fields.items():
                setattr(doc, k, v)


def _document_ready(doc_id: uuid.UUID) -> bool:
    with session_scope() as db:
        doc = db.get(Document, doc_id)
        return bool(doc and doc.status == "ready")


def _node_document(state: PipelineState) -> PipelineState:
    doc_id = state["doc_id"]
    if settings.run_agents:
        _set(doc_id, extraction_status="queued", red_flag_status="queued", analysis_error=None)
    run_document_agent(doc_id)
    return {**state, "document_ok": _document_ready(doc_id)}


def _node_extraction(state: PipelineState) -> PipelineState:
    return {**state, "extraction_ok": run_extraction_agent(state["doc_id"])}


def _node_red_flag(state: PipelineState) -> PipelineState:
    return {**state, "red_flag_ok": run_red_flag_agent(state["doc_id"])}


def _node_skip(state: PipelineState) -> PipelineState:
    """Mark whatever did not run as skipped."""
    if not settings.run_agents:
        return state
    fields = {}
    if not state.get("document_ok"):
        fields = {"extraction_status": "skipped", "red_flag_status": "skipped"}
    elif not state.get("extraction_ok"):
        fields = {"red_flag_status": "skipped"}
    if fields:
        _set(state["doc_id"], **fields)
        logger.warning("pipeline: doc=%s skipped later agents %s", state["doc_id"], fields)
    return state


def _after_document(state: PipelineState) -> Literal["extraction", "skip"]:
    return "extraction" if state.get("document_ok") and settings.run_agents else "skip"


def _after_extraction(state: PipelineState) -> Literal["red_flag", "skip"]:
    return "red_flag" if state.get("extraction_ok") else "skip"


def _build_graph() -> StateGraph:
    g = StateGraph(PipelineState)
    g.add_node("document", _node_document)
    g.add_node("extraction", _node_extraction)
    g.add_node("red_flag", _node_red_flag)
    g.add_node("skip", _node_skip)
    g.set_entry_point("document")
    g.add_conditional_edges("document", _after_document, {"extraction": "extraction", "skip": "skip"})
    g.add_conditional_edges("extraction", _after_extraction, {"red_flag": "red_flag", "skip": "skip"})
    g.add_edge("red_flag", END)
    g.add_edge("skip", END)
    return g


_graph = _build_graph().compile()


def run_pipeline(doc_id: uuid.UUID) -> None:
    """Document Agent -> Extraction Agent -> Red Flag Agent for one document."""
    logger.info("pipeline: doc=%s start", doc_id)
    _graph.invoke({"doc_id": doc_id})
    logger.info("pipeline: doc=%s finished", doc_id)


def run_analysis(doc_id: uuid.UUID) -> None:
    """Re-run only Extraction + Red Flags (the document is already indexed)."""
    _set(doc_id, extraction_status="queued", red_flag_status="queued", analysis_error=None)
    if run_extraction_agent(doc_id):
        run_red_flag_agent(doc_id)
    else:
        _set(doc_id, red_flag_status="skipped")
