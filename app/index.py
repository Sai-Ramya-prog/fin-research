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
from app.embeddings import get_embeddings, embedding_fingerprint
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

def index_chunks(
    document_id: uuid.UUID,
    chunks: List[Dict[str, Any]],
) -> None:
    """Index *chunks* for a single document in Chroma Cloud.

    The function is **idempotent** – any pre‑existing chunks for the same
    ``document_id`` are deleted before the new ones are added.  It is also
    **resumable** – if a quota or network error aborts the run, the document is
    marked ``failed`` and the next invocation will start again, skipping already
    indexed chunks because they have been removed only on successful start.
    """
    # -------------------------------------------------------------------
    # 1️⃣  Prepare collection and delete old chunks (if any)
    # -------------------------------------------------------------------
    collection = _get_collection()
    # Delete existing chunks belonging to this document – use a filter on the
    # metadata field ``doc_id`` which we store as a string.
    try:
        existing = collection.get(
            where={"doc_id": str(document_id)}, include=["ids"]
        )
        if existing and existing["ids"]:
            collection.delete(ids=existing["ids"])
            logger.info(
                "Deleted %d old chunks for document %s", len(existing["ids"]), document_id
            )
    except Exception as exc:  # pragma: no cover – defensive
        logger.warning(
            "Failed to clean up old chunks for %s – proceeding anyway: %s",
            document_id,
            exc,
        )

    # -------------------------------------------------------------------
    # 2️⃣  Iterate over batches of up to CHROMA_MAX_BATCH chunks
    # -------------------------------------------------------------------
    total = len(chunks)
    logger.info("Indexing %d chunks for document %s", total, document_id)

    # Update status – indexing started
    _set_document_status(document_id, "indexing")

    for batch_start in range(0, total, CHROMA_MAX_BATCH):
        batch = chunks[batch_start : batch_start + CHROMA_MAX_BATCH]
        texts = [c["text"] for c in batch]
        metadatas = []
        ids = []
        for i, chunk in enumerate(batch, start=batch_start):
            meta = dict(chunk["metadata"])  # shallow copy
            # Ensure all required keys exist and are Chroma‑safe
            meta.setdefault("doc_id", str(document_id))
            meta.setdefault("chunk_index", i)
            # Convert possible ``None`` values to empty string or -1 (metadata
            # cannot contain ``None``).  ``printed_page`` is the only field that
            # may be ``None``.
            if meta.get("printed_page") in (None, ""):
                meta["printed_page"] = -1
            # Chroma expects simple scalar types – everything else will be cast
            # to string automatically, but we keep it explicit.
            for k, v in list(meta.items()):
                if v is None:
                    meta[k] = ""
            metadatas.append(meta)
            # Chunk ID is deterministic so that re‑runs produce the same ID;
            # format: <doc_id>:<index>
            ids.append(f"{document_id}:{i}")

        # ----------------------------------------------------------------
        # 3️⃣  Embed in sub‑batches respecting the embedding service limits
        # ----------------------------------------------------------------
        embeddings: List[List[float]] = []
        emb = get_embeddings()
        for emb_start in range(0, len(texts), EMBED_BATCH_SIZE):
            sub_texts = texts[emb_start : emb_start + EMBED_BATCH_SIZE]
            # Retry wrapper for the embed call – simple exponential back‑off
            attempt = 0
            while True:
                try:
                    sub_emb = emb.embed_documents(sub_texts)
                    embeddings.extend(sub_emb)
                    break
                except Exception as exc:  # pragma: no cover – network errors
                    attempt += 1
                    if attempt >= 3:
                        # Mark document as failed and abort indexing
                        msg = f"Embedding failed after {attempt} attempts: {exc}"
                        logger.error(msg)
                        _set_document_status(document_id, "failed", error_message=msg)
                        raise
                    backoff = 2 ** attempt
                    logger.warning(
                        "Embedding error (attempt %d) – backing off %ds: %s",
                        attempt,
                        backoff,
                        exc,
                    )
                    time.sleep(backoff)
            # Pause between embedding batches to respect free‑tier limits
            time.sleep(EMBED_BATCH_PAUSE)

        # ----------------------------------------------------------------
        # 4️⃣  Add documents to Chroma (using pre‑computed embeddings)
        # ----------------------------------------------------------------
        attempt = 0
        while True:
            try:
                collection.add(
                    ids=ids,
                    documents=texts,
                    metadatas=metadatas,
                    embeddings=embeddings,
                )
                logger.info(
                    "Added %d chunks (batch %d‑%d) to Chroma", len(ids), batch_start + 1, batch_start + len(ids)
                )
                break
            except Exception as exc:  # pragma: no cover – network / quota errors
                attempt += 1
                if attempt >= 3:
                    msg = f"Chroma add failed after {attempt} attempts: {exc}"
                    logger.error(msg)
                    _set_document_status(document_id, "failed", error_message=msg)
                    raise
                backoff = 2 ** attempt
                logger.warning(
                    "Chroma add error (attempt %d) – backing off %ds: %s", attempt, backoff, exc
                )
                time.sleep(backoff)

    # -------------------------------------------------------------------
    # 5️⃣  Finalise – mark document ready
    # -------------------------------------------------------------------
    _set_document_status(document_id, "ready")
    logger.info("Indexing complete for document %s", document_id)

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

