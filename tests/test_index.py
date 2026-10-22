"""
tests/test_index.py
───────────────────────────────────────────────────────────────────────────────
Unit tests for app/index.py (Task 7: Embedding & vector indexing).

All tests use:
  - ``EMBEDDING_PROVIDER=fake`` so no real embedding API is called.
  - A mock Chroma ``EphemeralClient`` (in-memory) instead of CloudClient so
    no Chroma Cloud credentials are needed.
  - A SQLite throwaway database (via ``app.db.engine``) for Document rows.

Run with:
    pytest tests/test_index.py -v
"""

from __future__ import annotations

import time
import types
import uuid
import pytest
from unittest.mock import MagicMock, patch, call
from typing import List, Dict, Any


# ---------------------------------------------------------------------------
# Helpers – build a minimal chunk list
# ---------------------------------------------------------------------------

def _make_chunks(n: int, doc_id: uuid.UUID) -> List[Dict[str, Any]]:
    """Return *n* minimal chunk dicts compatible with index_chunks."""
    return [
        {
            "text": f"chunk text number {i}",
            "metadata": {
                "doc_id": str(doc_id),
                "chunk_index": i,
                "pdf_page": i + 1,
                "printed_page": i + 1,
                "section": "balance_sheet",
                "scope": "consolidated",
                "unit": "rs in crores",
                "content_type": "table",
                "company": "TestCo",
                "fiscal_year": "FY2024",
                "source_file": "test.pdf",
                "session_id": str(uuid.uuid4()),
                "part": 1,
            },
        }
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _patch_env(monkeypatch):
    """Override env so no real Chroma / DB credentials are needed."""
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    monkeypatch.setenv("CHROMA_API_KEY", "test-key")
    monkeypatch.setenv("CHROMA_TENANT", "test-tenant")
    monkeypatch.setenv("CHROMA_DATABASE", "test-db")
    monkeypatch.setenv("CHROMA_COLLECTION", "test-collection")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./test_index.db")
    monkeypatch.setenv("GOOGLE_API_KEY", "dummy")


@pytest.fixture()
def ephemeral_collection():
    """Return an in-memory Chroma collection using EphemeralClient."""
    import chromadb
    client = chromadb.EphemeralClient()
    # EphemeralClient state is shared across tests in one process, so give every
    # test its own collection.
    collection = client.get_or_create_collection(
        name=f"test-collection-{uuid.uuid4().hex[:8]}",
        metadata={"embedding_fingerprint": "fake:fake-model:16"},
    )
    return collection


# ---------------------------------------------------------------------------
# Status tracker helper
# ---------------------------------------------------------------------------

def _make_status_tracker():
    statuses = []

    def _fake_set_status(doc_id, new_status, error_message=None):
        statuses.append((new_status, error_message))

    return statuses, _fake_set_status


# ---------------------------------------------------------------------------
# Tests – _cloud_client
# ---------------------------------------------------------------------------

class TestCloudClient:
    def test_raises_when_tenant_missing(self):
        from app import index as idx
        bad_settings = MagicMock()
        bad_settings.chroma_tenant = ""
        bad_settings.chroma_database = "db"
        bad_settings.chroma_api_key = "key"
        bad_settings.chroma_collection = "col"
        with patch.object(idx, "settings", bad_settings):
            with pytest.raises(RuntimeError, match="CHROMA_TENANT"):
                idx._cloud_client()

    def test_raises_when_api_key_missing(self):
        from app import index as idx
        bad_settings = MagicMock()
        bad_settings.chroma_tenant = "tenant"
        bad_settings.chroma_database = "db"
        bad_settings.chroma_api_key = ""
        bad_settings.chroma_collection = "col"
        with patch.object(idx, "settings", bad_settings):
            with pytest.raises(RuntimeError, match="CHROMA_API_KEY"):
                idx._cloud_client()

    def test_raises_when_database_missing(self):
        from app import index as idx
        bad_settings = MagicMock()
        bad_settings.chroma_tenant = "tenant"
        bad_settings.chroma_database = ""
        bad_settings.chroma_api_key = "key"
        bad_settings.chroma_collection = "col"
        with patch.object(idx, "settings", bad_settings):
            with pytest.raises(RuntimeError, match="CHROMA_DATABASE"):
                idx._cloud_client()


# ---------------------------------------------------------------------------
# Tests – index_chunks
# ---------------------------------------------------------------------------

class TestIndexChunks:
    def test_single_chunk_indexed_correctly(self, ephemeral_collection):
        from app import index as idx
        doc_id = uuid.uuid4()
        chunks = _make_chunks(1, doc_id)
        statuses, fake_set = _make_status_tracker()

        with (
            patch.object(idx, "_get_collection", return_value=ephemeral_collection),
            patch.object(idx, "_set_document_status", side_effect=fake_set),
        ):
            idx.index_chunks(doc_id, chunks)

        assert statuses[-1][0] == "ready"
        results = ephemeral_collection.get(where={"doc_id": str(doc_id)})
        assert len(results["ids"]) == 1
        assert results["ids"][0] == f"{doc_id}:1:0"  # <doc>:<pdf_page>:<k>

    def test_idempotent_second_run_same_count(self, ephemeral_collection):
        """Indexing the same doc twice leaves exactly the same chunk count."""
        from app import index as idx
        doc_id = uuid.uuid4()
        chunks = _make_chunks(5, doc_id)
        _, fake_set = _make_status_tracker()

        with (
            patch.object(idx, "_get_collection", return_value=ephemeral_collection),
            patch.object(idx, "_set_document_status", side_effect=fake_set),
        ):
            idx.index_chunks(doc_id, chunks)
            first_count = len(
                ephemeral_collection.get(where={"doc_id": str(doc_id)})["ids"]
            )
            idx.index_chunks(doc_id, chunks)
            second_count = len(
                ephemeral_collection.get(where={"doc_id": str(doc_id)})["ids"]
            )

        assert first_count == 5
        assert second_count == 5  # idempotent – not 10

    def test_status_sequence_indexing_then_ready(self, ephemeral_collection):
        from app import index as idx
        doc_id = uuid.uuid4()
        chunks = _make_chunks(3, doc_id)
        statuses, fake_set = _make_status_tracker()

        with (
            patch.object(idx, "_get_collection", return_value=ephemeral_collection),
            patch.object(idx, "_set_document_status", side_effect=fake_set),
        ):
            idx.index_chunks(doc_id, chunks)

        status_values = [s[0] for s in statuses]
        assert "indexing" in status_values
        assert status_values[-1] == "ready"

    def test_metadata_none_printed_page_replaced_with_minus_one(self, ephemeral_collection):
        """None printed_page should become -1, not break Chroma."""
        from app import index as idx
        doc_id = uuid.uuid4()
        chunks = _make_chunks(1, doc_id)
        chunks[0]["metadata"]["printed_page"] = None
        _, fake_set = _make_status_tracker()

        with (
            patch.object(idx, "_get_collection", return_value=ephemeral_collection),
            patch.object(idx, "_set_document_status", side_effect=fake_set),
        ):
            idx.index_chunks(doc_id, chunks)

        results = ephemeral_collection.get(
            where={"doc_id": str(doc_id)}, include=["metadatas"]
        )
        assert results["metadatas"][0]["printed_page"] == -1

    def test_chunk_ids_deterministic(self, ephemeral_collection):
        """Chunk IDs are <doc_id>:<index> — deterministic."""
        from app import index as idx
        doc_id = uuid.uuid4()
        chunks = _make_chunks(3, doc_id)
        _, fake_set = _make_status_tracker()

        with (
            patch.object(idx, "_get_collection", return_value=ephemeral_collection),
            patch.object(idx, "_set_document_status", side_effect=fake_set),
        ):
            idx.index_chunks(doc_id, chunks)

        results = ephemeral_collection.get(where={"doc_id": str(doc_id)})
        expected_ids = {f"{doc_id}:1:0", f"{doc_id}:2:0", f"{doc_id}:3:0"}
        assert set(results["ids"]) == expected_ids

    def test_embedding_error_marks_document_failed(self, ephemeral_collection):
        """If embedding raises an exception 3 times the doc is marked failed."""
        from app import index as idx
        doc_id = uuid.uuid4()
        chunks = _make_chunks(2, doc_id)
        statuses, fake_set = _make_status_tracker()

        boom_embeddings = MagicMock()
        boom_embeddings.embed_documents.side_effect = RuntimeError("API error")

        with (
            patch.object(idx, "_get_collection", return_value=ephemeral_collection),
            patch.object(idx, "_set_document_status", side_effect=fake_set),
            patch("app.index.get_embeddings", return_value=boom_embeddings),
            patch("time.sleep"),
        ):
            with pytest.raises(RuntimeError, match="API error"):
                idx.index_chunks(doc_id, chunks)

        status_values = [s[0] for s in statuses]
        assert "failed" in status_values

    def test_writes_to_chroma_after_every_embedding_batch(self, ephemeral_collection):
        """Each embedded batch (<= EMBED_BATCH_SIZE) is written immediately, so a
        later failure never loses vectors that were already paid for."""
        from app import index as idx
        doc_id = uuid.uuid4()
        chunks = _make_chunks(350, doc_id)
        _, fake_set = _make_status_tracker()

        add_calls = []
        original_add = ephemeral_collection.add

        def tracking_add(**kwargs):
            add_calls.append(len(kwargs["ids"]))
            return original_add(**kwargs)

        ephemeral_collection.add = tracking_add

        with (
            patch.object(idx, "_get_collection", return_value=ephemeral_collection),
            patch.object(idx, "_set_document_status", side_effect=fake_set),
        ):
            idx.index_chunks(doc_id, chunks)

        assert sum(add_calls) == 350
        assert max(add_calls) <= idx.EMBED_BATCH_SIZE
        assert ephemeral_collection.count() == 350

    def test_rerun_embeds_nothing_when_already_indexed(self, ephemeral_collection):
        from app import index as idx
        doc_id = uuid.uuid4()
        chunks = _make_chunks(40, doc_id)
        _, fake_set = _make_status_tracker()
        with (
            patch.object(idx, "_get_collection", return_value=ephemeral_collection),
            patch.object(idx, "_set_document_status", side_effect=fake_set),
            patch.object(idx, "get_embeddings") as ge,
        ):
            ge.return_value.embed_documents.side_effect = lambda t: [[0.1] * 16 for _ in t]
            idx.index_chunks(doc_id, chunks)
            first = ge.return_value.embed_documents.call_count
            idx.index_chunks(doc_id, chunks)  # second run: nothing to embed
            assert ge.return_value.embed_documents.call_count == first

    def test_resume_embeds_only_missing_chunks(self, ephemeral_collection):
        from app import index as idx
        doc_id = uuid.uuid4()
        chunks = _make_chunks(70, doc_id)
        _, fake_set = _make_status_tracker()
        embedded = []

        def fake_embed(texts):
            embedded.extend(texts)
            return [[0.1] * 16 for _ in texts]

        # Pre-store the first 32 chunks as if an earlier run got that far.
        ephemeral_collection.add(
            ids=[f"{doc_id}:{i + 1}:0" for i in range(32)],
            documents=[c["text"] for c in chunks[:32]],
            metadatas=[{"doc_id": str(doc_id)}] * 32,
            embeddings=[[0.1] * 16] * 32,
        )
        with (
            patch.object(idx, "_get_collection", return_value=ephemeral_collection),
            patch.object(idx, "_set_document_status", side_effect=fake_set),
            patch.object(idx, "get_embeddings") as ge,
        ):
            ge.return_value.embed_documents.side_effect = fake_embed
            idx.index_chunks(doc_id, chunks)
        assert len(embedded) == 38
        assert ephemeral_collection.count() == 70

    def test_daily_quota_marks_failed_and_keeps_progress(self, ephemeral_collection):
        from app import index as idx
        from app.embeddings import QuotaExhaustedError
        doc_id = uuid.uuid4()
        chunks = _make_chunks(100, doc_id)
        statuses, fake_set = _make_status_tracker()
        calls = {"n": 0}

        def fake_embed(texts):
            calls["n"] += 1
            if calls["n"] == 2:
                raise QuotaExhaustedError("Gemini daily embedding quota exhausted.")
            return [[0.1] * 16 for _ in texts]

        with (
            patch.object(idx, "_get_collection", return_value=ephemeral_collection),
            patch.object(idx, "_set_document_status", side_effect=fake_set),
            patch.object(idx, "get_embeddings") as ge,
        ):
            ge.return_value.embed_documents.side_effect = fake_embed
            with pytest.raises(QuotaExhaustedError):
                idx.index_chunks(doc_id, chunks)
        assert ephemeral_collection.count() == idx.EMBED_BATCH_SIZE  # first batch kept
        assert statuses[-1][0] == "failed"
        assert "quota exhausted" in statuses[-1][1]


class TestTieredIds:
    def test_ids_are_page_based_and_stable(self):
        from app.index import chunk_ids
        doc_id = uuid.uuid4()
        chunks = [
            {"text": "a", "metadata": {"pdf_page": "5"}},
            {"text": "b", "metadata": {"pdf_page": "5"}},
            {"text": "c", "metadata": {"pdf_page": "9"}},
        ]
        assert chunk_ids(doc_id, chunks) == [
            f"{doc_id}:5:0", f"{doc_id}:5:1", f"{doc_id}:9:0",
        ]
        # adding other pages later never changes ids of pages already stored
        more = chunks + [{"text": "d", "metadata": {"pdf_page": "7"}}]
        assert chunk_ids(doc_id, more)[:3] == chunk_ids(doc_id, chunks)

    def test_old_format_chunks_are_removed(self, ephemeral_collection):
        from app import index as idx
        doc_id = uuid.uuid4()
        _, fake_set = _make_status_tracker()
        # legacy chunk from the old id scheme  <doc>:<n>
        ephemeral_collection.add(
            ids=[f"{doc_id}:0"], documents=["old"],
            metadatas=[{"doc_id": str(doc_id)}], embeddings=[[0.1] * 16],
        )
        with (
            patch.object(idx, "_get_collection", return_value=ephemeral_collection),
            patch.object(idx, "_set_document_status", side_effect=fake_set),
            patch.object(idx, "get_embeddings") as ge,
        ):
            ge.return_value.embed_documents.side_effect = lambda t: [[0.1] * 16 for _ in t]
            pages = idx.index_chunks(doc_id, _make_chunks(2, doc_id))
        ids = set(ephemeral_collection.get(where={"doc_id": str(doc_id)})["ids"])
        assert f"{doc_id}:0" not in ids
        assert pages == [1, 2]

    def test_track_status_false_leaves_status_alone(self, ephemeral_collection):
        from app import index as idx
        doc_id = uuid.uuid4()
        statuses, fake_set = _make_status_tracker()
        with (
            patch.object(idx, "_get_collection", return_value=ephemeral_collection),
            patch.object(idx, "_set_document_status", side_effect=fake_set),
            patch.object(idx, "get_embeddings") as ge,
        ):
            ge.return_value.embed_documents.side_effect = lambda t: [[0.1] * 16 for _ in t]
            idx.index_chunks(doc_id, _make_chunks(3, doc_id), track_status=False)
        assert statuses == []


# ---------------------------------------------------------------------------
# Tests – ping_chroma
# ---------------------------------------------------------------------------

class TestPingChroma:
    def test_ping_returns_true_when_collection_ok(self, ephemeral_collection):
        from app import index as idx
        with patch.object(idx, "_get_collection", return_value=ephemeral_collection):
            assert idx.ping_chroma() is True

    def test_ping_returns_false_on_exception(self):
        from app import index as idx
        with patch.object(idx, "_get_collection", side_effect=RuntimeError("bad key")):
            assert idx.ping_chroma() is False


# ---------------------------------------------------------------------------
# Tests – fingerprint mismatch
# ---------------------------------------------------------------------------

class TestFingerprintMismatch:
    def test_mismatched_fingerprint_raises(self):
        """Stored fingerprint != current fingerprint must raise RuntimeError."""
        from app import index as idx
        import chromadb

        client = chromadb.EphemeralClient()
        # Create collection with a DIFFERENT fingerprint
        client.get_or_create_collection(
            name="test-fp",
            metadata={"embedding_fingerprint": "openai:text-embedding-3-small:1536"},
        )

        with (
            patch.object(idx, "_cloud_client", return_value=client),
            patch.object(idx, "settings") as mock_settings,
            patch("app.index.embedding_fingerprint", return_value="fake:fake-model:16"),
        ):
            mock_settings.chroma_collection = "test-fp"
            with pytest.raises(RuntimeError, match="fingerprint mismatch"):
                idx._get_collection()
