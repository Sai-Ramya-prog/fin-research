"""
tests/test_embeddings.py
─────────────────────────────────────────────────────────────────────────────
Task 3 acceptance tests for app/embeddings.py.

Test classes:
  TestFakeProvider     – always runs, no API key needed.
  TestRetryWrapper     – always runs; simulates 429 errors via monkeypatching.
  TestEmbeddingFingerprint – always runs.
  TestGeminiProvider   – live call against Gemini API.
                          Skipped when EMBEDDING_PROVIDER != 'gemini' or
                          GOOGLE_API_KEY is not set.

Run with:
    pytest tests/test_embeddings.py -v
"""

from __future__ import annotations

import time
import unittest.mock as mock

import pytest

from app.config import settings


# ─────────────────────────────────────────────────────────────────────────────
# Helpers / fixtures
# ─────────────────────────────────────────────────────────────────────────────

QUERY = "total assets"
DOCS = ["total assets", "revenue growth", "net profit margin"]


# ─────────────────────────────────────────────────────────────────────────────
# Fake provider
# ─────────────────────────────────────────────────────────────────────────────

class TestFakeProvider:
    """Fake provider works offline and is fully deterministic."""

    @pytest.fixture(autouse=True)
    def _use_fake(self, monkeypatch):
        monkeypatch.setattr(settings, "embedding_provider", "fake")
        monkeypatch.setattr(settings, "embedding_model", "fake")
        # Disable batch pause so tests are fast
        monkeypatch.setattr(settings, "embedding_batch_pause_seconds", 0.0)

    def test_embed_query_returns_list_of_floats(self):
        from app.embeddings import get_embeddings
        emb = get_embeddings()
        vec = emb.embed_query(QUERY)
        assert isinstance(vec, list)
        assert len(vec) == 768
        assert all(isinstance(v, float) for v in vec)

    def test_embed_query_is_deterministic(self):
        from app.embeddings import get_embeddings
        emb = get_embeddings()
        v1 = emb.embed_query(QUERY)
        v2 = emb.embed_query(QUERY)
        assert v1 == v2

    def test_different_texts_produce_different_vectors(self):
        from app.embeddings import get_embeddings
        emb = get_embeddings()
        v1 = emb.embed_query("total assets")
        v2 = emb.embed_query("net profit")
        assert v1 != v2

    def test_embed_documents_returns_correct_count(self):
        from app.embeddings import get_embeddings
        emb = get_embeddings()
        vecs = emb.embed_documents(DOCS)
        assert len(vecs) == len(DOCS)
        for vec in vecs:
            assert len(vec) == 768

    def test_vector_is_unit_norm(self):
        from app.embeddings import get_embeddings
        emb = get_embeddings()
        vec = emb.embed_query(QUERY)
        norm = sum(v ** 2 for v in vec) ** 0.5
        assert abs(norm - 1.0) < 1e-6

    def test_embed_documents_is_deterministic(self):
        from app.embeddings import get_embeddings
        emb = get_embeddings()
        vecs1 = emb.embed_documents(DOCS)
        vecs2 = emb.embed_documents(DOCS)
        assert vecs1 == vecs2


# ─────────────────────────────────────────────────────────────────────────────
# Retry wrapper – simulated 429 errors
# ─────────────────────────────────────────────────────────────────────────────

class TestRetryWrapper:
    """
    Verifies that 429 / rate-limit errors are retried and eventually succeed,
    and that non-retryable errors propagate immediately.
    """

    @pytest.fixture(autouse=True)
    def _use_fake(self, monkeypatch):
        monkeypatch.setattr(settings, "embedding_provider", "fake")
        monkeypatch.setattr(settings, "embedding_batch_pause_seconds", 0.0)

    def test_429_is_retried_and_succeeds(self, monkeypatch):
        """
        Simulate two consecutive 429 errors followed by a successful response.
        The wrapper must retry and return the final successful result.
        """
        from app.embeddings import _FakeEmbeddings, _RetryEmbeddings

        inner = _FakeEmbeddings()
        expected = inner.embed_query(QUERY)

        call_count = {"n": 0}

        def flaky_embed_query(text):
            call_count["n"] += 1
            if call_count["n"] <= 2:
                raise Exception("429 Too Many Requests: quota exceeded")
            return expected

        # Patch sleep so the test doesn't actually wait
        monkeypatch.setattr(time, "sleep", lambda _: None)

        wrapped = _RetryEmbeddings(inner, max_retries=5)
        monkeypatch.setattr(wrapped._inner, "embed_query", flaky_embed_query)

        result = wrapped.embed_query(QUERY)
        assert result == expected
        assert call_count["n"] == 3   # 2 failures + 1 success

    def test_rate_limit_phrase_triggers_retry(self, monkeypatch):
        """Various rate-limit error messages must all trigger a retry."""
        from app.embeddings import _FakeEmbeddings, _RetryEmbeddings

        monkeypatch.setattr(time, "sleep", lambda _: None)

        for error_msg in [
            "429",
            "resource exhausted",
            "too many requests",
            "quota exceeded",
            "503 service unavailable",
        ]:
            inner = _FakeEmbeddings()
            # Capture the *original* bound method BEFORE monkeypatching so the
            # fallback call inside fn() doesn't recurse into the patched version.
            original_embed = inner.embed_query.__func__  # unbound
            expected = original_embed(inner, QUERY)

            attempts = {"n": 0}

            def make_flaky_fn(msg=error_msg, _inner=inner, _orig=original_embed):
                def fn(text):
                    attempts["n"] += 1
                    if attempts["n"] == 1:
                        raise Exception(msg)
                    return _orig(_inner, text)   # call original, not the patch
                return fn

            wrapped = _RetryEmbeddings(inner, max_retries=3)
            monkeypatch.setattr(wrapped._inner, "embed_query", make_flaky_fn())
            result = wrapped.embed_query(QUERY)
            assert result == expected, f"Failed for error: {error_msg!r}"

    def test_non_retryable_error_propagates_immediately(self, monkeypatch):
        """A ValueError (bad input, not a rate limit) must NOT be retried."""
        from app.embeddings import _FakeEmbeddings, _RetryEmbeddings

        inner = _FakeEmbeddings()
        monkeypatch.setattr(time, "sleep", lambda _: None)

        call_count = {"n": 0}

        def bad_embed(text):
            call_count["n"] += 1
            raise ValueError("invalid text input")

        wrapped = _RetryEmbeddings(inner, max_retries=5)
        monkeypatch.setattr(wrapped._inner, "embed_query", bad_embed)

        with pytest.raises(ValueError, match="invalid text input"):
            wrapped.embed_query(QUERY)

        # Must have been called exactly once (no retries)
        assert call_count["n"] == 1

    def test_exhausted_retries_raise_runtime_error(self, monkeypatch):
        """After max_retries retryable failures, RuntimeError is raised."""
        from app.embeddings import _FakeEmbeddings, _RetryEmbeddings

        inner = _FakeEmbeddings()
        monkeypatch.setattr(time, "sleep", lambda _: None)

        def always_429(text):
            raise Exception("429 rate limited forever")

        wrapped = _RetryEmbeddings(inner, max_retries=2)
        monkeypatch.setattr(wrapped._inner, "embed_query", always_429)

        with pytest.raises(RuntimeError, match="failed after 2 retries"):
            wrapped.embed_query(QUERY)

    def test_batch_pause_is_applied(self, monkeypatch):
        """embed_documents() must sleep for EMBEDDING_BATCH_PAUSE_SECONDS."""
        from app.embeddings import _FakeEmbeddings, _RetryEmbeddings

        inner = _FakeEmbeddings()
        monkeypatch.setattr(settings, "embedding_batch_pause_seconds", 1.5)

        sleep_calls = []
        monkeypatch.setattr(time, "sleep", lambda s: sleep_calls.append(s))

        wrapped = _RetryEmbeddings(inner)
        wrapped.embed_documents(DOCS)

        assert any(abs(s - 1.5) < 0.01 for s in sleep_calls), (
            f"Expected a 1.5s batch pause; sleep calls were: {sleep_calls}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Fingerprint
# ─────────────────────────────────────────────────────────────────────────────

class TestEmbeddingFingerprint:

    def test_fake_fingerprint(self, monkeypatch):
        monkeypatch.setattr(settings, "embedding_provider", "fake")
        monkeypatch.setattr(settings, "embedding_model", "fake")
        from app.embeddings import embedding_fingerprint
        fp = embedding_fingerprint()
        assert fp == "fake:fake:768"

    def test_gemini_fingerprint(self, monkeypatch):
        monkeypatch.setattr(settings, "embedding_provider", "gemini")
        monkeypatch.setattr(settings, "embedding_model", "gemini-embedding-001")
        from app.embeddings import embedding_fingerprint
        fp = embedding_fingerprint()
        assert fp == "gemini:gemini-embedding-001:3072"

    def test_fingerprint_is_stable(self, monkeypatch):
        """Calling fingerprint() twice returns the same string."""
        monkeypatch.setattr(settings, "embedding_provider", "fake")
        monkeypatch.setattr(settings, "embedding_model", "fake")
        from app.embeddings import embedding_fingerprint
        assert embedding_fingerprint() == embedding_fingerprint()

    def test_fingerprint_format(self, monkeypatch):
        """Fingerprint must be <provider>:<model>:<dimension>."""
        monkeypatch.setattr(settings, "embedding_provider", "fake")
        monkeypatch.setattr(settings, "embedding_model", "fake")
        from app.embeddings import embedding_fingerprint
        parts = embedding_fingerprint().split(":")
        assert len(parts) == 3
        assert parts[2].isdigit()


# ─────────────────────────────────────────────────────────────────────────────
# Live Gemini test
# ─────────────────────────────────────────────────────────────────────────────

def _gemini_available() -> bool:
    return (
        settings.embedding_provider == "gemini"
        and bool(settings.google_api_key)
    )


@pytest.mark.skipif(not _gemini_available(), reason="Gemini API key not configured")
class TestGeminiProvider:
    """
    Live integration test — makes a real API call to Gemini.
    Requires GOOGLE_API_KEY and EMBEDDING_PROVIDER=gemini in .env.
    """

    @pytest.fixture(autouse=True)
    def _no_batch_pause(self, monkeypatch):
        monkeypatch.setattr(settings, "embedding_batch_pause_seconds", 0.0)

    def test_embed_query_returns_vector(self):
        from app.embeddings import get_embeddings
        emb = get_embeddings()
        vec = emb.embed_query(QUERY)
        assert isinstance(vec, list)
        assert len(vec) > 0
        assert all(isinstance(v, float) for v in vec)

    def test_embed_query_dimension_matches_fingerprint(self):
        from app.embeddings import embedding_fingerprint, get_embeddings
        emb = get_embeddings()
        vec = emb.embed_query(QUERY)
        fp = embedding_fingerprint()
        declared_dim = int(fp.split(":")[-1])
        assert len(vec) == declared_dim, (
            f"Vector dim {len(vec)} != fingerprint dim {declared_dim}"
        )

    def test_embed_documents_returns_correct_count(self):
        from app.embeddings import get_embeddings
        emb = get_embeddings()
        vecs = emb.embed_documents(DOCS[:2])   # small batch to save quota
        assert len(vecs) == 2

    def test_gemini_fingerprint_format(self):
        from app.embeddings import embedding_fingerprint
        fp = embedding_fingerprint()
        assert fp.startswith("gemini:")
        parts = fp.split(":")
        assert len(parts) == 3
        assert int(parts[2]) > 0


class TestDailyQuotaDetection:
    def test_per_day_quota_is_detected(self):
        from app.embeddings import _is_daily_quota_error
        e = Exception("429 RESOURCE_EXHAUSTED EmbedContentRequestsPerDayPerProjectPerModel-FreeTier")
        assert _is_daily_quota_error(e)

    def test_long_retry_delay_is_detected(self):
        from app.embeddings import _is_daily_quota_error
        assert _is_daily_quota_error(Exception("429 quota exceeded. retryDelay: '40000s'"))

    def test_short_retry_delay_is_not_daily(self):
        from app.embeddings import _is_daily_quota_error
        assert not _is_daily_quota_error(Exception("429 rate limit. retryDelay: '12s'"))

    def test_daily_quota_stops_without_retry(self):
        from unittest.mock import MagicMock
        from app.embeddings import QuotaExhaustedError, _RetryEmbeddings
        inner = MagicMock()
        inner.embed_documents.side_effect = Exception("429 ...PerDay...")
        with pytest.raises(QuotaExhaustedError):
            _RetryEmbeddings(inner).embed_documents(["a"])
        assert inner.embed_documents.call_count == 1
