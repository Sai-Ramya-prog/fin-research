"""
app/seed.py
───────────────────────────────────────────────────────────────────────────────
Seed loader for the 4 pre-loaded annual-report PDFs (Task 11).

What it does
------------
1. Ensures a session called "Seed Companies" exists in the database (creates
   it once; idempotent on repeated runs).
2. For each of the 4 seed PDFs in ``data/seed/``:
   a. Skips if a Document row for that file already has ``status="ready"``.
   b. Skips the storage upload if the storage key already exists in the bucket
      (re-use the key if a previous run uploaded the file but the pipeline
      failed before completing).
   c. Creates (or re-uses) the Document DB row.
   d. Runs the Document Agent pipeline synchronously (parse → tag → chunk →
      index) and waits for it to finish before starting the next document, so
      Gemini free-tier rate limits are never exceeded.
3. Logs embedding-call counts per document (estimated from chunk count and
   batch size).

Usage
-----
As a CLI command (recommended after ``alembic upgrade head``)::

    python -m app.seed

Automatic on first startup – the FastAPI lifespan hook calls ``seed_once()``
in a background thread so it doesn't block the HTTP server from accepting
requests.  The seeding runs only when the "Seed Companies" session has fewer
than 4 ``ready`` documents.

Environment variables
---------------------
All credentials are read from ``.env`` via ``app.config.settings``.
``STORAGE_BACKEND=local`` is NOT suitable for production seeding (files are
written to a temp dir that disappears on restart).  Use ``remote`` with a
real S3-compatible bucket.

Seed catalogue
--------------
The four PDFs in ``data/seed/`` map to::

    infosys_fy2025.pdf.pdf     → company="Infosys"       fiscal_year=2025
    mahindra_fy2025.pdf.pdf    → company="M&M"           fiscal_year=2025
    reliance_fy2025.pdf.pdf    → company="Reliance"      fiscal_year=2025
    tata_motors_fy2025.pdf.pdf → company="Tata Motors"   fiscal_year=2025
"""

from __future__ import annotations

import logging
import math
import sys
import time
import uuid
from pathlib import Path

from app.agents.document_agent.graph import run_document_agent
from app.config import settings
from app import storage
from app.db.engine import SessionLocal
from app.db.models import Document, Session

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Seed catalogue
# Each entry: (filename_in_data_seed, company_name, fiscal_year_int)
# ─────────────────────────────────────────────────────────────────────────────

_SEED_DIR = Path(__file__).parent.parent / "data" / "seed"

SEED_DOCUMENTS: list[tuple[str, str, int]] = [
    ("infosys_fy2025.pdf.pdf",     "Infosys",     2025),
    ("mahindra_fy2025.pdf.pdf",    "M&M",         2025),
    ("reliance_fy2025.pdf.pdf",    "Reliance",    2025),
    ("tata_motors_fy2025.pdf.pdf", "Tata Motors", 2025),
]

SEED_SESSION_NAME = "Seed Companies"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_or_create_seed_session() -> uuid.UUID:
    """Return the UUID of the 'Seed Companies' session, creating it if needed."""
    db = SessionLocal()
    try:
        existing = (
            db.query(Session)
            .filter(Session.name == SEED_SESSION_NAME)
            .first()
        )
        if existing:
            logger.info("seed: using existing session id=%s", existing.id)
            return existing.id

        sess = Session(name=SEED_SESSION_NAME)
        db.add(sess)
        db.flush()
        session_id = sess.id
        db.commit()
        logger.info("seed: created session '%s' id=%s", SEED_SESSION_NAME, session_id)
        return session_id
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _find_existing_doc(session_id: uuid.UUID, filename: str) -> Document | None:
    """Return an existing Document row for this (session, filename), or None."""
    db = SessionLocal()
    try:
        doc = (
            db.query(Document)
            .filter(
                Document.session_id == session_id,
                Document.filename == filename,
            )
            .first()
        )
        if doc is not None:
            db.expunge(doc)
        return doc
    finally:
        db.close()


def _count_ready(session_id: uuid.UUID) -> int:
    """Count how many documents in the session have status='ready'."""
    db = SessionLocal()
    try:
        return (
            db.query(Document)
            .filter(
                Document.session_id == session_id,
                Document.status == "ready",
            )
            .count()
        )
    finally:
        db.close()


def _create_doc_row(
    session_id: uuid.UUID,
    doc_id: uuid.UUID,
    filename: str,
    storage_key: str,
    company: str,
    fiscal_year: int,
) -> None:
    """Insert a new Document row with status='uploaded'."""
    db = SessionLocal()
    try:
        doc = Document(
            id=doc_id,
            session_id=session_id,
            filename=filename,
            storage_key=storage_key,
            company=company,
            fiscal_year=fiscal_year,
            status="uploaded",
        )
        db.add(doc)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _estimated_embedding_calls(doc_id: uuid.UUID) -> int | str:
    """
    Estimate the number of embedding API calls made for a document.

    We approximate: calls = ceil(page_count / EMBEDDING_BATCH_SIZE).
    This is a rough proxy – narrative pages are split into multiple chunks,
    so real call count is slightly higher.  Returns "unknown" if page_count
    is not yet set.
    """
    db = SessionLocal()
    try:
        doc = db.get(Document, doc_id)
        if doc is None or doc.page_count is None:
            return "unknown"
        chunk_estimate = max(1, doc.page_count)
        batch_size = settings.embedding_batch_size or 32
        return math.ceil(chunk_estimate / batch_size)
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# Core seeding logic
# ─────────────────────────────────────────────────────────────────────────────

def seed_once() -> None:
    """
    Ensure all 4 seed documents are uploaded and processed.

    Safe to call multiple times – already-ready documents are skipped.
    Documents are processed one at a time so Gemini free-tier limits are
    respected.
    """
    logger.info("seed: starting seed_once()")

    session_id = _get_or_create_seed_session()
    ready_before = _count_ready(session_id)
    logger.info(
        "seed: session=%s  ready_docs_before=%d / %d",
        session_id,
        ready_before,
        len(SEED_DOCUMENTS),
    )

    if ready_before >= len(SEED_DOCUMENTS):
        logger.info(
            "seed: all %d documents already ready – nothing to do.",
            len(SEED_DOCUMENTS),
        )
        return

    for filename, company, fiscal_year in SEED_DOCUMENTS:
        pdf_path = _SEED_DIR / filename
        if not pdf_path.exists():
            logger.warning(
                "seed: PDF not found at %s – skipping %s.", pdf_path, filename
            )
            continue

        # ── Check if this document is already ready ──────────────────────────
        existing_doc = _find_existing_doc(session_id, filename)
        if existing_doc is not None and existing_doc.status == "ready":
            logger.info(
                "seed: %s (id=%s) already ready – skipping.",
                filename,
                existing_doc.id,
            )
            continue

        logger.info(
            "seed: processing %s  company=%s  fy=%d",
            filename,
            company,
            fiscal_year,
        )

        # ── Determine document ID (re-use existing or create new) ────────────
        if existing_doc is not None:
            doc_id = existing_doc.id
            storage_key = existing_doc.storage_key
            logger.info(
                "seed: re-using existing doc row id=%s  status=%s",
                doc_id,
                existing_doc.status,
            )
        else:
            doc_id = uuid.uuid4()
            storage_key = storage.make_storage_key(doc_id, filename)

        # ── Upload to storage (skip if already there) ────────────────────────
        if not storage.exists(storage_key):
            logger.info(
                "seed: uploading %s  key=%s  size=%.1f MB",
                filename,
                storage_key,
                pdf_path.stat().st_size / (1024 * 1024),
            )
            t_upload = time.perf_counter()
            with open(pdf_path, "rb") as f:
                storage.save(f, storage_key)
            logger.info(
                "seed: upload complete in %.1fs", time.perf_counter() - t_upload
            )
        else:
            logger.info(
                "seed: key %s already in storage – skipping upload.", storage_key
            )

        # ── Create DB row if not present ─────────────────────────────────────
        if existing_doc is None:
            _create_doc_row(
                session_id=session_id,
                doc_id=doc_id,
                filename=filename,
                storage_key=storage_key,
                company=company,
                fiscal_year=fiscal_year,
            )
            logger.info("seed: created Document row id=%s", doc_id)

        # ── Run Document Agent pipeline (synchronous, one at a time) ─────────
        logger.info("seed: running Document Agent for doc=%s", doc_id)
        t_agent = time.perf_counter()
        try:
            run_document_agent(doc_id)
        except Exception as exc:
            logger.exception(
                "seed: unhandled exception in Document Agent for doc=%s: %s",
                doc_id,
                exc,
            )
            # Continue to the next document; don't abort the whole seed run.
        else:
            elapsed_agent = time.perf_counter() - t_agent
            est_calls = _estimated_embedding_calls(doc_id)
            logger.info(
                "seed: doc=%s  company=%s  agent_elapsed=%.1fs  "
                "estimated_embedding_calls=%s",
                doc_id,
                company,
                elapsed_agent,
                est_calls,
            )

        # ── Pause between documents (respect Gemini free-tier daily limit) ───
        pause = settings.embedding_batch_pause_seconds
        logger.info(
            "seed: pausing %.1fs before next document (EMBEDDING_BATCH_PAUSE_SECONDS).",
            pause,
        )
        time.sleep(pause)

    # ── Final summary ────────────────────────────────────────────────────────
    ready_after = _count_ready(session_id)
    logger.info(
        "seed: finished.  ready=%d / %d  session=%s",
        ready_after,
        len(SEED_DOCUMENTS),
        session_id,
    )


# ─────────────────────────────────────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    """CLI entry point: ``python -m app.seed``."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        stream=sys.stdout,
    )
    logger.info("seed: invoked from CLI")
    seed_once()
    logger.info("seed: CLI run complete.")


if __name__ == "__main__":
    main()
