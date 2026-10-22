"""
app/embeddings.py
─────────────────────────────────────────────────────────────────────────────
Embeddings provider abstraction.

Supported providers (EMBEDDING_PROVIDER in .env)
─────────────────────────────────────────────────
  gemini  – Google Generative AI embeddings via langchain-google-genai.
             Requires GOOGLE_API_KEY.  Default model: gemini-embedding-001.
  fake    – Deterministic, offline, fixed-dimension embeddings.
             Useful for tests and dry-runs (no API key required).

Public API
──────────
  get_embeddings() -> LangChain Embeddings
      Returns a ready-to-use Embeddings object.  The object is wrapped so
      that every call goes through retry + exponential back-off on 429 / 5xx
      errors, and a configurable pause is inserted between batches.

  embedding_fingerprint() -> str
      Returns  "<provider>:<model>:<dimension>"  –  stored in Chroma collection
      metadata (Task 7) so we can detect model mismatches early.

Rate-limit handling (Gemini free tier)
───────────────────────────────────────
  - Up to EMBEDDING_MAX_RETRIES attempts (default 6).
  - Exponential back-off: 2^attempt seconds, jittered ±10 %.
  - After each *batch* (list of texts), sleep EMBEDDING_BATCH_PAUSE_SECONDS.
  - A 429 or 5xx error slows the run down; it never fails the document
    immediately.

Design note: we intentionally pick ONE model for the whole project.
Vectors produced by different models are incompatible and cannot be
searched together in Chroma.
"""

from __future__ import annotations

import hashlib
import logging
import random
import time
from typing import Any

from langchain_core.embeddings import Embeddings

from app.config import settings

logger = logging.getLogger(__name__)

# Dimension of the fake provider – fixed so tests are deterministic.
_FAKE_DIMENSION = 768

# Retry settings
_MAX_RETRIES = 6
_BASE_DELAY_SECONDS = 2.0


# ── Fake provider ─────────────────────────────────────────────────────────────

class _FakeEmbeddings(Embeddings):
    """
    Deterministic, offline embeddings for tests and dry-runs.

    Each text is hashed to produce a stable, fixed-length float vector.
    Vectors have unit L2 norm so cosine similarity is well-defined.
    No API key is required.
    """

    dimension: int = _FAKE_DIMENSION

    def _text_to_vector(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode()).digest()
        # Expand the 32-byte digest to `dimension` floats using a seeded RNG.
        seed = int.from_bytes(digest, "big") % (2**32)
        rng = random.Random(seed)
        vec = [rng.gauss(0, 1) for _ in range(self.dimension)]
        norm = sum(v ** 2 for v in vec) ** 0.5 or 1.0
        return [v / norm for v in vec]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._text_to_vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._text_to_vector(text)


# ── Retry wrapper ─────────────────────────────────────────────────────────────

class _RetryEmbeddings(Embeddings):
    """
    Wraps any LangChain Embeddings object and adds:
      - Retry with exponential back-off on 429 / 5xx / transient errors.
      - A configurable pause between batches (EMBEDDING_BATCH_PAUSE_SECONDS).
    """

    def __init__(self, inner: Embeddings, max_retries: int = _MAX_RETRIES) -> None:
        self._inner = inner
        self._max_retries = max_retries

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        """Return True for 429 rate-limit and 5xx server errors."""
        msg = str(exc).lower()
        retryable_phrases = (
            "429",
            "quota",
            "rate limit",
            "resource exhausted",
            "too many requests",
            "500",
            "502",
            "503",
            "service unavailable",
            "internal server error",
        )
        return any(p in msg for p in retryable_phrases)

    def _call_with_retry(self, fn, *args, **kwargs):
        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                if not self._is_retryable(exc):
                    raise
                last_exc = exc
                if attempt == self._max_retries:
                    break
                delay = _BASE_DELAY_SECONDS * (2 ** attempt)
                # Add ±10 % jitter to avoid thundering herd
                jitter = delay * 0.1 * (2 * random.random() - 1)
                sleep_for = max(0.1, delay + jitter)
                logger.warning(
                    "Embedding rate-limited (attempt %d/%d).  "
                    "Retrying in %.1fs.  Error: %s",
                    attempt + 1,
                    self._max_retries,
                    sleep_for,
                    exc,
                )
                time.sleep(sleep_for)
        raise RuntimeError(
            f"Embedding call failed after {self._max_retries} retries."
        ) from last_exc

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        result = self._call_with_retry(self._inner.embed_documents, texts)
        pause = settings.embedding_batch_pause_seconds
        if pause > 0:
            logger.debug("Batch pause: sleeping %.1fs", pause)
            time.sleep(pause)
        return result

    def embed_query(self, text: str) -> list[float]:
        return self._call_with_retry(self._inner.embed_query, text)


# ── Provider factory ──────────────────────────────────────────────────────────

def _build_inner_embeddings() -> Embeddings:
    provider = settings.embedding_provider
    model = settings.embedding_model

    if provider == "fake":
        logger.info("Using fake embeddings (dimension=%d)", _FAKE_DIMENSION)
        return _FakeEmbeddings()

    if provider == "gemini":
        if not settings.google_api_key:
            raise RuntimeError(
                "GOOGLE_API_KEY is required for EMBEDDING_PROVIDER=gemini.\n"
                "  → Get a free key at https://aistudio.google.com/app/apikey\n"
                "  → Add it to your .env file:  GOOGLE_API_KEY=<your-key>"
            )
        from langchain_google_genai import GoogleGenerativeAIEmbeddings

        logger.info("Using Gemini embeddings model=%s", model)
        return GoogleGenerativeAIEmbeddings(
            model=model,
            google_api_key=settings.google_api_key,
        )

    raise ValueError(
        f"Unknown EMBEDDING_PROVIDER={provider!r}.  "
        "Valid values: 'gemini', 'fake'."
    )


# ── Public API ────────────────────────────────────────────────────────────────

def get_embeddings() -> Embeddings:
    """
    Return a LangChain ``Embeddings`` object for the configured provider.

    The object wraps the underlying provider with:
      - Automatic retry + exponential back-off on 429 / 5xx errors.
      - A configurable pause between batches (``EMBEDDING_BATCH_PAUSE_SECONDS``).

    Use the returned object everywhere in the pipeline::

        emb = get_embeddings()
        vector = emb.embed_query("total assets")        # → list[float]
        vectors = emb.embed_documents(["doc1", "doc2"]) # → list[list[float]]
    """
    inner = _build_inner_embeddings()
    return _RetryEmbeddings(inner)


def embedding_fingerprint() -> str:
    """
    Return a stable identifier string for the current embedding config.

    Format: ``"<provider>:<model>:<dimension>"``

    Example::
        "gemini:gemini-embedding-001:3072"
        "fake:fake:768"

    This value is stored in the Chroma collection metadata (Task 7) so
    mismatches between the stored vectors and the active model are caught
    before any search is attempted.
    """
    provider = settings.embedding_provider
    model = settings.embedding_model

    if provider == "fake":
        return f"fake:fake:{_FAKE_DIMENSION}"

    if provider == "gemini":
        # Gemini embedding-001 produces 3072-dimensional vectors.
        # If the model changes, update this mapping.
        _GEMINI_DIMENSIONS: dict[str, int] = {
            "gemini-embedding-001": 3072,
            "text-embedding-004": 768,
        }
        dim = _GEMINI_DIMENSIONS.get(model, 3072)
        return f"gemini:{model}:{dim}"

    return f"{provider}:{model}:unknown"
