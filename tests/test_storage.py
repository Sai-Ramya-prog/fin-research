"""
tests/test_storage.py
─────────────────────────────────────────────────────────────────────────────
Task 2b acceptance tests for app/storage.py.

Two test classes:

  TestLocalStorage  – always runs; uses STORAGE_BACKEND=local so no cloud
                      credentials are needed.
  TestRemoteStorage – uses the real Supabase S3 bucket from .env.
                      Skipped automatically when STORAGE_BACKEND != "remote"
                      or when STORAGE_ENDPOINT_URL is not set.

Run with:
    pytest tests/test_storage.py -v
"""

from __future__ import annotations

import io
import os
import uuid

import pytest

import app.storage as storage
from app.storage import make_storage_key


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

SAMPLE_CONTENT = b"Hello, fin-research storage!\nLine 2.\n" * 100   # ~3.7 KB


def _unique_key(suffix: str = ".pdf") -> str:
    doc_id = uuid.uuid4()
    return make_storage_key(doc_id, f"test_report{suffix}")


# ─────────────────────────────────────────────────────────────────────────────
# Local backend tests  (no credentials needed)
# ─────────────────────────────────────────────────────────────────────────────

class TestLocalStorage:
    """
    These tests force STORAGE_BACKEND=local via monkeypatching so they
    always pass without any cloud credentials.
    """

    @pytest.fixture(autouse=True)
    def _use_local(self, monkeypatch):
        from app import config as cfg
        monkeypatch.setattr(cfg.settings, "storage_backend", "local")
        # Also reset the local storage root between tests so they are isolated.
        from app.storage import _local_storage
        _local_storage._root = None
        yield

    def test_save_and_exists(self):
        key = _unique_key()
        fileobj = io.BytesIO(SAMPLE_CONTENT)
        returned_key = storage.save(fileobj, key)
        assert returned_key == key
        assert storage.exists(key)

    def test_download_bytes_identical(self):
        key = _unique_key()
        storage.save(io.BytesIO(SAMPLE_CONTENT), key)

        local_path = storage.download_to_temp(key)
        try:
            with open(local_path, "rb") as f:
                data = f.read()
            assert data == SAMPLE_CONTENT
        finally:
            os.unlink(local_path)

    def test_delete_then_not_exists(self):
        key = _unique_key()
        storage.save(io.BytesIO(SAMPLE_CONTENT), key)
        assert storage.exists(key)

        storage.delete(key)
        assert not storage.exists(key)

    def test_exists_false_for_unknown_key(self):
        assert not storage.exists("documents/nonexistent/file.pdf")

    def test_delete_nonexistent_is_silent(self):
        # Must not raise
        storage.delete("documents/nonexistent/noop.pdf")

    def test_download_missing_raises_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            storage.download_to_temp("documents/nonexistent/missing.pdf")

    def test_make_storage_key_format(self):
        doc_id = uuid.uuid4()
        key = make_storage_key(doc_id, "Annual Report 2024.pdf")
        assert key.startswith(f"documents/{doc_id}/")
        # safe_filename must have replaced the space
        assert " " not in key

    def test_make_storage_key_strips_path_traversal(self):
        doc_id = uuid.uuid4()
        key = make_storage_key(doc_id, "../../etc/passwd")
        # Should only keep the basename part
        assert "etc" not in key
        assert ".." not in key

    def test_save_returns_exact_key(self):
        doc_id = uuid.uuid4()
        key = make_storage_key(doc_id, "report.pdf")
        result = storage.save(io.BytesIO(b"data"), key)
        assert result == key

    def test_multiple_files_independent(self):
        key1 = _unique_key()
        key2 = _unique_key()
        storage.save(io.BytesIO(b"file one"), key1)
        storage.save(io.BytesIO(b"file two"), key2)

        assert storage.exists(key1)
        assert storage.exists(key2)

        storage.delete(key1)
        assert not storage.exists(key1)
        assert storage.exists(key2)  # key2 untouched


# ─────────────────────────────────────────────────────────────────────────────
# Remote backend tests  (real Supabase S3 bucket)
# ─────────────────────────────────────────────────────────────────────────────

def _remote_available() -> bool:
    from app.config import settings
    return (
        settings.storage_backend == "remote"
        and bool(settings.storage_endpoint_url)
        and bool(settings.storage_access_key_id)
        and bool(settings.storage_secret_access_key)
        and bool(settings.storage_bucket)
    )


@pytest.mark.skipif(not _remote_available(), reason="Remote storage not configured")
class TestRemoteStorage:
    """
    Integration tests against the real Supabase S3 bucket.
    Each test uploads to a unique key and always cleans up after itself.
    """

    def test_save_and_exists(self):
        key = _unique_key()
        try:
            storage.save(io.BytesIO(SAMPLE_CONTENT), key)
            assert storage.exists(key)
        finally:
            storage.delete(key)

    def test_download_bytes_identical(self):
        key = _unique_key()
        storage.save(io.BytesIO(SAMPLE_CONTENT), key)
        local_path = None
        try:
            local_path = storage.download_to_temp(key)
            with open(local_path, "rb") as f:
                data = f.read()
            assert data == SAMPLE_CONTENT
        finally:
            if local_path and os.path.exists(local_path):
                os.unlink(local_path)
            storage.delete(key)

    def test_delete_then_not_exists(self):
        key = _unique_key()
        storage.save(io.BytesIO(SAMPLE_CONTENT), key)
        assert storage.exists(key)
        storage.delete(key)
        assert not storage.exists(key)

    def test_exists_false_for_unknown_key(self):
        key = f"documents/{uuid.uuid4()}/this_file_does_not_exist.pdf"
        assert not storage.exists(key)

    def test_delete_nonexistent_is_silent(self):
        key = f"documents/{uuid.uuid4()}/noop.pdf"
        # Must not raise
        storage.delete(key)
