"""
app/page_render.py
─────────────────────────────────────────────────────────────────────────────
Render one page of a stored PDF as a PNG so the website can show the exact
page a number came from. The PDF is downloaded from storage once and kept in
a temp folder; rendered pages are cached next to it.
"""

from __future__ import annotations

import hashlib
import re
import tempfile
import threading
import uuid
from pathlib import Path

import pymupdf as fitz

from app import storage

_ROOT = Path(tempfile.gettempdir()) / "fin_research_pages"
_lock = threading.Lock()
_ZOOM = 1.6


def _pdf_path(document_id: uuid.UUID, storage_key: str) -> Path:
    folder = _ROOT / str(document_id)
    folder.mkdir(parents=True, exist_ok=True)
    pdf = folder / "source.pdf"
    if not pdf.exists():
        tmp = storage.download_to_temp(storage_key)
        try:
            pdf.write_bytes(Path(tmp).read_bytes())
        finally:
            Path(tmp).unlink(missing_ok=True)
    return pdf


def _highlight(page: "fitz.Page", text: str) -> None:
    """Mark the cited line on the page. Tries the whole line, then its longest
    number, because the saved line may differ from the PDF in spacing."""
    text = " ".join(text.split())
    candidates = [text]
    numbers = re.findall(r"\(?\d[\d,]*\.?\d*\)?", text)
    numbers = sorted({n.strip("()") for n in numbers if len(n.strip("()")) >= 3}, key=len, reverse=True)
    candidates += numbers[:2]
    for cand in candidates:
        rects = page.search_for(cand)
        if rects:
            for r in rects[:4]:
                page.add_highlight_annot(r)
            return


def render_page(document_id: uuid.UUID, storage_key: str, page_number: int, highlight: str | None = None) -> bytes:
    """PNG bytes for 1-indexed ``page_number``. Raises FileNotFoundError when
    the PDF is gone, IndexError when the page does not exist."""
    key = hashlib.sha1((highlight or "").encode()).hexdigest()[:10]
    cache = _ROOT / str(document_id) / f"p{page_number}_{key}.png"
    if cache.exists():
        return cache.read_bytes()
    with _lock:
        pdf = _pdf_path(document_id, storage_key)
        with fitz.open(pdf) as doc:
            if page_number < 1 or page_number > doc.page_count:
                raise IndexError(page_number)
            page = doc[page_number - 1]
            if highlight:
                _highlight(page, highlight)
            data = page.get_pixmap(matrix=fitz.Matrix(_ZOOM, _ZOOM)).tobytes("png")
    cache.write_bytes(data)
    return data
