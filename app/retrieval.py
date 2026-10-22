"""
app/retrieval.py
───────────────────────────────────────────────────────────────────────────────
Filtered similarity search helper (Task 9).

Public API
----------
    search(query, *, company=None, section=None, scope=None,
           fiscal_year=None, k=5) -> list[SearchResult]

    SearchResult – namedtuple with fields:
        text      (str)   – the chunk text
        metadata  (dict)  – full Chroma metadata dict
        score     (float) – cosine similarity score (higher = more similar)

Design notes
------------
* Connects to the same Chroma Cloud collection as ``app.index`` (re-uses
  ``_cloud_client`` / settings).
* Embeds the query with ``get_embeddings().embed_query()`` – the same
  provider and model used at indexing time, so vectors are comparable.
* Builds a metadata ``where`` filter using ``$and`` for multiple conditions.
  If only one condition is provided, passes it directly (Chroma rejects a
  single-element ``$and`` list on some versions).
* Returns results ordered by descending similarity score (Chroma default).
* With the ``fake`` provider only filter behaviour is tested (dot-product
  scores are arbitrary); real semantic ranking requires Gemini or OpenAI.
"""

from __future__ import annotations

import logging
from typing import Any, NamedTuple, Optional

import chromadb

from app.config import settings
from app.embeddings import get_embeddings

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Result type
# ─────────────────────────────────────────────────────────────────────────────

class SearchResult(NamedTuple):
    """A single hit from a Chroma similarity search."""

    text: str
    """The chunk text stored in Chroma."""

    metadata: dict[str, Any]
    """Full metadata dict attached to this chunk at indexing time."""

    score: float
    """
    Cosine similarity score returned by Chroma.
    Higher is more similar (range: 0–1 for cosine / L2 normalised).
    """


# ─────────────────────────────────────────────────────────────────────────────
# Chroma connection helper (mirrors app.index._cloud_client)
# ─────────────────────────────────────────────────────────────────────────────

def _get_collection() -> chromadb.api.models.Collection.Collection:
    """Return the Chroma collection for querying."""
    required = {
        "chroma_tenant": "CHROMA_TENANT",
        "chroma_database": "CHROMA_DATABASE",
        "chroma_api_key": "CHROMA_API_KEY",
        "chroma_collection": "CHROMA_COLLECTION",
    }
    missing = [env for attr, env in required.items() if not getattr(settings, attr)]
    if missing:
        raise RuntimeError(
            f"Chroma Cloud configuration incomplete – missing: {', '.join(missing)}"
        )
    client = chromadb.CloudClient(
        tenant=settings.chroma_tenant,
        database=settings.chroma_database,
        api_key=settings.chroma_api_key,
    )
    return client.get_collection(name=settings.chroma_collection)


# ─────────────────────────────────────────────────────────────────────────────
# Filter builder
# ─────────────────────────────────────────────────────────────────────────────

def _build_where(
    company: Optional[str],
    section: Optional[str],
    scope: Optional[str],
    fiscal_year: Optional[str],
) -> Optional[dict[str, Any]]:
    """
    Build a Chroma ``where`` filter dict.

    Single condition → ``{"field": {"$eq": "value"}}``
    Multiple conditions → ``{"$and": [{"field": {"$eq": "value"}}, ...]}``
    No conditions → ``None`` (no filtering)
    """
    clauses: list[dict[str, Any]] = []

    if company:
        clauses.append({"company": {"$eq": company}})
    if section:
        clauses.append({"section": {"$eq": section}})
    if scope:
        clauses.append({"scope": {"$eq": scope}})
    if fiscal_year:
        clauses.append({"fiscal_year": {"$eq": str(fiscal_year)}})

    if not clauses:
        return None
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


# ─────────────────────────────────────────────────────────────────────────────
# Public search function
# ─────────────────────────────────────────────────────────────────────────────

def search(
    query: str,
    *,
    company: Optional[str] = None,
    section: Optional[str] = None,
    scope: Optional[str] = None,
    fiscal_year: Optional[str] = None,
    k: int = 5,
) -> list[SearchResult]:
    """
    Similarity search over the Chroma Cloud collection with optional metadata
    filtering.

    Parameters
    ----------
    query:
        Natural-language query string to embed and search.
    company:
        Filter to chunks from this company (exact match on ``company`` field).
    section:
        Filter to a specific financial section, e.g. ``"balance_sheet"``,
        ``"profit_loss"``, ``"cash_flow"``.
    scope:
        Filter to ``"consolidated"`` or ``"standalone"``.
    fiscal_year:
        Filter to a specific fiscal year string, e.g. ``"FY2024"``.
    k:
        Maximum number of results to return (default 5).

    Returns
    -------
    list[SearchResult]
        Ordered by descending similarity score (most relevant first).
        Returns an empty list if the collection is empty or no results match.

    Raises
    ------
    RuntimeError
        If Chroma Cloud settings are missing or the collection does not exist.
    """
    if not query or not query.strip():
        raise ValueError("query must be a non-empty string")

    # 1. Embed the query
    emb = get_embeddings()
    query_vector = emb.embed_query(query.strip())
    logger.debug("search: embedded query (%d dims)", len(query_vector))

    # 2. Build metadata filter
    where = _build_where(company, section, scope, fiscal_year)
    logger.debug("search: where=%s  k=%d", where, k)

    # 3. Query Chroma
    collection = _get_collection()
    query_kwargs: dict[str, Any] = {
        "query_embeddings": [query_vector],
        "n_results": k,
        "include": ["documents", "metadatas", "distances"],
    }
    if where is not None:
        query_kwargs["where"] = where

    raw = collection.query(**query_kwargs)

    # 4. Unpack results
    # Chroma returns lists-of-lists (one inner list per query vector)
    documents: list[str] = raw.get("documents", [[]])[0]
    metadatas: list[dict] = raw.get("metadatas", [[]])[0]
    distances: list[float] = raw.get("distances", [[]])[0]

    # Chroma returns L2 distances (lower = more similar); convert to a
    # similarity score in [0, 1] using: score = 1 / (1 + distance).
    # For cosine space Chroma returns (1 - cosine_sim); adjust accordingly.
    results: list[SearchResult] = []
    for doc, meta, dist in zip(documents, metadatas, distances):
        # score: 1 = identical, 0 = maximally dissimilar
        score = 1.0 / (1.0 + dist) if dist >= 0 else 0.0
        results.append(SearchResult(text=doc, metadata=meta or {}, score=score))

    logger.info(
        "search: query=%r  company=%s  section=%s  scope=%s  hits=%d",
        query[:60],
        company,
        section,
        scope,
        len(results),
    )
    return results
