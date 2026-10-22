"""
tests/test_seed.py
───────────────────────────────────────────────────────────────────────────────
Unit tests for app/seed.py (Task 11).

All external I/O is mocked:
  - DB → in-memory SQLite (same pattern as test_api.py)
  - storage.save / storage.exists / storage.exists → mocked
  - run_document_agent → mocked (no real pipeline)

Run with:
    pytest tests/test_seed.py -v
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Document, Session


# ─────────────────────────────────────────────────────────────────────────────
# In-memory SQLite engine (same pattern as test_api.py)
# ─────────────────────────────────────────────────────────────────────────────

def _make_sqlite_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture()
def db_engine():
    engine = _make_sqlite_engine()
    yield engine
    engine.dispose()


@pytest.fixture()
def db_session(db_engine):
    connection = db_engine.connect()
    transaction = connection.begin()
    TestSession = sessionmaker(
        bind=connection,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    session = TestSession()
    yield session
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture(autouse=True)
def _patch_env(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    monkeypatch.setenv("CHROMA_API_KEY", "key")
    monkeypatch.setenv("CHROMA_TENANT", "tenant")
    monkeypatch.setenv("CHROMA_DATABASE", "db")
    monkeypatch.setenv("CHROMA_COLLECTION", "col")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./test_seed.db")
    monkeypatch.setenv("GOOGLE_API_KEY", "dummy")
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("EMBEDDING_BATCH_PAUSE_SECONDS", "0")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _mock_session_local(db_session):
    """Return a callable that, when called like SessionLocal(), returns db_session."""
    return lambda: db_session


def _fake_pdf_path(tmp_path: Path, filename: str) -> Path:
    """Write a minimal fake PDF to tmp_path and return its path."""
    p = tmp_path / filename
    p.write_bytes(b"%PDF-1.4 fake content")
    return p


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestGetOrCreateSeedSession:
    def test_creates_session_when_missing(self, db_session):
        from app.seed import _get_or_create_seed_session, SEED_SESSION_NAME

        with patch("app.seed.SessionLocal", _mock_session_local(db_session)):
            sid = _get_or_create_seed_session()

        assert isinstance(sid, uuid.UUID)
        sess = db_session.query(Session).filter(Session.name == SEED_SESSION_NAME).first()
        assert sess is not None
        assert sess.id == sid

    def test_returns_existing_session_id(self, db_session):
        from app.seed import _get_or_create_seed_session, SEED_SESSION_NAME

        # Pre-create session
        existing = Session(name=SEED_SESSION_NAME)
        db_session.add(existing)
        db_session.flush()
        existing_id = existing.id
        db_session.commit()

        with patch("app.seed.SessionLocal", _mock_session_local(db_session)):
            sid = _get_or_create_seed_session()

        assert sid == existing_id
        # Should not have created a duplicate
        count = db_session.query(Session).filter(Session.name == SEED_SESSION_NAME).count()
        assert count == 1


class TestFindExistingDoc:
    def test_returns_none_when_missing(self, db_session):
        from app.seed import _find_existing_doc

        sid = uuid.uuid4()
        with patch("app.seed.SessionLocal", _mock_session_local(db_session)):
            result = _find_existing_doc(sid, "report.pdf")
        assert result is None

    def test_returns_doc_when_present(self, db_session):
        from app.seed import _find_existing_doc

        sess = Session(name="S")
        db_session.add(sess)
        db_session.flush()
        doc = Document(
            session_id=sess.id,
            filename="report.pdf",
            storage_key="documents/x/report.pdf",
            company="Infosys",
            fiscal_year=2025,
            status="ready",
        )
        db_session.add(doc)
        db_session.commit()

        with patch("app.seed.SessionLocal", _mock_session_local(db_session)):
            result = _find_existing_doc(sess.id, "report.pdf")

        assert result is not None
        assert result.status == "ready"


class TestCountReady:
    def test_zero_when_empty(self, db_session):
        from app.seed import _count_ready

        sid = uuid.uuid4()
        with patch("app.seed.SessionLocal", _mock_session_local(db_session)):
            assert _count_ready(sid) == 0

    def test_counts_only_ready(self, db_session):
        from app.seed import _count_ready

        sess = Session(name="S")
        db_session.add(sess)
        db_session.flush()

        for i, status in enumerate(("ready", "ready", "failed", "uploaded")):
            doc = Document(
                session_id=sess.id,
                filename=f"doc_{i}_{status}.pdf",
                storage_key=f"k/{i}/{status}.pdf",
                company="X",
                fiscal_year=2025,
                status=status,
            )
            db_session.add(doc)
        db_session.commit()

        with patch("app.seed.SessionLocal", _mock_session_local(db_session)):
            assert _count_ready(sess.id) == 2


class TestSeedOnce:
    """Integration-style tests for seed_once() with all I/O mocked."""

    def _patches(self, db_session, tmp_path: Path, *, files_exist=True):
        """Build the standard set of patches for seed_once tests."""
        # Place fake PDFs in tmp_path
        from app.seed import SEED_DOCUMENTS
        seed_dir = tmp_path / "data" / "seed"
        seed_dir.mkdir(parents=True)
        for filename, _, _ in SEED_DOCUMENTS:
            (seed_dir / filename).write_bytes(b"%PDF-1.4 fake")

        patches = [
            patch("app.seed.SessionLocal", _mock_session_local(db_session)),
            patch("app.seed._SEED_DIR", seed_dir),
            patch("app.seed.storage.exists", return_value=False),
            patch("app.seed.storage.save", return_value="key"),
            patch("app.seed.storage.make_storage_key", side_effect=lambda d, f: f"documents/{d}/{f}"),
            patch("app.seed.run_document_agent", return_value=None),
            patch("app.seed.time.sleep", return_value=None),
        ]
        return patches

    def test_creates_session_and_processes_all_docs(self, db_session, tmp_path):
        from app.seed import seed_once, SEED_DOCUMENTS, SEED_SESSION_NAME

        with (
            patch("app.seed.SessionLocal", _mock_session_local(db_session)),
            patch("app.seed._SEED_DIR", self._make_seed_dir(tmp_path)),
            patch("app.seed.storage.exists", return_value=False),
            patch("app.seed.storage.save", return_value="key"),
            patch("app.seed.storage.make_storage_key", side_effect=lambda d, f: f"docs/{d}/{f}"),
            patch("app.seed.run_document_agent") as mock_agent,
            patch("app.seed.time.sleep"),
        ):
            seed_once()

        # All 4 documents should have had the agent run
        assert mock_agent.call_count == len(SEED_DOCUMENTS)

        # Session was created
        sess = db_session.query(Session).filter(Session.name == SEED_SESSION_NAME).first()
        assert sess is not None

        # All 4 Document rows were created
        docs = db_session.query(Document).filter(Document.session_id == sess.id).all()
        assert len(docs) == len(SEED_DOCUMENTS)

    def test_skips_already_ready_documents(self, db_session, tmp_path):
        from app.seed import seed_once, SEED_SESSION_NAME, SEED_DOCUMENTS

        # Pre-populate session + 2 ready docs
        sess = Session(name=SEED_SESSION_NAME)
        db_session.add(sess)
        db_session.flush()

        ready_filenames = [SEED_DOCUMENTS[0][0], SEED_DOCUMENTS[1][0]]
        for fn in ready_filenames:
            doc = Document(
                session_id=sess.id,
                filename=fn,
                storage_key=f"documents/x/{fn}",
                company="X",
                fiscal_year=2025,
                status="ready",
            )
            db_session.add(doc)
        db_session.commit()

        with (
            patch("app.seed.SessionLocal", _mock_session_local(db_session)),
            patch("app.seed._SEED_DIR", self._make_seed_dir(tmp_path)),
            patch("app.seed.storage.exists", return_value=False),
            patch("app.seed.storage.save", return_value="key"),
            patch("app.seed.storage.make_storage_key", side_effect=lambda d, f: f"docs/{d}/{f}"),
            patch("app.seed.run_document_agent") as mock_agent,
            patch("app.seed.time.sleep"),
        ):
            seed_once()

        # Agent should only run for the 2 non-ready documents
        assert mock_agent.call_count == 2

    def test_skips_storage_upload_if_key_exists(self, db_session, tmp_path):
        from app.seed import seed_once

        mock_save = MagicMock()
        with (
            patch("app.seed.SessionLocal", _mock_session_local(db_session)),
            patch("app.seed._SEED_DIR", self._make_seed_dir(tmp_path)),
            patch("app.seed.storage.exists", return_value=True),   # already uploaded
            patch("app.seed.storage.save", mock_save),
            patch("app.seed.storage.make_storage_key", side_effect=lambda d, f: f"docs/{d}/{f}"),
            patch("app.seed.run_document_agent"),
            patch("app.seed.time.sleep"),
        ):
            seed_once()

        # save should NOT have been called
        mock_save.assert_not_called()

    def test_continues_after_agent_exception(self, db_session, tmp_path):
        """seed_once must not abort if the agent raises for one document."""
        from app.seed import seed_once, SEED_DOCUMENTS

        def _agent_that_fails_once(doc_id):
            _agent_that_fails_once.call_count += 1
            if _agent_that_fails_once.call_count == 1:
                raise RuntimeError("simulated failure")

        _agent_that_fails_once.call_count = 0

        with (
            patch("app.seed.SessionLocal", _mock_session_local(db_session)),
            patch("app.seed._SEED_DIR", self._make_seed_dir(tmp_path)),
            patch("app.seed.storage.exists", return_value=False),
            patch("app.seed.storage.save", return_value="key"),
            patch("app.seed.storage.make_storage_key", side_effect=lambda d, f: f"docs/{d}/{f}"),
            patch("app.seed.run_document_agent", side_effect=_agent_that_fails_once),
            patch("app.seed.time.sleep"),
        ):
            seed_once()  # must not raise

        # All 4 documents should still have been attempted
        assert _agent_that_fails_once.call_count == len(SEED_DOCUMENTS)

    def test_early_exit_when_all_ready(self, db_session, tmp_path):
        """seed_once() should skip all processing if all docs already ready."""
        from app.seed import seed_once, SEED_SESSION_NAME, SEED_DOCUMENTS

        # Pre-populate session + 4 ready docs
        sess = Session(name=SEED_SESSION_NAME)
        db_session.add(sess)
        db_session.flush()
        for fn, company, fy in SEED_DOCUMENTS:
            doc = Document(
                session_id=sess.id,
                filename=fn,
                storage_key=f"documents/x/{fn}",
                company=company,
                fiscal_year=fy,
                status="ready",
            )
            db_session.add(doc)
        db_session.commit()

        mock_agent = MagicMock()
        with (
            patch("app.seed.SessionLocal", _mock_session_local(db_session)),
            patch("app.seed.run_document_agent", mock_agent),
            patch("app.seed.time.sleep"),
        ):
            seed_once()

        mock_agent.assert_not_called()

    # ── helper ────────────────────────────────────────────────────────────────

    def _make_seed_dir(self, tmp_path: Path) -> Path:
        from app.seed import SEED_DOCUMENTS
        seed_dir = tmp_path / "data" / "seed"
        seed_dir.mkdir(parents=True, exist_ok=True)
        for filename, _, _ in SEED_DOCUMENTS:
            (seed_dir / filename).write_bytes(b"%PDF-1.4 fake content")
        return seed_dir
