"""
scripts/normalize_companies.py
─────────────────────────────────────────────────────────────────────────────
One-time fix: make every stored company name upper-case ("Infosys" -> "INFOSYS")
so old documents match new ones.

    cd backend
    python -m scripts.normalize_companies --dry-run      # show what would change
    python -m scripts.normalize_companies                # do it

It updates the Postgres rows and the ``company`` field of every chunk already
stored in Qdrant. Nothing is re-embedded, so it costs no embedding quota.
Safe to run again.
"""

from __future__ import annotations

import argparse
import logging

from app import vectorstore
from app.company import normalize_company
from app.db.engine import session_scope
from app.db.models import Document

logger = logging.getLogger("normalize_companies")


def normalize_all(dry_run: bool = False) -> int:
    """Returns the number of documents whose name was (or would be) changed."""
    changed = 0
    with session_scope() as db:
        for doc in db.query(Document).all():
            new = normalize_company(doc.company)
            if new == doc.company:
                continue
            changed += 1
            logger.info("%s: %r -> %r", doc.id, doc.company, new)
            if dry_run:
                continue
            doc.company = new
            if new:
                chunks = vectorstore.set_company(doc.id, new)
                logger.info("  updated %d vector chunks", chunks)
    return changed


def main() -> None:
    ap = argparse.ArgumentParser(description="Upper-case all stored company names.")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    n = normalize_all(dry_run=args.dry_run)
    logger.info("%d document(s) %s.", n, "would change" if args.dry_run else "updated")


if __name__ == "__main__":
    main()
