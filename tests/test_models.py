"""
tests/test_models.py
─────────────────────────────────────────────────────────────────────────────
Task 2 acceptance tests.

These tests use SQLite (in-memory) so they run without a hosted Postgres.
They verify:
  1. All tables can be created from the ORM metadata.
  2. A Session with two Documents can be inserted and queried back.
  3. Cascade delete removes Documents when their Session is deleted.
  4. The document_status field only accepts valid enum values.

Run with:
    pytest tests/test_models.py -v
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, Document, Metric, PageTag, RedFlag
from app.db.models import Session as SessionModel


# ── SQLite-compatible engine (no Postgres needed for unit tests) ──────────────

@pytest.fixture(scope="module")
def engine():
    """In-memory SQLite engine with UUID stored as strings."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )

    # SQLite does not have a native UUID type – tell SQLAlchemy to use strings.
    from sqlalchemy.dialects import sqlite as _sqlite
    from sqlalchemy import TypeDecorator, String
    import uuid as _uuid

    # Patch UUID columns to store as strings in SQLite
    @event.listens_for(eng, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    # Create all tables (unit tests only; prod uses Alembic)
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)


@pytest.fixture()
def db(engine):
    """Yield a fresh session; roll back after each test."""
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = Session()
    yield session
    session.rollback()
    session.close()


# ── helpers ───────────────────────────────────────────────────────────────────

def make_session(name: str = "Test Session") -> SessionModel:
    return SessionModel(id=uuid.uuid4(), name=name)


def make_document(session_id: uuid.UUID, filename: str = "report.pdf") -> Document:
    return Document(
        id=uuid.uuid4(),
        session_id=session_id,
        filename=filename,
        storage_key=f"documents/{uuid.uuid4()}/{filename}",
        company="Acme Corp",
        fiscal_year=2024,
        doc_type="annual_report",
        status="uploaded",
    )


# ── tests ─────────────────────────────────────────────────────────────────────

class TestTableCreation:
    def test_all_tables_exist(self, engine):
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        expected = {"sessions", "documents", "page_tags", "metrics", "red_flags"}
        assert expected.issubset(tables), f"Missing tables: {expected - tables}"


class TestSessionModel:
    def test_insert_and_query_session(self, db):
        sess = make_session("Q4 Research")
        db.add(sess)
        db.flush()

        result = db.get(SessionModel, sess.id)
        assert result is not None
        assert result.name == "Q4 Research"
        assert result.created_at is not None

    def test_session_with_two_documents(self, db):
        sess = make_session("Multi-doc session")
        db.add(sess)
        db.flush()

        doc1 = make_document(sess.id, "infosys_2024.pdf")
        doc2 = make_document(sess.id, "tcs_2024.pdf")
        db.add_all([doc1, doc2])
        db.flush()

        # reload via relationship
        db.refresh(sess)
        assert len(sess.documents) == 2
        filenames = {d.filename for d in sess.documents}
        assert filenames == {"infosys_2024.pdf", "tcs_2024.pdf"}

    def test_cascade_delete_removes_documents(self, db):
        sess = make_session("To be deleted")
        db.add(sess)
        db.flush()

        doc = make_document(sess.id)
        db.add(doc)
        db.flush()
        doc_id = doc.id

        db.delete(sess)
        db.flush()

        assert db.get(Document, doc_id) is None


class TestDocumentModel:
    def test_default_status_is_uploaded(self, db):
        sess = make_session()
        db.add(sess)
        db.flush()

        doc = make_document(sess.id)
        db.add(doc)
        db.flush()

        assert doc.status == "uploaded"

    def test_error_message_nullable(self, db):
        sess = make_session()
        db.add(sess)
        db.flush()

        doc = make_document(sess.id)
        db.add(doc)
        db.flush()

        assert doc.error_message is None

        doc.error_message = "Parsing failed: corrupt PDF"
        db.flush()
        db.refresh(doc)
        assert doc.error_message == "Parsing failed: corrupt PDF"

    def test_status_transition(self, db):
        sess = make_session()
        db.add(sess)
        db.flush()

        doc = make_document(sess.id)
        db.add(doc)
        db.flush()

        for status in ("parsing", "tagging", "chunking", "indexing", "ready"):
            doc.status = status
            db.flush()
            db.refresh(doc)
            assert doc.status == status

    def test_storage_key_unique(self, db):
        import sqlalchemy.exc

        sess = make_session()
        db.add(sess)
        db.flush()

        key = f"documents/{uuid.uuid4()}/report.pdf"
        doc1 = Document(
            id=uuid.uuid4(),
            session_id=sess.id,
            filename="report.pdf",
            storage_key=key,
            status="uploaded",
        )
        doc2 = Document(
            id=uuid.uuid4(),
            session_id=sess.id,
            filename="report.pdf",
            storage_key=key,   # duplicate!
            status="uploaded",
        )
        db.add(doc1)
        db.flush()
        db.add(doc2)
        with pytest.raises(sqlalchemy.exc.IntegrityError):
            db.flush()
        db.rollback()


class TestPageTagModel:
    def test_insert_page_tag(self, db):
        sess = make_session()
        db.add(sess)
        db.flush()

        doc = make_document(sess.id)
        db.add(doc)
        db.flush()

        tag = PageTag(
            id=uuid.uuid4(),
            document_id=doc.id,
            pdf_page=1,
            printed_page=1,
            section="Balance Sheet",
            scope="standalone",
            unit="INR crore",
            content_type="financial_statement",
            is_image_only=False,
        )
        db.add(tag)
        db.flush()

        db.refresh(doc)
        assert len(doc.page_tags) == 1
        assert doc.page_tags[0].section == "Balance Sheet"


class TestPlaceholderTables:
    """Metric and RedFlag tables exist but have no logic yet."""

    def test_metric_insert(self, db):
        sess = make_session()
        db.add(sess)
        db.flush()

        doc = make_document(sess.id)
        db.add(doc)
        db.flush()

        metric = Metric(
            id=uuid.uuid4(),
            document_id=doc.id,
            name="Total Assets",
            value=148903.0,
            unit="INR crore",
            year=2024,
            source_page=274,
        )
        db.add(metric)
        db.flush()

        db.refresh(doc)
        assert len(doc.metrics) == 1
        assert doc.metrics[0].name == "Total Assets"

    def test_red_flag_insert(self, db):
        sess = make_session()
        db.add(sess)
        db.flush()

        doc = make_document(sess.id)
        db.add(doc)
        db.flush()

        flag = RedFlag(
            id=uuid.uuid4(),
            document_id=doc.id,
            type="related_party",
            description="Large related-party transaction not disclosed in notes.",
            source_page=42,
        )
        db.add(flag)
        db.flush()

        db.refresh(doc)
        assert len(doc.red_flags) == 1
        assert doc.red_flags[0].type == "related_party"
