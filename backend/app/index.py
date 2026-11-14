"""
app/index.py
───────────────────────────────────────────────────────────────────────
Embedding & Vector‑store indexing – Document Agent, node 4.

The pipeline receives a list of *chunks* produced by ``chunk_pages`` - each
item is a ``dict`` with the keys ``text`` (str) and ``metadata`` (dict).  The
metadata already contains all the fields required by the design
(``pdf_name``, ``pdf_page``, ``printed_page``, ``section`` ... ``source_type``).

This module:
  * Stores chunks in **Qdrant** through ``app.vectorstore`` (the only module
    that talks to the vector database).
  * ``vectorstore.ensure_collection`` checks that the collection's vector size
    matches the current embedding model; a mismatch raises a clear error.
  * Provides ``index_chunks`` which is **idempotent** and **resumable**:
      - Chunk ids are deterministic, so chunks that are already stored are
        skipped and never embedded (or paid for) twice.
      - ``force=True`` first deletes the document's stored chunks.
      - Chunks are embedded in batches of ``EMBEDDING_BATCH_SIZE`` (default 32)
        with a pause of ``EMBEDDING_BATCH_PAUSE_SECONDS`` between batches, and
        each batch is written straight after it is embedded.
      - A daily-quota error stops immediately: the document is marked
        ``failed`` and the next run picks up where it left off.
  * Updates the ``Document`` row status at each stage so the FastAPI endpoints
    can report progress.
  * Exposes a small CLI for manual re-indexing of ready documents.
"""

from __future__ import annotations

import time
import uuid
import logging
from pathlib import Path
from typing import List, Dict, Any

from app import vectorstore
from app.config import settings
from app.embeddings import QuotaExhaustedError, get_embeddings
from app.db.models import Document
from app.db.engine import session_scope

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------
EMBED_BATCH_SIZE = settings.embedding_batch_size  # usually 32
EMBED_BATCH_PAUSE = settings.embedding_batch_pause_seconds

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

def _existing_ids(ids: List[str]) -> set[str]:
    """Return the subset of *ids* that already exist in the vector store."""
    return vectorstore.existing_ids(ids)


def _delete_document_chunks(document_id: uuid.UUID) -> int:
    """Delete every stored chunk of a document. Returns how many were removed."""
    return vectorstore.delete_document(document_id)


def _delete_legacy_ids(document_id: uuid.UUID) -> int:
    """Remove chunks stored with the OLD id scheme ``<doc>:<n>``.

    New ids are ``<doc>:<pdf_page>:<k>`` (two colons). Old-scheme chunks were
    built by an earlier version of the chunker and would pollute search.
    """
    legacy = [i for i in vectorstore.document_chunk_ids(document_id) if i.count(":") == 1]
    vectorstore.delete_chunk_ids(legacy)
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
    """Index *chunks* for a single document in Qdrant.

    **Resumable**: ids are deterministic, so before embedding anything we ask
    the vector store which ids already exist and embed only the missing ones. A re-run
    after a failure never pays for the same chunk twice.

    ``force=True`` deletes the document's stored chunks first and re-embeds.
    ``track_status=False`` leaves ``Document.status`` alone (used when embedding
    extra tier-2 pages for a document that is already ``ready``).

    A daily-quota error from Gemini stops immediately, marks the document
    ``failed`` (when tracking status) and re-raises ``QuotaExhaustedError``.
    Chunks already written stay stored for the next run.

    Returns the PDF page numbers whose chunks are now all stored.
    """
    vectorstore.ensure_collection()

    if force:
        removed = _delete_document_chunks(document_id)
        logger.info("force: deleted %d stored chunks for %s", removed, document_id)
    else:
        legacy = _delete_legacy_ids(document_id)
        if legacy:
            logger.info("Removed %d old-format chunks for %s", legacy, document_id)

    total = len(chunks)
    all_ids = chunk_ids(document_id, chunks)
    done = _existing_ids(all_ids)
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
                    vectorstore.upsert(ids, texts, metas, vectors)
                    break
                except Exception as exc:  # network errors
                    attempt += 1
                    if attempt >= 3:
                        raise
                    logger.warning("Vector store write error (attempt %d): %s", attempt, exc)
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

def ping_vector_store() -> bool:
    """Return ``True`` if the vector database is reachable; otherwise ``False``.
    Used by the health endpoint.
    """
    return vectorstore.ping()

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

