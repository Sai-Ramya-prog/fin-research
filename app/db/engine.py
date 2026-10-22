"""
app/db/engine.py
─────────────────────────────────────────────────────────────────────────────
SQLAlchemy engine + session factory.

- pool_pre_ping=True  → stale connections from serverless Postgres are recycled
                         before use, avoiding "server closed the connection" errors.
- Small pool (max 5 + 2 overflow) → appropriate for cloud hosts that cap
  concurrent connections on free / starter tiers.
- Import `get_session` to open a database session in a FastAPI dependency or
  any other context.
"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings


def _build_engine():
    if not settings.database_url:
        raise RuntimeError(
            "DATABASE_URL is not set.  "
            "Add it to your .env file before importing app.db.engine."
        )
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,   # recycle stale connections (critical for hosted Postgres)
        pool_size=5,
        max_overflow=2,
        echo=False,           # set True temporarily to log SQL during debugging
    )


engine = _build_engine()

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,  # keep attributes readable after commit
)


def get_session() -> Generator[Session, None, None]:
    """
    FastAPI / general-purpose dependency that yields a DB session and
    commits on success or rolls back on any exception.

    Usage (FastAPI)::

        @router.get("/items")
        def list_items(db: Session = Depends(get_session)):
            ...
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def check_connection() -> bool:
    """Return True if the database is reachable, False otherwise."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
