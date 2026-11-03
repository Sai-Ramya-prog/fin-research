"""
tests/test_api.py
───────────────────────────────────────────────────────────────────────────────
Integration tests for the FastAPI app (Task 10).

Uses ``httpx`` (async ASGI test client via ``httpx.AsyncClient``) so we test
the full request-response cycle without running a real server.

All external I/O is mocked:
  - DB calls via a fresh in-memory SQLite engine (override ``get_session``).
  - ``storage.save``, ``storage.delete``, ``storage.exists`` mocked.
  - ``run_pipeline`` mocked to avoid real pipeline runs.
  - ``ping_chroma`` and ``check_connection`` mocked for ``/health``.

Run with:
    pytest tests/test_api.py -v
"""

from __future__ import annotations

import io
import uuid
from typing import Generator
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base


# ─────────────────────────────────────────────────────────────────────────────
# In-memory SQLite engine (per test, fresh tables)
# ─────────────────────────────────────────────────────────────────────────────

from sqlalchemy.pool import StaticPool


# ─────────────────────────────────────────────────────────────────────────────
# In-memory SQLite engine (per test, fresh tables)
# ─────────────────────────────────────────────────────────────────────────────

def _make_sqlite_engine():
    """
    Use StaticPool so all connections within the test share the same
    underlying SQLite in-memory connection. Without this, each new
    connection gets a fresh empty database and tables created by
    create_all() are invisible to the session.
    """
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
    """
    Yield a session bound to the same engine connection used by create_all.
    Rolls back after each test for isolation.
    """
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


# ─────────────────────────────────────────────────────────────────────────────
# Test client with mocked dependencies
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def client(db_session):
    """
    FastAPI TestClient with:
      - DB overridden to in-memory SQLite.
      - storage.save / storage.delete mocked (no real S3).
      - run_pipeline mocked (no real pipeline).
      - ping_chroma and check_connection mocked.
    """
    from app.api.main import app
    from app.api.routes_sessions import _db as sessions_db
    from app.api.routes_documents import _db as documents_db

    def override_db():
        yield db_session

    app.dependency_overrides[sessions_db] = override_db
    app.dependency_overrides[documents_db] = override_db

    minimal_pdf = b"%PDF-1.4 fake content"

    with (
        patch("app.api.routes_sessions.storage.save", return_value=None),
        patch("app.api.routes_documents.storage.delete", return_value=None),
        patch("app.api.routes_sessions.run_pipeline", return_value=None),
        patch("app.api.routes_documents._delete_chroma_chunks", return_value=None),
        patch("app.api.main.ping_chroma", return_value=True),
        patch("app.api.main.check_connection", return_value=True),
    ):
        with TestClient(app) as c:
            yield c

    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _patch_env(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    monkeypatch.setenv("CHROMA_API_KEY", "key")
    monkeypatch.setenv("CHROMA_TENANT", "tenant")
    monkeypatch.setenv("CHROMA_DATABASE", "db")
    monkeypatch.setenv("CHROMA_COLLECTION", "col")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./test_api.db")
    monkeypatch.setenv("GOOGLE_API_KEY", "dummy")
    monkeypatch.setenv("STORAGE_BACKEND", "local")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

MINIMAL_PDF = b"%PDF-1.4 minimal pdf content for testing purposes only"


def _create_session(client, name="Test Session") -> dict:
    r = client.post("/sessions", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()


def _upload_doc(client, session_id: str, company="Infosys", fy="FY2024") -> dict:
    r = client.post(
        f"/sessions/{session_id}/documents",
        data={"company": company, "fiscal_year": fy},
        files={"file": ("report.pdf", io.BytesIO(MINIMAL_PDF), "application/pdf")},
    )
    assert r.status_code == 202, r.text
    return r.json()


# ─────────────────────────────────────────────────────────────────────────────
# Health
# ─────────────────────────────────────────────────────────────────────────────

class TestHealth:
    def test_health_ok(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["database"] is True
        assert body["chroma"] is True

    def test_health_chroma_down(self, client):
        with patch("app.api.main.ping_chroma", return_value=False):
            r = client.get("/health")
        body = r.json()
        assert body["status"] == "ok"
        assert body["chroma"] is False


# ─────────────────────────────────────────────────────────────────────────────
# Sessions CRUD
# ─────────────────────────────────────────────────────────────────────────────

class TestSessionsCRUD:
    def test_create_session(self, client):
        r = client.post("/sessions", json={"name": "My Session"})
        assert r.status_code == 201
        body = r.json()
        assert body["name"] == "My Session"
        assert "id" in body
        assert "created_at" in body

    def test_create_session_empty_name_rejected(self, client):
        r = client.post("/sessions", json={"name": ""})
        assert r.status_code == 422

    def test_list_sessions_empty(self, client):
        r = client.get("/sessions")
        assert r.status_code == 200
        assert r.json() == []

    def test_list_sessions_returns_all(self, client):
        _create_session(client, "Session A")
        _create_session(client, "Session B")
        r = client.get("/sessions")
        assert r.status_code == 200
        assert len(r.json()) == 2
        assert all(item["document_count"] == 0 for item in r.json())

    def test_list_documents_endpoint(self, client):
        s = _create_session(client)
        _upload_doc(client, s["id"], company="Apple")
        r = client.get("/documents")
        assert r.status_code == 200
        body = r.json()
        assert body[0]["company"] == "Apple" and "extraction_status" in body[0]

    def test_get_session_detail(self, client):
        s = _create_session(client)
        r = client.get(f"/sessions/{s['id']}")
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == s["id"]
        assert body["documents"] == []

    def test_get_session_not_found(self, client):
        r = client.get(f"/sessions/{uuid.uuid4()}")
        assert r.status_code == 404

    def test_delete_session(self, client):
        s = _create_session(client)
        r = client.delete(f"/sessions/{s['id']}")
        assert r.status_code == 204
        r2 = client.get(f"/sessions/{s['id']}")
        assert r2.status_code == 404

    def test_delete_session_not_found(self, client):
        r = client.delete(f"/sessions/{uuid.uuid4()}")
        assert r.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# Document upload
# ─────────────────────────────────────────────────────────────────────────────

class TestDocumentUpload:
    def test_upload_pdf_accepted(self, client):
        s = _create_session(client)
        r = client.post(
            f"/sessions/{s['id']}/documents",
            data={"company": "Infosys", "fiscal_year": "FY2024"},
            files={"file": ("report.pdf", io.BytesIO(MINIMAL_PDF), "application/pdf")},
        )
        assert r.status_code == 202
        body = r.json()
        assert body["status"] == "uploaded"
        assert body["company"] == "Infosys"
        assert body["filename"] == "report.pdf"

    def test_upload_non_pdf_rejected_by_extension(self, client):
        s = _create_session(client)
        r = client.post(
            f"/sessions/{s['id']}/documents",
            data={"company": "Infosys", "fiscal_year": "FY2024"},
            files={"file": ("report.txt", io.BytesIO(b"plain text"), "text/plain")},
        )
        assert r.status_code == 422

    def test_upload_fake_pdf_rejected_by_magic_bytes(self, client):
        s = _create_session(client)
        r = client.post(
            f"/sessions/{s['id']}/documents",
            data={"company": "Infosys", "fiscal_year": "FY2024"},
            files={"file": ("report.pdf", io.BytesIO(b"not a pdf"), "application/pdf")},
        )
        assert r.status_code == 422

    def test_upload_to_nonexistent_session(self, client):
        r = client.post(
            f"/sessions/{uuid.uuid4()}/documents",
            data={"company": "Infosys", "fiscal_year": "FY2024"},
            files={"file": ("report.pdf", io.BytesIO(MINIMAL_PDF), "application/pdf")},
        )
        assert r.status_code == 404

    def test_document_appears_in_session_detail(self, client):
        s = _create_session(client)
        _upload_doc(client, s["id"])
        r = client.get(f"/sessions/{s['id']}")
        assert r.status_code == 200
        assert len(r.json()["documents"]) == 1

    def test_fiscal_year_parsed_correctly(self, client):
        s = _create_session(client)
        d = _upload_doc(client, s["id"], fy="FY2024-25")
        assert d["fiscal_year"] == 2024

    def test_background_agent_scheduled(self, client):
        """run_pipeline must be called after upload."""
        s = _create_session(client)
        with patch(
            "app.api.routes_sessions.run_pipeline"
        ) as mock_agent:
            _upload_doc(client, s["id"])
        # Background tasks in TestClient run synchronously
        mock_agent.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# Document status & delete
# ─────────────────────────────────────────────────────────────────────────────

class TestDocumentLifecycle:
    def test_get_document_status(self, client):
        s = _create_session(client)
        d = _upload_doc(client, s["id"])
        r = client.get(f"/documents/{d['id']}")
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == d["id"]
        assert body["status"] == "uploaded"

    def test_get_document_not_found(self, client):
        r = client.get(f"/documents/{uuid.uuid4()}")
        assert r.status_code == 404

    def test_delete_document(self, client):
        s = _create_session(client)
        d = _upload_doc(client, s["id"])
        r = client.delete(f"/documents/{d['id']}")
        assert r.status_code == 204
        r2 = client.get(f"/documents/{d['id']}")
        assert r2.status_code == 404

    def test_delete_document_not_found(self, client):
        r = client.delete(f"/documents/{uuid.uuid4()}")
        assert r.status_code == 404

    def test_delete_removes_from_session(self, client):
        s = _create_session(client)
        d = _upload_doc(client, s["id"])
        client.delete(f"/documents/{d['id']}")
        r = client.get(f"/sessions/{s['id']}")
        assert r.json()["documents"] == []

    def test_delete_calls_chroma_cleanup(self, client):
        """Chroma chunk deletion must be attempted on document delete."""
        s = _create_session(client)
        d = _upload_doc(client, s["id"])
        with patch(
            "app.api.routes_documents._delete_chroma_chunks"
        ) as mock_chroma:
            client.delete(f"/documents/{d['id']}")
        mock_chroma.assert_called_once_with(uuid.UUID(d["id"]))


# ─────────────────────────────────────────────────────────────────────────────
# Page map, source page image, validation
# ─────────────────────────────────────────────────────────────────────────────

class TestDocumentViews:
    def _doc_with_data(self, client, db_session):
        from app.db.models import Document, Metric, PageTag

        s = _create_session(client)
        d = _upload_doc(client, s["id"], company="Apple")
        doc = db_session.get(Document, uuid.UUID(d["id"]))
        doc.fiscal_year = 2025
        for n, (sec, tier) in enumerate([("other", 2), ("profit_loss", 1), ("balance_sheet", 1)], start=1):
            db_session.add(PageTag(document_id=doc.id, pdf_page=n, section=sec, tier=tier,
                                   indexed=tier == 1, is_image_only=False))
        db_session.add_all([
            Metric(document_id=doc.id, name="revenue", year=2025, value=416161, unit="$ in million",
                   kind="extracted", status="verified", source_page=2, source_text="Total net sales 416,161"),
            Metric(document_id=doc.id, name="cash", year=2025, value=35934, unit="$ in million",
                   kind="extracted", status="needs_review", source_page=3, note="not found on page"),
            Metric(document_id=doc.id, name="net_margin_pct", year=2025, value=26.9, unit="%",
                   kind="ratio", status="verified"),
        ])
        db_session.commit()
        return d["id"]

    def test_page_map(self, client, db_session):
        doc_id = self._doc_with_data(client, db_session)
        body = client.get(f"/documents/{doc_id}/pages").json()
        assert body["page_count"] == 3 and body["tier1"] == 2 and body["embedded"] == 2
        assert [p["section"] for p in body["pages"]] == ["other", "profit_loss", "balance_sheet"]
        assert {s["section"] for s in body["sections"]} == {"other", "profit_loss", "balance_sheet"}

    def test_page_count_filled_for_old_documents(self, client, db_session):
        doc_id = self._doc_with_data(client, db_session)
        assert client.get(f"/documents/{doc_id}").json()["page_count"] == 3

    def test_validation(self, client, db_session):
        doc_id = self._doc_with_data(client, db_session)
        body = client.get(f"/documents/{doc_id}/validation").json()
        assert body["extracted_total"] == 2 and body["verified"] == 1 and body["needs_review"] == 1
        assert body["ratios_total"] == 1
        assert body["missing_statements"] == ["cash_flow"]
        assert body["review_items"][0]["name"] == "cash"

    def test_page_image_renders_and_highlights(self, client, db_session, tmp_path):
        import shutil
        from pathlib import Path

        doc_id = self._doc_with_data(client, db_session)
        src = Path(__file__).parent / "data" / "apple_fy2025_extract.pdf"

        def fake_download(key):
            dest = tmp_path / "dl.pdf"
            shutil.copy(src, dest)
            return str(dest)

        with patch("app.page_render.storage.download_to_temp", side_effect=fake_download), \
             patch("app.page_render._ROOT", tmp_path / "cache"):
            r = client.get(f"/documents/{doc_id}/pages/1/image", params={"q": "Total net sales"})
            assert r.status_code == 200 and r.content[:8] == b"\x89PNG\r\n\x1a\n"
            assert client.get(f"/documents/{doc_id}/pages/999/image").status_code == 404

    def test_views_404_for_unknown_document(self, client):
        for path in ("pages", "validation", "detail"):
            assert client.get(f"/documents/{uuid.uuid4()}/{path}").status_code == 404
