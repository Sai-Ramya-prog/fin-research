"""
app/vectorstore.py
─────────────────────────────────────────────────────────────────────────────
The ONLY module that talks to the vector database (Qdrant).

Everything else (indexing, search, delete, health check) goes through the small
functions below, so swapping the database again later means editing one file.

Connection
──────────
  QDRANT_URL        https://<cluster>.qdrant.io   (Qdrant Cloud)
                    http://localhost:6333          (local Docker)
                    :memory:                       (in-process, used by tests)
  QDRANT_API_KEY    Qdrant Cloud API key (leave empty for local Docker)
  QDRANT_COLLECTION collection name (default ``financial_docs``)

Ids
───
Qdrant point ids must be an integer or a UUID. Our chunk ids look like
``<doc_id>:<pdf_page>:<k>``, so each one is turned into a UUID with ``uuid5``
and a FIXED namespace (``point_id``). The original id is also stored in the
payload as ``chunk_id``. NEVER change the namespace: the migration script and
the app must agree, or already-stored chunks would stop being found.

Distance
────────
Cosine. Scores returned by search are cosine similarity (higher = closer).
"""

from __future__ import annotations

import logging
import threading
import uuid
from typing import Any, Dict, List, Optional, Sequence

from qdrant_client import QdrantClient, models

from app.config import settings
from app.embeddings import embedding_fingerprint

logger = logging.getLogger(__name__)

# Fixed forever - see module docstring.
ID_NAMESPACE = uuid.UUID("6f1c2a52-0000-4000-8000-000000000001")

MAX_BATCH = 200          # points per request (retrieve / upsert / delete)
SCROLL_PAGE = 500

# Payload fields we filter on. Indexed so filtered search stays fast in the cloud.
_INDEXED_FIELDS = (
    "doc_id", "section", "scope", "company", "fiscal_year",
    "pdf_page", "pdf_name", "source_type",
)

_client: Optional[QdrantClient] = None
_ensured: set[str] = set()
_lock = threading.Lock()


# ── ids ───────────────────────────────────────────────────────────────────────

def point_id(chunk_id: str) -> str:
    """Stable UUID for a chunk id such as ``<doc>:<page>:<k>``."""
    return str(uuid.uuid5(ID_NAMESPACE, chunk_id))


# ── connection ────────────────────────────────────────────────────────────────

def get_client() -> QdrantClient:
    """One shared client per process."""
    global _client
    with _lock:
        if _client is None:
            url = settings.qdrant_url
            if not url:
                raise RuntimeError("QDRANT_URL is not set. Add it to backend/.env.")
            if url == ":memory:":
                _client = QdrantClient(":memory:")
            else:
                _client = QdrantClient(
                    url=url, api_key=settings.qdrant_api_key or None, timeout=60,
                )
        return _client


def reset_client() -> None:
    """Forget the shared client (used by tests)."""
    global _client
    with _lock:
        _client = None
        _ensured.clear()


def _vector_size() -> int:
    """Vector size from the embedding fingerprint, e.g. gemini:...:3072 -> 3072."""
    tail = embedding_fingerprint().rsplit(":", 1)[1]
    if not tail.isdigit():
        raise RuntimeError(
            f"Unknown embedding dimension in fingerprint {embedding_fingerprint()!r}."
        )
    return int(tail)


def ensure_collection() -> str:
    """Create the collection if missing and check its vector size.

    A size mismatch means the collection was built with a different embedding
    model; vectors from different models cannot be searched together.
    """
    name = settings.qdrant_collection
    if name in _ensured:
        return name
    client = get_client()
    size = _vector_size()
    if not client.collection_exists(name):
        client.create_collection(
            collection_name=name,
            vectors_config=models.VectorParams(size=size, distance=models.Distance.COSINE),
        )
        if settings.qdrant_url != ":memory:":   # in-process mode has no payload indexes
            for field in _INDEXED_FIELDS:
                client.create_payload_index(
                    collection_name=name,
                    field_name=field,
                    field_schema=models.PayloadSchemaType.KEYWORD,
                )
        logger.info("Created Qdrant collection %s (size=%d, cosine)", name, size)
    else:
        existing = client.get_collection(name).config.params.vectors
        existing_size = getattr(existing, "size", None)
        if existing_size is not None and existing_size != size:
            raise RuntimeError(
                f"Embedding size mismatch: collection '{name}' has {existing_size}-dim vectors "
                f"but the current embedding model ({embedding_fingerprint()}) makes {size}. "
                "Use a new QDRANT_COLLECTION or re-index everything."
            )
    _ensured.add(name)
    return name


def ping() -> bool:
    """True when the vector database is reachable (used by /health)."""
    try:
        ensure_collection()
        get_client().get_collections()
        return True
    except Exception as exc:  # pragma: no cover - logged, never raised
        logger.error("Vector store ping failed: %s", exc)
        return False


# ── reads ─────────────────────────────────────────────────────────────────────

def _doc_filter(document_id: uuid.UUID | str) -> models.Filter:
    return models.Filter(
        must=[models.FieldCondition(key="doc_id", match=models.MatchValue(value=str(document_id)))]
    )


def existing_ids(chunk_ids: Sequence[str]) -> set[str]:
    """Which of *chunk_ids* are already stored."""
    client, name = get_client(), ensure_collection()
    found: set[str] = set()
    ids = list(chunk_ids)
    for start in range(0, len(ids), MAX_BATCH):
        part = ids[start : start + MAX_BATCH]
        points = client.retrieve(
            collection_name=name,
            ids=[point_id(c) for c in part],
            with_payload=["chunk_id"],
            with_vectors=False,
        )
        found.update(p.payload["chunk_id"] for p in points if p.payload and "chunk_id" in p.payload)
    return found


def document_chunk_ids(document_id: uuid.UUID | str) -> List[str]:
    """Every stored chunk id of one document."""
    client, name = get_client(), ensure_collection()
    out: List[str] = []
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=name,
            scroll_filter=_doc_filter(document_id),
            limit=SCROLL_PAGE,
            offset=offset,
            with_payload=["chunk_id"],
            with_vectors=False,
        )
        out.extend(p.payload["chunk_id"] for p in points if p.payload and "chunk_id" in p.payload)
        if offset is None:
            return out


def search(
    vector: Sequence[float],
    limit: int,
    must: Optional[Dict[str, str]] = None,
) -> List[models.ScoredPoint]:
    """Nearest chunks to *vector*; ``must`` maps payload field -> exact value."""
    client, name = get_client(), ensure_collection()
    flt = None
    if must:
        flt = models.Filter(
            must=[models.FieldCondition(key=k, match=models.MatchValue(value=v)) for k, v in must.items()]
        )
    res = client.query_points(
        collection_name=name,
        query=list(vector),
        query_filter=flt,
        limit=limit,
        with_payload=True,
    )
    return list(res.points)


# ── writes ────────────────────────────────────────────────────────────────────

def upsert(
    chunk_ids: Sequence[str],
    texts: Sequence[str],
    metadatas: Sequence[Dict[str, Any]],
    vectors: Sequence[Sequence[float]],
) -> None:
    """Store chunks. Same chunk id twice = overwritten, never duplicated."""
    client, name = get_client(), ensure_collection()
    fp = embedding_fingerprint()
    points = [
        models.PointStruct(
            id=point_id(cid),
            vector=list(vec),
            payload={**meta, "text": text, "chunk_id": cid, "embedding_fingerprint": fp},
        )
        for cid, text, meta, vec in zip(chunk_ids, texts, metadatas, vectors)
    ]
    for start in range(0, len(points), MAX_BATCH):
        client.upsert(collection_name=name, points=points[start : start + MAX_BATCH], wait=True)


def set_company(document_id: uuid.UUID | str, company: str) -> int:
    """Rewrite the ``company`` payload of every chunk of a document, in place.

    No embedding is touched, so this costs no quota. Returns how many chunks
    belong to the document.
    """
    client, name = get_client(), ensure_collection()
    flt = _doc_filter(document_id)
    count = client.count(collection_name=name, count_filter=flt, exact=True).count
    if count:
        client.set_payload(
            collection_name=name,
            payload={"company": company},
            points=models.FilterSelector(filter=flt),
            wait=True,
        )
    return count


def delete_chunk_ids(chunk_ids: Sequence[str]) -> None:
    client, name = get_client(), ensure_collection()
    ids = list(chunk_ids)
    for start in range(0, len(ids), MAX_BATCH):
        client.delete(
            collection_name=name,
            points_selector=models.PointIdsList(
                points=[point_id(c) for c in ids[start : start + MAX_BATCH]]
            ),
            wait=True,
        )


def delete_document(document_id: uuid.UUID | str) -> int:
    """Delete every chunk of a document. Returns how many were removed."""
    client, name = get_client(), ensure_collection()
    flt = _doc_filter(document_id)
    count = client.count(collection_name=name, count_filter=flt, exact=True).count
    if count:
        client.delete(
            collection_name=name,
            points_selector=models.FilterSelector(filter=flt),
            wait=True,
        )
    return count
