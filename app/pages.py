"""
app/pages.py
─────────────────────────────────────────────────────────────────────────────
Page store for tiered indexing (table ``page_tags``).

Every page's text and tags are saved when a PDF is processed – this costs no
embedding quota. Tier-1 pages are embedded straight away; tier-2 pages stay
here until a question needs them. ``find_pages`` locates the likely pages by
keyword, and ``app.lazy_index.index_more`` embeds just those.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Iterable, Sequence

from app.db.engine import session_scope
from app.db.models import PageTag

_STOPWORDS = {
    "the", "and", "for", "are", "was", "were", "what", "why", "how", "did", "does",
    "this", "that", "with", "from", "about", "into", "has", "have", "had", "its",
    "their", "which", "when", "where", "who", "than", "then", "year", "company",
}


def save_pages(document_id: uuid.UUID, pages: Sequence[Any], tags: Sequence[Any]) -> None:
    """Replace all stored rows for a document with the given pages + tags."""
    with session_scope() as db:
        db.query(PageTag).filter(PageTag.document_id == document_id).delete()
        for page, tag in zip(pages, tags):
            db.add(
                PageTag(
                    document_id=document_id,
                    pdf_page=tag.pdf_page,
                    printed_page=tag.printed_page,
                    section=tag.section,
                    scope=tag.scope,
                    unit=tag.unit,
                    content_type=tag.content_type,
                    is_image_only=tag.is_image_only,
                    tier=getattr(tag, "tier", 2),
                    text=None if tag.is_image_only else page.text,
                    indexed=False,
                )
            )


def mark_indexed(document_id: uuid.UUID, pdf_pages: Iterable[int]) -> None:
    pages = sorted(set(int(p) for p in pdf_pages))
    if not pages:
        return
    with session_scope() as db:
        (
            db.query(PageTag)
            .filter(PageTag.document_id == document_id, PageTag.pdf_page.in_(pages))
            .update({PageTag.indexed: True}, synchronize_session=False)
        )


def load_unindexed_pages(document_id: uuid.UUID) -> list[dict[str, Any]]:
    """All stored pages that have text and are not embedded yet."""
    with session_scope() as db:
        rows = (
            db.query(PageTag)
            .filter(
                PageTag.document_id == document_id,
                PageTag.indexed.is_(False),
                PageTag.is_image_only.is_(False),
                PageTag.text.isnot(None),
            )
            .order_by(PageTag.pdf_page)
            .all()
        )
        return [
            {
                "pdf_page": r.pdf_page,
                "printed_page": r.printed_page,
                "section": r.section or "other",
                "scope": r.scope or "unknown",
                "unit": r.unit or "",
                "content_type": r.content_type or "narrative",
                "tier": r.tier,
                "text": r.text or "",
            }
            for r in rows
        ]


def score_pages(query: str, rows: Sequence[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    """Rank pages by keyword match with *query* (pure function, no I/O).

    Score = 10 × distinct query words found + total occurrences, plus a bonus
    when the whole query phrase appears. Pages with no match are dropped.
    """
    q = query.lower().strip()
    words = [w for w in re.findall(r"[a-z0-9]{3,}", q) if w not in _STOPWORDS]
    if not words:
        return []
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for row in rows:
        text = (row.get("text") or "").lower()
        distinct = sum(1 for w in set(words) if w in text)
        if distinct == 0:
            continue
        occurrences = sum(text.count(w) for w in set(words))
        score = 10 * distinct + occurrences + (15 if q in text else 0)
        scored.append((score, -row["pdf_page"], row))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [r for _, _, r in scored[:limit]]


def find_pages(document_id: uuid.UUID, query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Likely not-yet-embedded pages for *query* (keyword search over stored text)."""
    return score_pages(query, load_unindexed_pages(document_id), limit=limit)
