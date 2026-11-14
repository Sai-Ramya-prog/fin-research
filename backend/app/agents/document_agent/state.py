"""
app/agents/document_agent/state.py
───────────────────────────────────────────────────────────────────────────────
LangGraph TypedDict state for the Document Agent.

Fields
------
doc_id          UUID of the Document row being processed.
pdf_path        Local temp path to the downloaded PDF (set by the graph entry
                point, deleted after the graph finishes).
company         Company name tag (passed in from the Document row).
fiscal_year     Fiscal year string (passed in from the Document row).
pages           Output of parse_fast / parse_layout – list of page dicts.
tags            Output of tag_pages – list of PageTag-like dicts.
chunks          Output of chunk_pages – list of chunk dicts (text + metadata).
error           If set, a human-readable error message that caused the
                pipeline to abort.  The ``fail`` node reads this to persist
                the error to the DB.
"""

from __future__ import annotations

import uuid
from typing import Any, List, Optional
from typing_extensions import TypedDict


class DocumentState(TypedDict, total=False):
    """Mutable state passed between nodes in the Document Agent graph."""

    # ── Input ─────────────────────────────────────────────────────────────────
    doc_id: uuid.UUID
    """UUID of the Document row.  Set before the graph is invoked."""

    pdf_path: str
    """Absolute local path to the downloaded PDF temp file."""

    filename: str
    """Original PDF file name (used as pdf_name in chunk metadata)."""

    company: str
    """Company name – copied from Document.company."""

    fiscal_year: str
    """Fiscal year string – copied from Document.fiscal_year."""

    session_id: uuid.UUID
    """Session UUID – needed for chunk metadata."""

    # ── Pipeline outputs ──────────────────────────────────────────────────────
    pages: List[Any]
    """
    List of page dicts from parse_fast / parse_layout.
    Each dict has at minimum: pdf_page (int, 1-indexed), text (str),
    has_text (bool), image_count (int), is_image_only (bool).
    """

    tags: List[Any]
    """
    List of tag dicts from tag_pages.
    Each dict carries: pdf_page, section, scope, unit, content_type,
    printed_page, is_image_only.
    """

    chunks: List[Any]
    """
    List of chunk dicts from chunk_pages.
    Each dict has: text (str), metadata (dict).
    """

    # ── Error handling ────────────────────────────────────────────────────────
    error: Optional[str]
    """
    Non-None when a node has caught a fatal error.  The conditional edge
    after each node routes to the ``fail`` node when this is set.
    """
