"""
app/parse.py
─────────────────────────────────────────────────────────────────────────────
PDF parsing — Document Agent, Node 1.

Two-pass strategy
─────────────────
  Pass 1 – parse_fast(pdf_path)
      Uses PyMuPDF (fitz) to extract text and image counts from every page
      in one fast sweep.  Returns a list of PageResult objects.

  Pass 2 – parse_layout(pdf_path, page_numbers)
      Uses pdfplumber with layout=True extraction for a specific subset of
      pages (e.g. financial-statement pages identified by the tagger in Task 5).
      This is slower but keeps column alignment intact, which is critical for
      tables like the Balance Sheet.

Design decisions
────────────────
  - is_image_only  : pages where len(text.strip()) < 50 are considered image-
                     only scans.  They are flagged and skipped downstream (no OCR).
  - Per-page try/except in both passes: a corrupt page must never crash the run.
    Errors are recorded in PageResult.error and logged as warnings.
  - Storage integration: download_from_storage(storage_key) downloads the PDF
    to a temp file and returns (path, cleanup).  Callers must call cleanup()
    regardless of success or failure.

Public API
──────────
  parse_fast(pdf_path)                    → list[PageResult]
  parse_layout(pdf_path, page_numbers)    → dict[int, str]
  download_from_storage(storage_key)      → tuple[str, Callable]
"""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pymupdf as fitz  # PyMuPDF 2.x (fitz alias deprecated)

from app import storage

logger = logging.getLogger(__name__)

# A page is considered "has_text" if it has at least this many non-whitespace chars.
_MIN_TEXT_CHARS = 50


# ── Data types ─────────────────────────────────────────────────────────────────

@dataclass
class PageResult:
    """
    Result of parsing a single PDF page.

    Attributes:
        pdf_page     : 1-indexed page number.
        text         : Raw extracted text (may be empty for image-only pages).
        has_text     : True if len(text.strip()) >= 50.
        image_count  : Number of images embedded on the page.
        is_image_only: True when has_text is False (no usable text found).
        error        : Non-None if an exception occurred while parsing this page.
    """
    pdf_page: int
    text: str = ""
    has_text: bool = False
    image_count: int = 0
    is_image_only: bool = False
    error: str | None = None


# ── Storage integration ────────────────────────────────────────────────────────

def download_from_storage(storage_key: str) -> tuple[str, Callable[[], None]]:
    """
    Download a PDF from cloud storage to a temporary local file.

    Returns:
        (local_path, cleanup)  where *cleanup* is a zero-arg callable that
        deletes the temp file.  Always call cleanup() in a finally block::

            pdf_path, cleanup = download_from_storage(doc.storage_key)
            try:
                results = parse_fast(pdf_path)
            finally:
                cleanup()
    """
    local_path = storage.download_to_temp(storage_key)

    def cleanup() -> None:
        try:
            if os.path.exists(local_path):
                os.unlink(local_path)
        except OSError as exc:
            logger.warning("Could not delete temp file %s: %s", local_path, exc)

    return local_path, cleanup


# ── Pass 1: fast full-document parse ──────────────────────────────────────────

def parse_fast(pdf_path: str | Path) -> list[PageResult]:
    """
    Fast full-document parse using PyMuPDF.

    Iterates every page, extracts plain text and counts embedded images.
    Each page is wrapped in try/except so a corrupt page never aborts the run.

    Args:
        pdf_path: Absolute or relative path to the PDF file.

    Returns:
        A list of :class:`PageResult` objects, one per page, in order.
        ``pdf_page`` is 1-indexed.
    """
    pdf_path = str(pdf_path)
    results: list[PageResult] = []

    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:
        logger.error("Could not open PDF %s: %s", pdf_path, exc)
        raise

    try:
        for zero_idx in range(len(doc)):
            pdf_page = zero_idx + 1  # 1-indexed
            try:
                page = doc[zero_idx]
                text: str = page.get_text("text") or ""
                image_count: int = len(page.get_images(full=False))

                has_text = len(text.strip()) >= _MIN_TEXT_CHARS
                is_image_only = not has_text

                results.append(PageResult(
                    pdf_page=pdf_page,
                    text=text,
                    has_text=has_text,
                    image_count=image_count,
                    is_image_only=is_image_only,
                ))

                if is_image_only:
                    logger.debug(
                        "Page %d flagged as image-only "
                        "(text_len=%d, images=%d)",
                        pdf_page,
                        len(text.strip()),
                        image_count,
                    )

            except Exception as exc:
                logger.warning(
                    "Error parsing page %d of %s: %s",
                    pdf_page,
                    pdf_path,
                    exc,
                )
                results.append(PageResult(
                    pdf_page=pdf_page,
                    error=str(exc),
                    is_image_only=True,   # treat broken pages as unreadable
                ))
    finally:
        doc.close()

    logger.info(
        "parse_fast: %s → %d pages, %d image-only",
        Path(pdf_path).name,
        len(results),
        sum(1 for r in results if r.is_image_only),
    )
    return results


# ── Pass 2: layout-preserving parse for selected pages ────────────────────────

def parse_layout(
    pdf_path: str | Path,
    page_numbers: list[int],
) -> dict[int, str]:
    """
    Layout-preserving extraction for a specific subset of pages.

    Uses pdfplumber with ``layout=True`` so column spacing is preserved,
    which is essential for multi-column financial tables (Balance Sheet, P&L).

    Args:
        pdf_path    : Path to the PDF.
        page_numbers: 1-indexed page numbers to extract.

    Returns:
        A dict mapping page number → extracted text.
        Pages that fail to extract are recorded with an empty string and a
        warning is logged; they do NOT raise an exception.
    """
    import pdfplumber  # imported here to keep startup fast when not needed

    pdf_path = str(pdf_path)
    page_set = set(page_numbers)
    extracted: dict[int, str] = {}

    try:
        pdf = pdfplumber.open(pdf_path)
    except Exception as exc:
        logger.error("pdfplumber could not open %s: %s", pdf_path, exc)
        raise

    try:
        for zero_idx, page in enumerate(pdf.pages):
            pdf_page = zero_idx + 1
            if pdf_page not in page_set:
                continue
            try:
                text = page.extract_text(layout=True) or ""
                extracted[pdf_page] = text
            except Exception as exc:
                logger.warning(
                    "parse_layout error on page %d of %s: %s",
                    pdf_page,
                    pdf_path,
                    exc,
                )
                extracted[pdf_page] = ""
    finally:
        pdf.close()

    logger.info(
        "parse_layout: %s → extracted %d/%d pages",
        Path(pdf_path).name,
        len(extracted),
        len(page_numbers),
    )
    return extracted
