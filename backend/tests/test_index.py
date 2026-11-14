"""
tests/test_index.py
───────────────────────────────────────────────────────────────────────────────
Unit tests for app/index.py (Task 7: Embedding & vector indexing).

All tests use:
  - ``EMBEDDING_PROVIDER=fake`` so no real embedding API is called.
  - Qdrant in its in-process ``:memory:`` mode (see the ``store`` fixture), so
    no Qdrant server, key or network is needed.
  - ``_set_document_status`` is replaced by a tracker, so no database is needed.

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

DIM = 768  # size of the fake embedding vectors (fake:fake:768)


def _vecs(texts):
    return [[0.1] * DIM for _ in texts]


@pytest.fixture(autouse=True)
def _patch_env(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")


@pytest.fixture()
def store(monkeypatch):
    """An empty in-memory Qdrant collection, fresh for every test.

    Returns a small helper with ``count()``, ``ids(doc_id)`` and ``payloads(doc_id)``.
    """
    from qdrant_client import QdrantClient, models
    from app import vectorstore

    client = QdrantClient(":memory:")
    name = f"test-{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(vectorstore, "get_client", lambda: client)
    monkeypatch.setattr(vectorstore.settings, "qdrant_collection", name)
    vectorstore._ensured.clear()

    class Store:
        client_ = client
        name_ = name

        def count(self):
            return client.count(name).count

        def _by_doc(self, doc_id):
            flt = models.Filter(must=[models.FieldCondition(
                key="doc_id", match=models.MatchValue(value=str(doc_id)))])
            pts, _ = client.scroll(name, scroll_filter=flt, limit=10_000, with_payload=True)
            return pts

        def ids(self, doc_id):
            return {p.payload["chunk_id"] for p in self._by_doc(doc_id)}

        def payloads(self, doc_id):
            return [p.payload for p in self._by_doc(doc_id)]

        def add_raw(self, chunk_id, doc_id, text="old"):
            vectorstore.upsert([chunk_id], [text], [{"doc_id": str(doc_id)}], [[0.1] * DIM])

    return Store()


# ---------------------------------------------------------------------------
# Status tracker helper
# ---------------------------------------------------------------------------

def _make_status_tracker():
    statuses = []

    def _fake_set_status(doc_id, new_status, error_message=None):
        statuses.append((new_status, error_message))

    return statuses, _fake_set_status


# ---------------------------------------------------------------------------
# Tests – settings
# ---------------------------------------------------------------------------

class TestQdrantSettings:
    def _make(self, **kw):
        from app.config import Settings
        base = dict(_env_file=None, google_api_key="x", database_url="postgresql://x/y")
        base.update(kw)
        return Settings(**base)

    def test_missing_url_fails_fast(self):
        with pytest.raises(ValueError, match="QDRANT_URL"):
            self._make(qdrant_url="")

    def test_cloud_url_needs_api_key(self):
        with pytest.raises(ValueError, match="QDRANT_API_KEY"):
            self._make(qdrant_url="https://abc.cloud.qdrant.io", qdrant_api_key="")

    def test_local_url_needs_no_key(self):
        assert self._make(qdrant_url="http://localhost:6333").qdrant_url.startswith("http://")


# ---------------------------------------------------------------------------
# Tests – index_chunks
# ---------------------------------------------------------------------------

class TestIndexChunks:
    def test_single_chunk_indexed_correctly(self, store):
        from app import index as idx
        doc_id = uuid.uuid4()
        chunks = _make_chunks(1, doc_id)
        statuses, fake_set = _make_status_tracker()

        with (
            patch.object(idx, "_set_document_status", side_effect=fake_set),
        ):
            idx.index_chunks(doc_id, chunks)

        assert statuses[-1][0] == "ready"
        assert store.ids(doc_id) == {f"{doc_id}:1:0"}  # <doc>:<pdf_page>:<k>

    def test_idempotent_second_run_same_count(self, store):
        """Indexing the same doc twice leaves exactly the same chunk count."""
        from app import index as idx
        doc_id = uuid.uuid4()
        chunks = _make_chunks(5, doc_id)
        _, fake_set = _make_status_tracker()

        with (
            patch.object(idx, "_set_document_status", side_effect=fake_set),
        ):
            idx.index_chunks(doc_id, chunks)
            first_count = len(store.ids(doc_id))
            idx.index_chunks(doc_id, chunks)
            second_count = len(store.ids(doc_id))

        assert first_count == 5
        assert second_count == 5  # idempotent – not 10

    def test_status_sequence_indexing_then_ready(self, store):
        from app import index as idx
        doc_id = uuid.uuid4()
        chunks = _make_chunks(3, doc_id)
        statuses, fake_set = _make_status_tracker()

        with (
            patch.object(idx, "_set_document_status", side_effect=fake_set),
        ):
            idx.index_chunks(doc_id, chunks)

        status_values = [s[0] for s in statuses]
        assert "indexing" in status_values
        assert status_values[-1] == "ready"

    def test_metadata_none_printed_page_replaced_with_minus_one(self, store):
        """None printed_page should become -1, not break the vector store."""
        from app import index as idx
        doc_id = uuid.uuid4()
        chunks = _make_chunks(1, doc_id)
        chunks[0]["metadata"]["printed_page"] = None
        _, fake_set = _make_status_tracker()

        with (
            patch.object(idx, "_set_document_status", side_effect=fake_set),
        ):
            idx.index_chunks(doc_id, chunks)

        assert store.payloads(doc_id)[0]["printed_page"] == -1

    def test_chunk_ids_deterministic(self, store):
        """Chunk IDs are <doc_id>:<index> — deterministic."""
        from app import index as idx
        doc_id = uuid.uuid4()
        chunks = _make_chunks(3, doc_id)
        _, fake_set = _make_status_tracker()

        with (
            patch.object(idx, "_set_document_status", side_effect=fake_set),
        ):
            idx.index_chunks(doc_id, chunks)

        expected_ids = {f"{doc_id}:1:0", f"{doc_id}:2:0", f"{doc_id}:3:0"}
        assert store.ids(doc_id) == expected_ids

    def test_embedding_error_marks_document_failed(self, store):
        """If embedding raises an exception 3 times the doc is marked failed."""
        from app import index as idx
        doc_id = uuid.uuid4()
        chunks = _make_chunks(2, doc_id)
        statuses, fake_set = _make_status_tracker()

        boom_embeddings = MagicMock()
        boom_embeddings.embed_documents.side_effect = RuntimeError("API error")

        with (
            patch.object(idx, "_set_document_status", side_effect=fake_set),
            patch("app.index.get_embeddings", return_value=boom_embeddings),
            patch("time.sleep"),
        ):
            with pytest.raises(RuntimeError, match="API error"):
                idx.index_chunks(doc_id, chunks)

        status_values = [s[0] for s in statuses]
        assert "failed" in status_values

    def test_writes_to_store_after_every_embedding_batch(self, store):
        """Each embedded batch (<= EMBED_BATCH_SIZE) is written immediately, so a
        later failure never loses vectors that were already paid for."""
        from app import index as idx
        doc_id = uuid.uuid4()
        chunks = _make_chunks(350, doc_id)
        _, fake_set = _make_status_tracker()

        from app import vectorstore
        add_calls = []
        original_upsert = vectorstore.upsert

        def tracking_upsert(ids, texts, metas, vectors):
            add_calls.append(len(ids))
            return original_upsert(ids, texts, metas, vectors)

        with (
            patch.object(idx, "_set_document_status", side_effect=fake_set),
            patch.object(vectorstore, "upsert", side_effect=tracking_upsert),
        ):
            idx.index_chunks(doc_id, chunks)

        assert sum(add_calls) == 350
        assert max(add_calls) <= idx.EMBED_BATCH_SIZE
        assert store.count() == 350

    def test_rerun_embeds_nothing_when_already_indexed(self, store):
        from app import index as idx
        doc_id = uuid.uuid4()
        chunks = _make_chunks(40, doc_id)
        _, fake_set = _make_status_tracker()
        with (
            patch.object(idx, "_set_document_status", side_effect=fake_set),
            patch.object(idx, "get_embeddings") as ge,
        ):
            ge.return_value.embed_documents.side_effect = _vecs
            idx.index_chunks(doc_id, chunks)
            first = ge.return_value.embed_documents.call_count
            idx.index_chunks(doc_id, chunks)  # second run: nothing to embed
            assert ge.return_value.embed_documents.call_count == first

    def test_resume_embeds_only_missing_chunks(self, store):
        from app import index as idx
        doc_id = uuid.uuid4()
        chunks = _make_chunks(70, doc_id)
        _, fake_set = _make_status_tracker()
        embedded = []

        def fake_embed(texts):
            embedded.extend(texts)
            return _vecs(texts)

        # Pre-store the first 32 chunks as if an earlier run got that far.
        for i in range(32):
            store.add_raw(f"{doc_id}:{i + 1}:0", doc_id, chunks[i]["text"])
        with (
            patch.object(idx, "_set_document_status", side_effect=fake_set),
            patch.object(idx, "get_embeddings") as ge,
        ):
            ge.return_value.embed_documents.side_effect = fake_embed
            idx.index_chunks(doc_id, chunks)
        assert len(embedded) == 38
        assert store.count() == 70

    def test_daily_quota_marks_failed_and_keeps_progress(self, store):
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
            return _vecs(texts)

        with (
            patch.object(idx, "_set_document_status", side_effect=fake_set),
            patch.object(idx, "get_embeddings") as ge,
        ):
            ge.return_value.embed_documents.side_effect = fake_embed
            with pytest.raises(QuotaExhaustedError):
                idx.index_chunks(doc_id, chunks)
        assert store.count() == idx.EMBED_BATCH_SIZE  # first batch kept
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

    def test_old_format_chunks_are_removed(self, store):
        from app import index as idx
        doc_id = uuid.uuid4()
        _, fake_set = _make_status_tracker()
        # legacy chunk from the old id scheme  <doc>:<n>
        store.add_raw(f"{doc_id}:0", doc_id)
        with (
            patch.object(idx, "_set_document_status", side_effect=fake_set),
            patch.object(idx, "get_embeddings") as ge,
        ):
            ge.return_value.embed_documents.side_effect = _vecs
            pages = idx.index_chunks(doc_id, _make_chunks(2, doc_id))
        ids = store.ids(doc_id)
        assert f"{doc_id}:0" not in ids
        assert pages == [1, 2]

    def test_track_status_false_leaves_status_alone(self, store):
        from app import index as idx
        doc_id = uuid.uuid4()
        statuses, fake_set = _make_status_tracker()
        with (
            patch.object(idx, "_set_document_status", side_effect=fake_set),
            patch.object(idx, "get_embeddings") as ge,
        ):
            ge.return_value.embed_documents.side_effect = _vecs
            idx.index_chunks(doc_id, _make_chunks(3, doc_id), track_status=False)
        assert statuses == []


# ---------------------------------------------------------------------------
# Tests – ping_vector_store
# ---------------------------------------------------------------------------

class TestPingVectorStore:
    def test_ping_returns_true_when_store_ok(self, store):
        from app import index as idx
        assert idx.ping_vector_store() is True

    def test_ping_returns_false_on_exception(self, monkeypatch):
        from app import index as idx, vectorstore
        monkeypatch.setattr(vectorstore, "get_client", MagicMock(side_effect=RuntimeError("bad key")))
        vectorstore._ensured.clear()
        assert idx.ping_vector_store() is False


# ---------------------------------------------------------------------------
# Tests – embedding size mismatch
# ---------------------------------------------------------------------------

class TestEmbeddingSizeMismatch:
    def test_collection_with_other_vector_size_raises(self, store):
        """A collection built with a different embedding model must not be reused."""
        from qdrant_client import models
        from app import vectorstore
        store.client_.create_collection(
            "wrong-size", vectors_config=models.VectorParams(size=16, distance=models.Distance.COSINE))
        vectorstore.settings.qdrant_collection = "wrong-size"
        vectorstore._ensured.clear()
        with pytest.raises(RuntimeError, match="size mismatch"):
            vectorstore.ensure_collection()


# ---------------------------------------------------------------------------
# Tests – id conversion and deletion
# ---------------------------------------------------------------------------

class TestVectorStoreHelpers:
    def test_point_id_is_stable_uuid(self):
        from app.vectorstore import point_id
        a = point_id("abc:1:0")
        assert a == point_id("abc:1:0")
        assert a != point_id("abc:1:1")
        uuid.UUID(a)  # valid UUID

    def test_point_id_matches_known_value(self):
        """Guards the namespace: changing it would orphan every stored chunk."""
        from app.vectorstore import point_id
        assert point_id("doc:1:0") == str(uuid.uuid5(uuid.UUID("6f1c2a52-0000-4000-8000-000000000001"), "doc:1:0"))

    def test_delete_document_removes_only_that_document(self, store):
        from app import vectorstore
        d1, d2 = uuid.uuid4(), uuid.uuid4()
        for i in range(3):
            store.add_raw(f"{d1}:{i}:0", d1)
        store.add_raw(f"{d2}:1:0", d2)
        assert vectorstore.delete_document(d1) == 3
        assert store.ids(d1) == set()
        assert store.ids(d2) == {f"{d2}:1:0"}

    def test_force_reindex_replaces_chunks(self, store):
        from app import index as idx
        doc_id = uuid.uuid4()
        _, fake_set = _make_status_tracker()
        with (
            patch.object(idx, "_set_document_status", side_effect=fake_set),
            patch.object(idx, "get_embeddings") as ge,
        ):
            ge.return_value.embed_documents.side_effect = _vecs
            idx.index_chunks(doc_id, _make_chunks(3, doc_id))
            idx.index_chunks(doc_id, _make_chunks(2, doc_id), force=True)
        assert len(store.ids(doc_id)) == 2
