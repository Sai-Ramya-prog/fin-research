"""
app/index.py
───────────────────────────────────────────────────────────────────────
Embedding & Vector‑store indexing – Document Agent, node 4.

The pipeline receives a list of *chunks* produced by ``chunk_pages`` – each
item is a ``dict`` with the keys ``text`` (str) and ``metadata`` (dict).  The
metadata already contains all Chroma‑safe fields required by the design
(``pdf_name``, ``pdf_page``, ``printed_page``, ``section`` … ``source_type``).

This module:
  * Connects to **Chroma Cloud** using the tenant/database/api‑key from ``.env``.
  * Verifies that the collection was created with the same embedding fingerprint
    (provider + model + dimension).  If the fingerprint differs we raise a clear
    error – the user must re‑index everything (or run the ``reindex_all`` CLI).
  * Provides ``index_chunks`` which is **idempotent** and **resumable**:
      - Before indexing a document we delete any existing chunks whose
        ``doc_id`` matches the document.
      - Chunks are written in batches of at most ``CHROMA_MAX_BATCH`` (30 0).
        Within each batch we embed the texts in sub‑batches of
        ``EMBEDDING_BATCH_SIZE`` (default 32) and pause
        ``EMBEDDING_BATCH_PAUSE_SECONDS`` between those sub‑batches.
      - Network or rate‑limit errors trigger a retry with exponential back‑off.
        If the error is identified as a quota‑exhaustion (HTTP 429) we mark the
        document ``failed`` and stop further indexing – the next run will pick up
        where it left off.
  * Updates the ``Document`` row status at each stage so the FastAPI endpoints
    can report progress.
  * Exposes a small CLI (`reindex_all`) for manual re‑indexing of the entire
    collection.
"""

from __future__ import annotations

import time
import uuid
import logging
from pathlib import Path
from typing import List, Dict, Any

import chromadb
from chromadb.api.models.Collection import Collection

from app.config import settings
from app.embeddings import QuotaExhaustedError, get_embeddings, embedding_fingerprint
from app.db.models import Document
from app.db.engine import session_scope

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration constants (mirroring the implementation plan)
# ---------------------------------------------------------------------------
CHROMA_MAX_BATCH = 300  # max records per ``add_documents`` call (cloud limit)
EMBED_BATCH_SIZE = settings.embedding_batch_size  # usually 32
EMBED_BATCH_PAUSE = settings.embedding_batch_pause_seconds

# ---------------------------------------------------------------------------
# Helper – Chroma Cloud client & collection
# ---------------------------------------------------------------------------

def _cloud_client() -> chromadb.CloudClient:
    """Create a ``chromadb.CloudClient`` from the environment settings.

    ``settings`` validates that the required variables are present; if any are
    missing we raise ``RuntimeError`` with a helpful message.
    """
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
    return client


def _get_collection() -> Collection:
    """Return the collection, creating it if necessary, and verify fingerprint.

    The collection metadata stores a single key ``embedding_fingerprint``.  If the
    stored value differs from the current ``embedding_fingerprint()`` we raise an
    error – the user must re‑create the collection (or call ``reindex_all``).
    """
    client = _cloud_client()
    collection = client.get_or_create_collection(
        name=settings.chroma_collection,
        metadata={"embedding_fingerprint": embedding_fingerprint()},
    )
    stored_fp = collection.metadata.get("embedding_fingerprint", "")
    current_fp = embedding_fingerprint()
    if stored_fp != current_fp:
        raise RuntimeError(
            f"Embedding fingerprint mismatch: stored='{stored_fp}' vs current='{current_fp}'. "
            "Delete the collection or run `reindex_all` to rebuild with the new model."
        )
    return collection

# ---------------------------------------------------------------------------
# Status helpers – keep the DB in sync with the pipeline
# ---------------------------------------------------------------------------

def _set_document_status(
    doc_id: uuid.UUID, new_status: str, error_message: str | None = None
) -> None:
    """Update ``Document.status`` (and optional ``error_message``) in a new session.
    The function commits immediately because indexing can be a long‑running
    background task and we want the status visible to the API.
    """
    with session_scope() as session:
        doc = session.get(Document, doc_id)
        if doc is None:
            raise RuntimeError(f"Document {doc_id} not found while updating status")
        doc.status = new_status
        if error_message is not None:
            doc.error_message = error_message
        session.commit()
        logger.info("Document %s status → %s", doc_id, new_status)

# ---------------------------------------------------------------------------
# Core indexing function
# ---------------------------------------------------------------------------

def _existing_ids(collection: Collection, ids: List[str]) -> set[str]:
    """Return the subset of *ids* that already exist in the collection."""
    found: set[str] = set()
    for start in range(0, len(ids), CHROMA_MAX_BATCH):
        part = ids[start : start + CHROMA_MAX_BATCH]
        res = collection.get(ids=part, include=[])
        found.update(res.get("ids", []))
    return found


def _delete_ids(collection: Collection, ids: List[str]) -> None:
    for start in range(0, len(ids), CHROMA_MAX_BATCH):
        collection.delete(ids=ids[start : start + CHROMA_MAX_BATCH])


def _delete_document_chunks(collection: Collection, document_id: uuid.UUID) -> int:
    """Delete every stored chunk of a document. Returns how many were removed."""
    res = collection.get(where={"doc_id": str(document_id)}, include=[])
    old_ids = res.get("ids", []) if res else []
    _delete_ids(collection, old_ids)
    return len(old_ids)


def _delete_legacy_ids(collection: Collection, document_id: uuid.UUID) -> int:
    """Remove chunks stored with the OLD id scheme ``<doc>:<n>``.

    New ids are ``<doc>:<pdf_page>:<k>`` (two colons). Old-scheme chunks were
    built by an earlier version of the chunker and would pollute search.
    """
    res = collection.get(where={"doc_id": str(document_id)}, include=[])
    legacy = [i for i in (res.get("ids", []) if res else []) if i.count(":") == 1]
    _delete_ids(collection, legacy)
    return len(legacy)


def chunk_ids(document_id: uuid.UUID, chunks: List[Dict[str, Any]]) -> List[str]:
    """Deterministic ids ``<doc>:<pdf_page>:<k>`` (k = chunk number on that page).

    Based on the page, not on the position in the whole list, so embedding more
    pages later (tier 2) never changes the ids of pages already stored.
    """
    counters: Dict[str, int] = {}
    ids: List[str] = []
    for c in chunks:
        page = str(c["metadata"].get("pdf_page", 0))
        k = counters.get(page, 0)
        counters[page] = k + 1
        ids.append(f"{document_id}:{page}:{k}")
    return ids


def _prepare_metadata(meta_in: Dict[str, Any], document_id: uuid.UUID, i: int) -> Dict[str, Any]:
    meta = dict(meta_in)
    meta.setdefault("doc_id", str(document_id))
    meta.setdefault("chunk_index", i)
    if meta.get("printed_page") in (None, ""):
        meta["printed_page"] = -1
    for k, v in list(meta.items()):
        if v is None:
            meta[k] = ""
    return meta


def index_chunks(
    document_id: uuid.UUID,
    chunks: List[Dict[str, Any]],
    force: bool = False,
    track_status: bool = True,
) -> List[int]:
    """Index *chunks* for a single document in Chroma Cloud.

    **Resumable**: ids are deterministic, so before embedding anything we ask
    Chroma which ids already exist and embed only the missing ones. A re-run
    after a failure never pays for the same chunk twice.

    ``force=True`` deletes the document's stored chunks first and re-embeds.
    ``track_status=False`` leaves ``Document.status`` alone (used when embedding
    extra tier-2 pages for a document that is already ``ready``).

    A daily-quota error from Gemini stops immediately, marks the document
    ``failed`` (when tracking status) and re-raises ``QuotaExhaustedError``.
    Chunks already written stay in Chroma for the next run.

    Returns the PDF page numbers whose chunks are now all stored.
    """
    collection = _get_collection()

    if force:
        removed = _delete_document_chunks(collection, document_id)
        logger.info("force: deleted %d stored chunks for %s", removed, document_id)
    else:
        legacy = _delete_legacy_ids(collection, document_id)
        if legacy:
            logger.info("Removed %d old-format chunks for %s", legacy, document_id)

    total = len(chunks)
    all_ids = chunk_ids(document_id, chunks)
    done = _existing_ids(collection, all_ids)
    todo = [i for i in range(total) if all_ids[i] not in done]
    logger.info(
        "Indexing %s: %d chunks total, %d already stored, %d to embed",
        document_id, total, len(done), len(todo),
    )

    if track_status:
        _set_document_status(document_id, "indexing")
    emb = get_embeddings()

    try:
        for start in range(0, len(todo), EMBED_BATCH_SIZE):
            idxs = todo[start : start + EMBED_BATCH_SIZE]
            texts = [chunks[i]["text"] for i in idxs]
            ids = [all_ids[i] for i in idxs]
            metas = [_prepare_metadata(chunks[i]["metadata"], document_id, i) for i in idxs]

            vectors = emb.embed_documents(texts)  # retries + quota detection inside

            # Write straight after each embedded batch so progress is never lost.
            attempt = 0
            while True:
                try:
                    collection.add(ids=ids, documents=texts, metadatas=metas, embeddings=vectors)
                    break
                except Exception as exc:  # network errors
                    attempt += 1
                    if attempt >= 3:
                        raise
                    logger.warning("Chroma add error (attempt %d): %s", attempt, exc)
                    time.sleep(2 ** attempt)
            logger.info("Stored %d/%d new chunks", start + len(idxs), len(todo))
    except QuotaExhaustedError as exc:
        if track_status:
            _set_document_status(document_id, "failed", error_message=str(exc))
        raise
    except Exception as exc:
        if track_status:
            _set_document_status(document_id, "failed", error_message=f"Indexing failed: {exc}")
        raise

    if track_status:
        _set_document_status(document_id, "ready")
    logger.info("Indexing complete for document %s", document_id)
    return sorted({int(c["metadata"].get("pdf_page", 0)) for c in chunks})

# ---------------------------------------------------------------------------
# Utility – connection health check
# ---------------------------------------------------------------------------

def ping_chroma() -> bool:
    """Return ``True`` if a collection can be queried; otherwise ``False``.
    Used by the health endpoint to report Chroma availability.
    """
    try:
        coll = _get_collection()
        # A cheap call – fetch zero results
        coll.get(limit=0)
        return True
    except Exception as exc:  # pragma: no cover
        logger.error("Chroma ping failed: %s", exc)
        return False

# ---------------------------------------------------------------------------
# CLI entry point – reindex everything (useful during development)
# ---------------------------------------------------------------------------
if __name__ == "__main__":  # pragma: no cover
    import argparse
    from app.db.models import Session, Document
    from app.db.engine import session_scope
    from app.chunk import chunk_pages
    from app.parse import parse_fast
    from app.tag import tag_pages

    parser = argparse.ArgumentParser(description="Re‑index all ready documents")
    parser.add_argument("--session-id", type=str, help="Only re‑index a single session")
    args = parser.parse_args()

    with session_scope() as db:
        query = db.query(Document).filter(Document.status == "ready")
        if args.session_id:
            query = query.filter(Document.session_id == uuid.UUID(args.session_id))
        docs = query.all()
        for doc in docs:
            pdf_path = Path(doc.storage_key)  # assuming the key is a local temp path for dev
            pages = parse_fast(str(pdf_path))
            tags = tag_pages(pages, pdf_name=pdf_path.name)
            chunks = chunk_pages(pages, tags, pdf_name=pdf_path.name)
            index_chunks(doc.id, chunks)
            print(f"Re‑indexed document {doc.id}")

