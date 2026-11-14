"""
scripts/chroma_to_qdrant.py
─────────────────────────────────────────────────────────────────────────────
Copy every stored chunk (id, vector, text, metadata) from Chroma Cloud to
Qdrant WITHOUT re-embedding anything - so it costs no embedding quota.

Run from the backend/ folder (where your .env lives):

    pip install -r scripts/requirements-migrate.txt      # chromadb, once
    python -m scripts.chroma_to_qdrant                   # export + import + verify

Steps
─────
  1. EXPORT   Read all records from Chroma Cloud into a local file
              (chroma_backup.jsonl). Keep this file: it is your safety copy
              once Chroma Cloud is switched off.
  2. IMPORT   Upsert the file into the Qdrant collection from QDRANT_* in .env.
              Safe to re-run: same chunk id = overwritten, never duplicated.
  3. VERIFY   Every exported chunk must be present in Qdrant, and searching with
              a stored vector must return that same chunk first.

Options
───────
  --backup PATH    where to write/read the backup (default chroma_backup.jsonl)
  --skip-export    do not touch Chroma; import from an existing backup file
  --skip-import    only export (and stop)

Chroma settings (read from backend/.env or the environment; the app itself no
longer needs them):

  CHROMA_API_KEY, CHROMA_TENANT, CHROMA_DATABASE, CHROMA_COLLECTION (default
  financial_docs)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterator, List

from app import vectorstore

logger = logging.getLogger("chroma_to_qdrant")

PAGE = 300          # Chroma Cloud returns at most this many records per call
BATCH = 200         # chunks per Qdrant write


# ── 1. export ─────────────────────────────────────────────────────────────────

def export_from_chroma(collection: Any, backup: Path) -> int:
    """Write every record of a Chroma collection to *backup* (JSON lines).

    Returns the number of records written. Raises if Chroma's own count and the
    number actually read disagree (nothing is silently dropped).
    """
    total = collection.count()
    written = 0
    with backup.open("w", encoding="utf-8") as f:
        for offset in range(0, total, PAGE):
            res = collection.get(
                limit=PAGE, offset=offset,
                include=["embeddings", "documents", "metadatas"],
            )
            for cid, vec, text, meta in zip(
                res["ids"], res["embeddings"], res["documents"], res["metadatas"]
            ):
                f.write(json.dumps({
                    "id": cid,
                    "vec": [float(x) for x in vec],
                    "text": text or "",
                    "meta": meta or {},
                }) + "\n")
                written += 1
    if written != total:
        raise RuntimeError(f"Chroma reported {total} records but only {written} were read.")
    logger.info("Exported %d records to %s", written, backup)
    return written


# ── 2. import ─────────────────────────────────────────────────────────────────

def _read_backup(backup: Path) -> Iterator[Dict[str, Any]]:
    with backup.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def import_to_qdrant(backup: Path) -> int:
    """Upsert the backup file into Qdrant. Returns the number of chunks sent."""
    vectorstore.ensure_collection()
    expected = vectorstore._vector_size()
    sent = 0
    batch: List[Dict[str, Any]] = []

    def flush() -> None:
        nonlocal sent
        if not batch:
            return
        vectorstore.upsert(
            [r["id"] for r in batch],
            [r["text"] for r in batch],
            [r["meta"] for r in batch],
            [r["vec"] for r in batch],
        )
        sent += len(batch)
        logger.info("Imported %d chunks", sent)
        batch.clear()

    for rec in _read_backup(backup):
        if len(rec["vec"]) != expected:
            raise RuntimeError(
                f"Chunk {rec['id']} has a {len(rec['vec'])}-dim vector but the app's embedding "
                f"model makes {expected}-dim vectors. Check EMBEDDING_PROVIDER / EMBEDDING_MODEL in .env."
            )
        batch.append(rec)
        if len(batch) >= BATCH:
            flush()
    flush()
    return sent


# ── 3. verify ─────────────────────────────────────────────────────────────────

def verify(backup: Path) -> None:
    """Raise unless every chunk in the backup is in Qdrant and search works."""
    ids = [r["id"] for r in _read_backup(backup)]
    present = vectorstore.existing_ids(ids)
    missing = [i for i in ids if i not in present]
    if missing:
        raise RuntimeError(f"{len(missing)} chunks are missing in Qdrant, e.g. {missing[:3]}")
    logger.info("Verified: all %d chunks are in Qdrant", len(ids))

    if not ids:
        return
    first = next(_read_backup(backup))
    top = vectorstore.search(first["vec"], limit=1)
    if not top or top[0].payload.get("chunk_id") != first["id"]:
        raise RuntimeError("Search check failed: a stored vector did not return its own chunk first.")
    logger.info("Verified: search with a stored vector returns its own chunk (score %.4f)", top[0].score)


# ── CLI ───────────────────────────────────────────────────────────────────────

def _chroma_collection() -> Any:
    """Connect to Chroma Cloud using CHROMA_* from backend/.env or the environment."""
    try:
        import chromadb
    except ImportError:
        sys.exit("chromadb is not installed. Run:  pip install -r scripts/requirements-migrate.txt")
    from dotenv import dotenv_values

    env = {**dotenv_values(".env"), **os.environ}
    missing = [k for k in ("CHROMA_API_KEY", "CHROMA_TENANT", "CHROMA_DATABASE") if not env.get(k)]
    if missing:
        sys.exit(f"Missing {', '.join(missing)} - put them in backend/.env for the export step.")
    client = chromadb.CloudClient(
        tenant=env["CHROMA_TENANT"], database=env["CHROMA_DATABASE"], api_key=env["CHROMA_API_KEY"],
    )
    return client.get_collection(env.get("CHROMA_COLLECTION") or "financial_docs")


def main(argv: List[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Copy chunks from Chroma Cloud to Qdrant (no re-embedding).")
    ap.add_argument("--backup", default="chroma_backup.jsonl")
    ap.add_argument("--skip-export", action="store_true")
    ap.add_argument("--skip-import", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s")
    backup = Path(args.backup)

    if not args.skip_export:
        export_from_chroma(_chroma_collection(), backup)
    elif not backup.exists():
        sys.exit(f"{backup} not found.")
    if args.skip_import:
        return
    import_to_qdrant(backup)
    verify(backup)
    logger.info("Done. Qdrant collection '%s' is ready.", vectorstore.settings.qdrant_collection)


if __name__ == "__main__":
    main()
