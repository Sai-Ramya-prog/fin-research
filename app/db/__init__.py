"""
app/db/__init__.py
─────────────────────────────────────────────────────────────────────────────
Convenience re-exports so callers can do:

    from app.db import Base, Session, Document, get_session, engine
"""

from app.db.engine import SessionLocal, check_connection, engine, get_session
from app.db.models import Base, Document, Metric, PageTag, RedFlag
from app.db.models import Session as SessionModel  # avoid clash with SQLAlchemy Session

__all__ = [
    "Base",
    "Document",
    "Metric",
    "PageTag",
    "RedFlag",
    "SessionModel",
    "SessionLocal",
    "engine",
    "get_session",
    "check_connection",
]
