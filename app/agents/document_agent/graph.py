"""
app/agents/document_agent/graph.py
───────────────────────────────────────────────────────────────────────────────
LangGraph wiring for the Document Agent (Task 8).

Pipeline
--------
  run_document_agent(doc_id)
        │
        ▼
  [parse]  →  [tag]  →  [chunk]  →  [index]  →  END (status: ready)
     │           │          │           │
     └───────────┴──────────┴───────────┘
                 (any error → [fail] → END)

Each node:
  1. Logs entry, timing and counts.
  2. On any exception sets ``state["error"]`` and returns immediately.

The conditional edge after every node checks ``state.get("error")`` – if set
it routes to the ``fail`` node which persists the error to the Document row and
ends the graph.

Public API
----------
  run_document_agent(doc_id: uuid.UUID) -> None
      Downloads the PDF temp file, builds initial state, invokes the graph and
      cleans up the temp file afterwards (even on failure).
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from pathlib import Path
from typing import Literal

from langgraph.graph import END, StateGraph

from app.agents.document_agent.state import DocumentState
from app.chunk import chunk_pages
from app.db.engine import session_scope
from app.db.models import Document
from app.config import settings
from app.index import index_chunks
from app.pages import mark_indexed, save_pages
from app.parse import parse_fast, parse_layout
from app.storage import download_to_temp
from app.tag import STATEMENT_SECTIONS, tag_pages

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# DB helpers
# ─────────────────────────────────────────────────────────────────────────────

def _set_status(doc_id: uuid.UUID, status: str, error_message: str | None = None) -> None:
    """Update Document.status (and optional error_message) inside a new session."""
    with session_scope() as db:
        doc = db.get(Document, doc_id)
        if doc is None:
            raise RuntimeError(f"Document {doc_id} not found")
        doc.status = status
        if error_message is not None:
            doc.error_message = error_message
        db.commit()
        logger.debug("Document %s → status=%s", doc_id, status)


def _get_document(doc_id: uuid.UUID) -> Document:
    """Return the Document ORM row (detached copy)."""
    with session_scope() as db:
        doc = db.get(Document, doc_id)
        if doc is None:
            raise RuntimeError(f"Document {doc_id} not found in database")
        # Detach – we just need the scalar fields
        db.expunge(doc)
        return doc


# ─────────────────────────────────────────────────────────────────────────────
# Graph nodes
# ─────────────────────────────────────────────────────────────────────────────

def _node_parse(state: DocumentState) -> DocumentState:
    """Node 1 – Parse the PDF into page dicts.

    Uses parse_fast for all pages, then re-parses statement pages with the
    layout-preserving pdfplumber pass so financial tables keep their columns.
    """
    doc_id = state["doc_id"]
    pdf_path = state["pdf_path"]
    t0 = time.perf_counter()

    logger.info("[parse] doc=%s  start", doc_id)
    _set_status(doc_id, "parsing")

    try:
        pages = parse_fast(pdf_path)
        elapsed = time.perf_counter() - t0
        logger.info(
            "[parse] doc=%s  pages=%d  elapsed=%.2fs",
            doc_id,
            len(pages),
            elapsed,
        )
        return {**state, "pages": pages}

    except Exception as exc:
        logger.exception("[parse] doc=%s  FAILED: %s", doc_id, exc)
        return {**state, "error": f"parse failed: {exc}"}


def _node_tag(state: DocumentState) -> DocumentState:
    """Node 2 – Tag each page (section, scope, unit, content_type, printed_page)."""
    if state.get("error"):
        return state

    doc_id = state["doc_id"]
    pages = state["pages"]
    pdf_name = state.get("filename") or Path(state["pdf_path"]).name
    t0 = time.perf_counter()

    logger.info("[tag] doc=%s  pages=%d  start", doc_id, len(pages))
    _set_status(doc_id, "tagging")

    try:
        tags = tag_pages(pages, pdf_name=pdf_name)
        elapsed = time.perf_counter() - t0
        statement_count = sum(
            1 for t in tags if t.content_type == "table"
        )
        logger.info(
            "[tag] doc=%s  tags=%d  statement_pages=%d  elapsed=%.2fs",
            doc_id,
            len(tags),
            statement_count,
            elapsed,
        )
        # Store every page's text + tags (no embedding, no quota). Tier-2 pages
        # stay here until a question needs them.
        save_pages(doc_id, pages, tags)
        tier1 = sum(1 for t in tags if getattr(t, "tier", 2) == 1)
        logger.info("[tag] doc=%s  stored %d pages, tier1=%d", doc_id, len(tags), tier1)
        return {**state, "tags": tags}

    except Exception as exc:
        logger.exception("[tag] doc=%s  FAILED: %s", doc_id, exc)
        return {**state, "error": f"tag failed: {exc}"}


def _node_chunk(state: DocumentState) -> DocumentState:
    """Node 3 – Chunk pages (table pages → 1 chunk each; narrative → splitter)."""
    if state.get("error"):
        return state

    doc_id = state["doc_id"]
    pages = state["pages"]
    tags = state["tags"]
    pdf_path = state["pdf_path"]
    # Use the real file name (not the temp download name) so chunk metadata and
    # citations show e.g. "infosys_fy2025.pdf".
    pdf_name = state.get("filename") or Path(pdf_path).name
    t0 = time.perf_counter()

    logger.info("[chunk] doc=%s  pages=%d  start", doc_id, len(pages))
    _set_status(doc_id, "chunking")

    try:
        # Re-parse statement pages with layout-preserving pass for accurate columns.
        # parse_layout returns dict[int, str] (pdf_page_number -> text).
        statement_page_nums = [
            t.pdf_page
            for t in tags
            if t.section in STATEMENT_SECTIONS and not t.is_image_only
        ]
        if statement_page_nums:
            layout_text_map: dict[int, str] = parse_layout(pdf_path, statement_page_nums)
            # Replace the text on matching PageResult objects with the
            # layout-preserved version (preserves column alignment in tables).
            import dataclasses
            pages = [
                dataclasses.replace(p, text=layout_text_map[p.pdf_page])
                if p.pdf_page in layout_text_map
                else p
                for p in pages
            ]

        # Tiered mode: chunk only tier-1 pages. Tier-2 pages are chunked and
        # embedded later, on demand (see app/lazy_index.py).
        if settings.index_mode == "tiered":
            pairs = [(p, t) for p, t in zip(pages, tags) if getattr(t, "tier", 2) == 1]
            chunk_src_pages = [p for p, _ in pairs]
            chunk_src_tags = [t for _, t in pairs]
        else:
            chunk_src_pages, chunk_src_tags = pages, tags
        raw_chunks = chunk_pages(chunk_src_pages, chunk_src_tags, pdf_name=pdf_name)

        # Inject pipeline-level metadata that chunk_pages doesn’t know about.
        # index_chunks also sets doc_id but it’s cleaner to have it here too.
        extra = {
            "doc_id":      str(doc_id),
            "session_id":  str(state.get("session_id", "")),
            "company":     state.get("company", ""),
            "fiscal_year": str(state.get("fiscal_year", "")),
        }
        chunks = []
        for chunk in raw_chunks:
            meta = {**chunk["metadata"], **extra}
            chunks.append({"text": chunk["text"], "metadata": meta})

        elapsed = time.perf_counter() - t0
        logger.info(
            "[chunk] doc=%s  chunks=%d  elapsed=%.2fs",
            doc_id,
            len(chunks),
            elapsed,
        )
        return {**state, "chunks": chunks, "pages": pages}

    except Exception as exc:
        logger.exception("[chunk] doc=%s  FAILED: %s", doc_id, exc)
        return {**state, "error": f"chunk failed: {exc}"}


def _node_index(state: DocumentState) -> DocumentState:
    """Node 4 – Embed chunks and write them to Chroma Cloud."""
    if state.get("error"):
        return state

    doc_id = state["doc_id"]
    chunks = state["chunks"]
    t0 = time.perf_counter()

    logger.info("[index] doc=%s  chunks=%d  start", doc_id, len(chunks))
    # index_chunks sets status to "indexing" then "ready" (or "failed") internally

    try:
        embedded_pages = index_chunks(doc_id, chunks)
        mark_indexed(doc_id, embedded_pages or [])
        elapsed = time.perf_counter() - t0
        logger.info(
            "[index] doc=%s  chunks=%d  elapsed=%.2fs",
            doc_id,
            len(chunks),
            elapsed,
        )
        return state

    except Exception as exc:
        logger.exception("[index] doc=%s  FAILED: %s", doc_id, exc)
        # index_chunks already sets status=failed on quota/network errors.
        # If the error slipped through (e.g. fingerprint mismatch), handle it here.
        return {**state, "error": f"index failed: {exc}"}


def _node_fail(state: DocumentState) -> DocumentState:
    """Terminal error node – persists the error message to the Document row."""
    doc_id = state["doc_id"]
    error = state.get("error", "unknown error")
    logger.error("[fail] doc=%s  error=%s", doc_id, error)
    try:
        _set_status(doc_id, "failed", error_message=error)
    except Exception as db_exc:  # pragma: no cover
        logger.error("[fail] could not persist error to DB: %s", db_exc)
    return state


# ─────────────────────────────────────────────────────────────────────────────
# Conditional routing helper
# ─────────────────────────────────────────────────────────────────────────────

def _route(state: DocumentState) -> Literal["continue", "fail"]:
    """Route to 'fail' if state carries an error, otherwise 'continue'."""
    return "fail" if state.get("error") else "continue"


# ─────────────────────────────────────────────────────────────────────────────
# Graph assembly
# ─────────────────────────────────────────────────────────────────────────────

def _build_graph() -> StateGraph:
    g = StateGraph(DocumentState)

    # Nodes
    g.add_node("parse", _node_parse)
    g.add_node("tag", _node_tag)
    g.add_node("chunk", _node_chunk)
    g.add_node("index", _node_index)
    g.add_node("fail", _node_fail)

    # Entry point
    g.set_entry_point("parse")

    # Conditional edges: on error → fail, else continue to next node
    g.add_conditional_edges(
        "parse",
        _route,
        {"continue": "tag", "fail": "fail"},
    )
    g.add_conditional_edges(
        "tag",
        _route,
        {"continue": "chunk", "fail": "fail"},
    )
    g.add_conditional_edges(
        "chunk",
        _route,
        {"continue": "index", "fail": "fail"},
    )
    g.add_conditional_edges(
        "index",
        _route,
        {"continue": END, "fail": "fail"},
    )

    # fail is always terminal
    g.add_edge("fail", END)

    return g


# Compile once at module load time – re-used for every document
_graph = _build_graph().compile()


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def run_document_agent(doc_id: uuid.UUID) -> None:
    """
    Download the PDF, run parse → tag → chunk → index, then clean up.

    This is the function called by:
      - FastAPI background tasks (after upload)
      - ``seed.py`` for the 4 pre-loaded reports

    The temp file is always deleted – even if the pipeline fails – so the
    API server's disk doesn't fill up.

    Parameters
    ----------
    doc_id:
        UUID of an existing ``Document`` row.  The row must already have
        ``storage_key``, ``company``, ``fiscal_year`` and ``session_id`` set.
    """
    doc = _get_document(doc_id)
    pdf_path: str | None = None

    try:
        # 1️⃣  Download PDF from cloud storage to a local temp file
        logger.info("run_document_agent: doc=%s  downloading %s", doc_id, doc.storage_key)
        pdf_path = download_to_temp(doc.storage_key)
        logger.info("run_document_agent: doc=%s  pdf_path=%s", doc_id, pdf_path)

        # 2️⃣  Build initial state
        initial_state: DocumentState = {
            "doc_id": doc.id,
            "pdf_path": pdf_path,
            "filename": doc.filename,
            "company": doc.company or "",
            "fiscal_year": str(doc.fiscal_year or ""),
            "session_id": doc.session_id,
            "pages": [],
            "tags": [],
            "chunks": [],
            "error": None,
        }

        # 3️⃣  Run the graph
        t0 = time.perf_counter()
        final_state: DocumentState = _graph.invoke(initial_state)
        elapsed = time.perf_counter() - t0

        if final_state.get("error"):
            logger.error(
                "run_document_agent: doc=%s  FAILED after %.2fs  error=%s",
                doc_id,
                elapsed,
                final_state["error"],
            )
        else:
            logger.info(
                "run_document_agent: doc=%s  DONE  pages=%d  chunks=%d  elapsed=%.2fs",
                doc_id,
                len(final_state.get("pages", [])),
                len(final_state.get("chunks", [])),
                elapsed,
            )

    finally:
        # 4️⃣  Always delete the temp file
        if pdf_path and Path(pdf_path).exists():
            try:
                os.unlink(pdf_path)
                logger.debug("run_document_agent: deleted temp file %s", pdf_path)
            except OSError as exc:  # pragma: no cover
                logger.warning("run_document_agent: could not delete temp file: %s", exc)
