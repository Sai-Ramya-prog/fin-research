"""
app/retrieval.py
-----------------------------------------------------------------------------
Filtered similarity search helper.

Public API
----------
    search(query, *, company=None, section=None, scope=None,
           fiscal_year=None, k=5) -> list[SearchResult]

    SearchResult - namedtuple with fields:
        text      (str)   - the chunk text
        metadata  (dict)  - the chunk's metadata (payload without the text)
        score     (float) - cosine similarity (higher = more similar)

Design notes
------------
* Talks to the vector database only through ``app.vectorstore`` (Qdrant).
* Embeds the query with ``get_embeddings().embed_query()`` - the same provider
  and model used at indexing time, so vectors are comparable.
* Optional filters are exact matches on payload fields; several filters are
  combined with AND.
* With the ``fake`` embedding provider only filter behaviour is meaningful
  (scores are arbitrary); real semantic ranking needs Gemini.
"""

from __future__ import annotations

import logging
from typing import Any, NamedTuple, Optional

from app import vectorstore
from app.embeddings import get_embeddings

logger = logging.getLogger(__name__)

# Payload keys that are bookkeeping, not chunk metadata.
_INTERNAL_KEYS = {"text", "chunk_id", "embedding_fingerprint"}


class SearchResult(NamedTuple):
    """A single hit from a similarity search."""

    text: str
    """The chunk text."""

    metadata: dict[str, Any]
    """Metadata attached to this chunk at indexing time."""

    score: float
    """Cosine similarity (higher is more similar)."""


def _build_filter(
    company: Optional[str],
    section: Optional[str],
    scope: Optional[str],
    fiscal_year: Optional[str],
    document_id: Optional[str] = None,
) -> Optional[dict[str, str]]:
    """Map the optional filters to ``{payload_field: exact_value}`` (AND).

    Returns ``None`` when no filter was given.
    """
    must: dict[str, str] = {}
    if document_id:
        must["doc_id"] = str(document_id)
    if company:
        must["company"] = company
    if section:
        must["section"] = section
    if scope:
        must["scope"] = scope
    if fiscal_year:
        must["fiscal_year"] = str(fiscal_year)
    return must or None


def search(
    query: str,
    *,
    company: Optional[str] = None,
    section: Optional[str] = None,
    scope: Optional[str] = None,
    fiscal_year: Optional[str] = None,
    document_id: Optional[str] = None,
    k: int = 5,
) -> list[SearchResult]:
    """
    Similarity search over the Qdrant collection with optional metadata filters.

    Parameters
    ----------
    query:
        Natural-language query string to embed and search.
    company, section, scope, fiscal_year, document_id:
        Optional exact-match filters (e.g. ``section="balance_sheet"``,
        ``scope="consolidated"``). ``document_id`` limits the search to one
        uploaded document.
    k:
        Maximum number of results to return (default 5).

    Returns
    -------
    list[SearchResult]
        Ordered by descending similarity (most relevant first). Empty when the
        collection is empty or nothing matches.
    """
    if not query or not query.strip():
        raise ValueError("query must be a non-empty string")

    query_vector = get_embeddings().embed_query(query.strip())
    logger.debug("search: embedded query (%d dims)", len(query_vector))

    must = _build_filter(company, section, scope, fiscal_year, document_id)
    logger.debug("search: filter=%s  k=%d", must, k)

    hits = vectorstore.search(query_vector, limit=k, must=must)

    results: list[SearchResult] = []
    for hit in hits:
        payload = dict(hit.payload or {})
        text = payload.get("text", "")
        meta = {key: val for key, val in payload.items() if key not in _INTERNAL_KEYS}
        results.append(SearchResult(text=text, metadata=meta, score=float(hit.score)))

    logger.info(
        "search: query=%r  company=%s  section=%s  scope=%s  hits=%d",
        query[:60], company, section, scope, len(results),
    )
    return results
