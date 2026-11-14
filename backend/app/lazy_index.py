"""
app/lazy_index.py
─────────────────────────────────────────────────────────────────────────────
Embed more pages of an already-processed document, on demand.

Tiered indexing embeds only the important pages (tier 1) after upload. When a
later question ("why did margin fall?") needs other pages, an agent calls
``index_more(document_id, query)``:

  1. keyword-search the stored page text for pages not embedded yet,
  2. chunk just those pages,
  3. embed + store them (resumable, same ids scheme),
  4. mark them as indexed.

``Document.status`` is not touched – the document stays ``ready``.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from app.chunk import chunk_pages
from app.db.engine import session_scope
from app.db.models import Document
from app.index import index_chunks
from app.pages import find_pages, mark_indexed
from app.parse import PageResult
from app.tag import TagResult

logger = logging.getLogger(__name__)


def _rows_to_pages_and_tags(rows: list[dict[str, Any]]):
    pages = [
        PageResult(pdf_page=r["pdf_page"], text=r["text"], has_text=True,
                   image_count=0, is_image_only=False)
        for r in rows
    ]
    tags = [
        TagResult(
            pdf_page=r["pdf_page"], section=r["section"], scope=r["scope"],
            unit=r["unit"], content_type=r["content_type"],
            printed_page=r["printed_page"], tier=r["tier"],
        )
        for r in rows
    ]
    return pages, tags


def index_more(document_id: uuid.UUID, query: str, limit: int = 5) -> list[int]:
    """Embed up to *limit* not-yet-embedded pages that best match *query*.

    Returns the PDF page numbers that were embedded (empty if nothing matched).
    May raise ``QuotaExhaustedError`` if the Gemini daily quota is gone.
    """
    rows = find_pages(document_id, query, limit=limit)
    if not rows:
        logger.info("index_more: no un-embedded pages match %r for %s", query, document_id)
        return []

    with session_scope() as db:
        doc = db.get(Document, document_id)
        if doc is None:
            raise RuntimeError(f"Document {document_id} not found")
        extra = {
            "doc_id": str(document_id),
            "session_id": str(doc.session_id),
            "company": doc.company or "",
            "fiscal_year": str(doc.fiscal_year or ""),
        }
        pdf_name = doc.filename

    pages, tags = _rows_to_pages_and_tags(rows)
    raw = chunk_pages(pages, tags, pdf_name=pdf_name)
    chunks = [{"text": c["text"], "metadata": {**c["metadata"], **extra}} for c in raw]

    embedded = index_chunks(document_id, chunks, track_status=False)
    mark_indexed(document_id, embedded)
    logger.info("index_more: embedded pages %s for %s", embedded, document_id)
    return embedded
