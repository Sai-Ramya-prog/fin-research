"""
app/chunk.py
───────────────────────────────────────────────────────────────────────
Chunking – Document Agent, Node 3.

We receive the raw page text from :pymod:`app.parse` (``PageResult``) and the
corresponding tagging information from :pymod:`app.tag` (``TagResult``).
The goal is to turn each page into one or more *chunks* suitable for storage in
Chroma (or any vector‑store).  Chunk metadata must be safe for Chroma – keys are
lower‑case, use underscores, contain no dots or spaces.

Rules
-----
* **Table / statement pages** (``section`` in ``STATEMENT_SECTIONS``)
  * One chunk per page by default.
  * If the raw text exceeds ``MAX_CHUNK_BYTES`` (16 KB) we first collapse
    consecutive whitespace to a single space and then split on line breaks so
    that each chunk is ≤``MAX_CHUNK_BYTES``.
* **Narrative pages** – use LangChain's ``RecursiveCharacterTextSplitter`` with a
  chunk size of 4 KB and an overlap of 200 bytes (standard for prose).
* Every chunk carries the same metadata fields – they are derived from the
  matching ``TagResult`` and the original ``PageResult``:

  ``pdf_name``         – the source PDF filename (no path)
  ``pdf_page``         – 1‑based page number
  ``printed_page``     – printed page number if detected
  ``section``          – tag section (e.g. ``balance_sheet``)
  ``scope``            – ``consolidated``/``standalone``/``unknown``
  ``unit``             – currency/unit string (may be empty)
  ``content_type``     – ``table`` or ``narrative``
  ``source_type``      – ``table`` for statement pages, ``narrative`` otherwise

The function ``chunk_pages`` returns a list of dictionaries, each with the
keys ``text`` and ``metadata`` ready to be fed to a Chroma collection.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import List, Dict

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.parse import PageResult
from app.tag import (
    TagResult,
    STATEMENT_SECTIONS,
    SECTION_OTHER,
    SECTION_BALANCE_SHEET,
    SECTION_PROFIT_LOSS,
    SECTION_CASH_FLOW,
    SECTION_EQUITY_CHANGES,
    SECTION_AUDITOR_REPORT,
    SECTION_NOTES,
    SECTION_MDNA,
)

# ---------------------------------------------------------------------------
# Configuration – keep values small enough for quick test runs.
# ---------------------------------------------------------------------------
MAX_CHUNK_BYTES = 16 * 1024  # 16 KB – maximum size for a *table* chunk.
NARRATIVE_CHUNK_SIZE = 4 * 1024  # 4 KB typical for prose.
NARRATIVE_CHUNK_OVERLAP = 200

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _collapse_whitespace(text: str) -> str:
    """Shrink whitespace but KEEP line breaks.

    Row structure matters in financial tables ("Total assets  1,23,456  1,10,000"
    must stay on one line), so only runs of spaces/tabs are shortened and blank
    lines dropped. Newlines are never turned into spaces.
    """
    lines = []
    for line in text.splitlines():
        line = re.sub(r"[ \t\u00a0]{3,}", "  ", line.replace("\t", " ")).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _split_large_table(text: str) -> List[str]:
    """Split a large table page into pieces of at most ``MAX_CHUNK_BYTES`` bytes,
    always on line boundaries so a row is never cut in half."""
    collapsed = _collapse_whitespace(text)
    if len(collapsed.encode("utf-8")) <= MAX_CHUNK_BYTES:
        return [collapsed]
    chunks: List[str] = []
    current: List[str] = []
    current_len = 0
    for line in collapsed.split("\n"):
        line_bytes = len(line.encode("utf-8")) + 1  # +1 for the newline
        if current_len + line_bytes > MAX_CHUNK_BYTES and current:
            chunks.append("\n".join(current))
            current = []
            current_len = 0
        current.append(line)
        current_len += line_bytes
    if current:
        chunks.append("\n".join(current))
    return chunks


def _metadata_from(tag: TagResult, page: PageResult, pdf_name: str) -> Dict[str, str]:
    """Create a Chroma‑safe metadata dictionary.
    All keys are lower‑case, use underscores and contain only alphanumerics.
    ``pdf_name`` is the filename without directory components.
    """
    def safe(v: str | None) -> str:
        return "" if v is None else str(v)

    return {
        "pdf_name": os.path.basename(pdf_name),
        "pdf_page": str(page.pdf_page),
        "printed_page": safe(tag.printed_page),
        "section": tag.section,
        "scope": tag.scope,
        "unit": tag.unit,
        "content_type": tag.content_type,
        "source_type": "table" if tag.section in STATEMENT_SECTIONS else "narrative",
        "tier": getattr(tag, "tier", 2),
    }

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def chunk_pages(
    pages: List[PageResult],
    tags: List[TagResult],
    pdf_name: str = "",
) -> List[Dict[str, object]]:
    """Chunk a PDF's pages according to the rules described in the module docstring.

    Parameters
    ----------
    pages:
        List of ``PageResult`` objects in the same order as the PDF pages.
    tags:
        Corresponding list of ``TagResult`` objects – must be the same length as
        ``pages``.
    pdf_name:
        Optional name of the source PDF (used only for metadata).

    Returns
    -------
    List[Dict]
        Each dict has ``"text"`` (the chunk content) and ``"metadata"`` (a dict).
    """
    if len(pages) != len(tags):
        raise ValueError("pages and tags must have identical length")

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=NARRATIVE_CHUNK_SIZE,
        chunk_overlap=NARRATIVE_CHUNK_OVERLAP,
        separators=["\n\n", "\n", " "],
    )

    result: List[Dict[str, object]] = []

    for page, tag in zip(pages, tags):
        # Narrative vs statement decision is based on the tag's section.
        if tag.section in STATEMENT_SECTIONS:
            # Table / statement page – single‑or‑split chunk.
            chunks = _split_large_table(page.text)
            for chunk_text in chunks:
                result.append({
                    "text": chunk_text,
                    "metadata": _metadata_from(tag, page, pdf_name),
                })
        else:
            # Narrative – let LangChain handle the splitting.
            for chunk_text in splitter.split_text(page.text):
                result.append({
                    "text": chunk_text,
                    "metadata": _metadata_from(tag, page, pdf_name),
                })

    return result

# ---------------------------------------------------------------------------
# When executed as a script we perform a quick sanity check on a sample PDF.
# ---------------------------------------------------------------------------
if __name__ == "__main__":  # pragma: no cover
    import argparse

    parser = argparse.ArgumentParser(description="Chunk a PDF using parse+tag modules")
    parser.add_argument("pdf_path", type=Path, help="Path to the PDF to process")
    args = parser.parse_args()

    from app.parse import parse_fast
    from app.tag import tag_pages

    pages = parse_fast(str(args.pdf_path))
    tags = tag_pages(pages, pdf_name=args.pdf_path.name)
    chunks = chunk_pages(pages, tags, pdf_name=args.pdf_path.name)
    print(f"Generated {len(chunks)} chunks from {len(pages)} pages")
