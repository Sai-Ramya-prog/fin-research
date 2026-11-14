"""
tests/test_migration.py
Chroma -> Qdrant copy script: vectors must arrive unchanged, with no embedding
calls, and a re-run must not duplicate anything.
"""

from __future__ import annotations

import uuid

import pytest

chromadb = pytest.importorskip("chromadb")

from qdrant_client import QdrantClient

from app import vectorstore
from scripts import chroma_to_qdrant as mig

DIM = 768


@pytest.fixture()
def qdrant(monkeypatch):
    client = QdrantClient(":memory:")
    monkeypatch.setattr(vectorstore, "get_client", lambda: client)
    monkeypatch.setattr(vectorstore.settings, "qdrant_collection", f"mig-{uuid.uuid4().hex[:8]}")
    vectorstore._ensured.clear()
    return client


@pytest.fixture()
def chroma_collection():
    """An in-memory Chroma collection holding 650 chunks (more than 2 export pages)."""
    col = chromadb.EphemeralClient().get_or_create_collection(f"src-{uuid.uuid4().hex[:8]}")
    doc = str(uuid.uuid4())
    ids = [f"{doc}:{i // 2 + 1}:{i % 2}" for i in range(650)]
    vecs = [[(i + 1) / 1000.0] + [0.01] * (DIM - 1) for i in range(650)]
    metas = [{"doc_id": doc, "section": "balance_sheet", "pdf_page": str(i // 2 + 1), "printed_page": -1}
             for i in range(650)]
    for s in range(0, 650, 300):
        col.add(ids=ids[s:s + 300], embeddings=vecs[s:s + 300],
                documents=[f"text {i}" for i in range(s, min(s + 300, 650))], metadatas=metas[s:s + 300])
    return col, ids, vecs, doc


def test_full_copy_keeps_vectors_text_and_metadata(tmp_path, qdrant, chroma_collection):
    col, ids, vecs, doc = chroma_collection
    backup = tmp_path / "b.jsonl"

    assert mig.export_from_chroma(col, backup) == 650
    assert mig.import_to_qdrant(backup) == 650
    mig.verify(backup)

    assert qdrant.count(vectorstore.settings.qdrant_collection).count == 650
    pt = qdrant.retrieve(vectorstore.settings.qdrant_collection, [vectorstore.point_id(ids[7])],
                         with_vectors=True, with_payload=True)[0]
    assert pt.payload["chunk_id"] == ids[7]
    assert pt.payload["text"] == "text 7"
    assert pt.payload["section"] == "balance_sheet" and pt.payload["doc_id"] == doc
    # Qdrant stores cosine vectors length-normalised; the direction must be unchanged.
    norm = sum(x * x for x in vecs[7]) ** 0.5
    assert pt.vector == pytest.approx([x / norm for x in vecs[7]], abs=1e-5)


def test_rerun_does_not_duplicate(tmp_path, qdrant, chroma_collection):
    col, *_ = chroma_collection
    backup = tmp_path / "b.jsonl"
    mig.export_from_chroma(col, backup)
    mig.import_to_qdrant(backup)
    mig.import_to_qdrant(backup)
    assert qdrant.count(vectorstore.settings.qdrant_collection).count == 650


def test_import_runs_without_embedding_calls(tmp_path, qdrant, chroma_collection, monkeypatch):
    col, *_ = chroma_collection
    backup = tmp_path / "b.jsonl"
    mig.export_from_chroma(col, backup)

    def boom(*a, **k):
        raise AssertionError("the migration must never call the embedding model")
    monkeypatch.setattr("app.embeddings.get_embeddings", boom)
    mig.import_to_qdrant(backup)


def test_wrong_vector_size_is_rejected(tmp_path, qdrant):
    import json
    backup = tmp_path / "b.jsonl"
    backup.write_text(json.dumps({"id": "d:1:0", "vec": [0.1] * 16, "text": "t", "meta": {}}) + "\n")
    with pytest.raises(RuntimeError, match="16-dim"):
        mig.import_to_qdrant(backup)


def test_verify_fails_when_a_chunk_is_missing(tmp_path, qdrant):
    import json
    vectorstore.ensure_collection()
    backup = tmp_path / "b.jsonl"
    backup.write_text(json.dumps({"id": "d:1:0", "vec": [0.1] * DIM, "text": "t", "meta": {}}) + "\n")
    with pytest.raises(RuntimeError, match="missing"):
        mig.verify(backup)
