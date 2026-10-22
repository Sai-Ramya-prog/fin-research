"""
app/storage.py
─────────────────────────────────────────────────────────────────────────────
Cloud object-storage abstraction.

Two backends are supported, selected by STORAGE_BACKEND in .env:

  remote  – S3-compatible bucket via boto3 (Supabase Storage, AWS S3, etc.)
  local   – writes to a temp directory on disk; used ONLY for tests / dry-runs

Public API
──────────
  save(fileobj, key)              → storage_key  (str)
  download_to_temp(storage_key)   → local_path   (str)   caller must delete
  delete(storage_key)             → None
  exists(storage_key)             → bool
  make_storage_key(document_id, filename) → str  (convenience helper)

Rules enforced
──────────────
  - STORAGE_ENDPOINT_URL must be set for the remote backend.
    We never fall back to AWS defaults – missing config raises immediately.
  - The bucket is kept private.  Presigned URLs or server-side streaming
    must be used to serve files; raw bucket URLs are never returned.
  - Key format: documents/{document_id}/{safe_filename}
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import BinaryIO

import boto3
import botocore.exceptions

from app.config import settings


# ── helpers ───────────────────────────────────────────────────────────────────

def _safe_filename(filename: str) -> str:
    """
    Strip path components and replace any character that is not
    alphanumeric, a dot, a dash, or an underscore with '_'.
    This prevents directory-traversal attacks and S3 key oddities.
    """
    name = Path(filename).name          # drop any path prefix
    name = re.sub(r"[^\w.\-]", "_", name)
    return name or "file"


def make_storage_key(document_id: str | uuid.UUID, filename: str) -> str:
    """
    Build the canonical storage key for a document file.
    Format: ``documents/{document_id}/{safe_filename}``
    """
    return f"documents/{document_id}/{_safe_filename(filename)}"


# ── remote backend (S3-compatible) ────────────────────────────────────────────

def _require_remote_config() -> None:
    """Fail fast with a clear message if any remote-storage setting is missing."""
    missing = []
    if not settings.storage_endpoint_url:
        missing.append("STORAGE_ENDPOINT_URL")
    if not settings.storage_bucket:
        missing.append("STORAGE_BUCKET")
    if not settings.storage_access_key_id:
        missing.append("STORAGE_ACCESS_KEY_ID")
    if not settings.storage_secret_access_key:
        missing.append("STORAGE_SECRET_ACCESS_KEY")
    if missing:
        raise RuntimeError(
            f"Remote storage is not fully configured.  Missing: {', '.join(missing)}.\n"
            "  → Add the missing values to your .env file.\n"
            "  → Set STORAGE_BACKEND=local if you only want local (test) storage."
        )


def _s3_client():
    """
    Build a boto3 S3 client from config.
    endpoint_url is always required – we never fall back to AWS defaults.
    """
    _require_remote_config()
    return boto3.client(
        "s3",
        endpoint_url=settings.storage_endpoint_url,
        aws_access_key_id=settings.storage_access_key_id,
        aws_secret_access_key=settings.storage_secret_access_key,
        region_name=settings.storage_region or "us-east-1",
    )


def _remote_save(fileobj: BinaryIO, key: str) -> str:
    client = _s3_client()
    client.upload_fileobj(
        fileobj,
        settings.storage_bucket,
        key,
        ExtraArgs={"ACL": "private"},   # bucket is always private
    )
    return key


def _remote_download_to_temp(storage_key: str) -> str:
    client = _s3_client()
    suffix = Path(storage_key).suffix or ".bin"
    fd, local_path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    try:
        client.download_file(settings.storage_bucket, storage_key, local_path)
    except botocore.exceptions.ClientError as exc:
        os.unlink(local_path)
        code = exc.response["Error"]["Code"]
        raise FileNotFoundError(
            f"Storage key not found: {storage_key!r} (S3 error {code})"
        ) from exc
    return local_path


def _remote_delete(storage_key: str) -> None:
    client = _s3_client()
    client.delete_object(Bucket=settings.storage_bucket, Key=storage_key)


def _remote_exists(storage_key: str) -> bool:
    client = _s3_client()
    try:
        client.head_object(Bucket=settings.storage_bucket, Key=storage_key)
        return True
    except botocore.exceptions.ClientError as exc:
        if exc.response["Error"]["Code"] in ("404", "NoSuchKey"):
            return False
        raise


# ── local backend (tests / dry-runs only) ─────────────────────────────────────

class _LocalStorage:
    """
    Stores files under a single temp directory.
    The directory is created once on first use and persists for the
    lifetime of the process (or until the OS cleans up /tmp).
    """

    def __init__(self) -> None:
        self._root: Path | None = None

    def _get_root(self) -> Path:
        if self._root is None:
            self._root = Path(tempfile.mkdtemp(prefix="fin_research_storage_"))
        return self._root

    def save(self, fileobj: BinaryIO, key: str) -> str:
        dest = self._get_root() / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as f:
            shutil.copyfileobj(fileobj, f)
        return key

    def download_to_temp(self, storage_key: str) -> str:
        src = self._get_root() / storage_key
        if not src.exists():
            raise FileNotFoundError(f"Storage key not found: {storage_key!r}")
        suffix = src.suffix or ".bin"
        fd, local_path = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        shutil.copy2(src, local_path)
        return local_path

    def delete(self, storage_key: str) -> None:
        path = self._get_root() / storage_key
        if path.exists():
            path.unlink()

    def exists(self, storage_key: str) -> bool:
        return (self._get_root() / storage_key).exists()


_local_storage = _LocalStorage()


# ── public API ────────────────────────────────────────────────────────────────

def save(fileobj: BinaryIO, key: str) -> str:
    """
    Upload *fileobj* to storage under *key* and return the storage_key.

    Args:
        fileobj: Any file-like object opened in binary mode.
        key:     The storage key (path within the bucket / local root).
                 Use ``make_storage_key()`` to build a canonical key.

    Returns:
        The storage key (same as *key*) – stored in ``Document.storage_key``.
    """
    if settings.storage_backend == "local":
        return _local_storage.save(fileobj, key)
    return _remote_save(fileobj, key)


def download_to_temp(storage_key: str) -> str:
    """
    Download the file at *storage_key* to a temporary local path and
    return that path.

    **The caller is responsible for deleting the temp file** after use,
    whether the run succeeds or fails::

        local_path = storage.download_to_temp(doc.storage_key)
        try:
            process(local_path)
        finally:
            os.unlink(local_path)

    Raises:
        FileNotFoundError: if the key does not exist in storage.
    """
    if settings.storage_backend == "local":
        return _local_storage.download_to_temp(storage_key)
    return _remote_download_to_temp(storage_key)


def delete(storage_key: str) -> None:
    """Delete the file at *storage_key*.  Silent if the key does not exist."""
    if settings.storage_backend == "local":
        _local_storage.delete(storage_key)
    else:
        _remote_delete(storage_key)


def exists(storage_key: str) -> bool:
    """Return True if the file at *storage_key* exists in storage."""
    if settings.storage_backend == "local":
        return _local_storage.exists(storage_key)
    return _remote_exists(storage_key)
