"""
tests/test_parse.py
─────────────────────────────────────────────────────────────────────────────
Task 4 acceptance tests for app/parse.py.

Test classes
────────────
  TestParseFastUnit       – synthetic PDFs, no seed file needed.
  TestParseLayoutUnit     – synthetic PDFs, no seed file needed.
  TestParseFastInfosys    – real Infosys PDF (skipped if file missing).
  TestParseLayoutInfosys  – real Infosys PDF (skipped if file missing).

Run with:
    pytest tests/test_parse.py -v
"""

from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path

import pymupdf as fitz  # PyMuPDF 2.x (fitz alias deprecated)
import pytest

# ─── path to the seed PDF ─────────────────────────────────────────────────────
_INFOSYS_PDF = (
    Path(__file__).parent.parent / "data" / "seed" / "infosys_fy2025.pdf.pdf"
)
_INFOSYS_AVAILABLE = _INFOSYS_PDF.exists()

# Page 274 of the Infosys PDF is the Balance Sheet (standalone).
_BS_PAGE = 274
_LAST_PAGE = 369


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_text_pdf(texts: list[str]) -> str:
    """
    Create a temporary PDF with one page per text string.
    Returns the temp file path; caller must delete it.
    """
    doc = fitz.open()
    for text in texts:
        page = doc.new_page()
        if text:
            page.insert_text((72, 100), text, fontsize=11)
    fd, path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    doc.save(path)
    doc.close()
    return path


# ─────────────────────────────────────────────────────────────────────────────
# Unit tests – synthetic PDFs
# ─────────────────────────────────────────────────────────────────────────────

class TestParseFastUnit:

    def test_page_count(self):
        from app.parse import parse_fast
        path = _make_text_pdf(["Hello world " * 10, "Page two " * 10, ""])
        try:
            results = parse_fast(path)
            assert len(results) == 3
        finally:
            os.unlink(path)

    def test_pdf_page_is_one_indexed(self):
        from app.parse import parse_fast
        path = _make_text_pdf(["Some text " * 10])
        try:
            results = parse_fast(path)
            assert results[0].pdf_page == 1
        finally:
            os.unlink(path)

    def test_has_text_true_for_rich_page(self):
        from app.parse import parse_fast
        # 50+ chars of non-whitespace
        rich_text = "Financial data " * 10
        path = _make_text_pdf([rich_text])
        try:
            results = parse_fast(path)
            assert results[0].has_text is True
            assert results[0].is_image_only is False
        finally:
            os.unlink(path)

    def test_has_text_false_for_empty_page(self):
        from app.parse import parse_fast
        path = _make_text_pdf([""])   # blank page
        try:
            results = parse_fast(path)
            assert results[0].has_text is False
            assert results[0].is_image_only is True
        finally:
            os.unlink(path)

    def test_has_text_false_for_short_text(self):
        from app.parse import parse_fast
        path = _make_text_pdf(["Hi"])  # < 50 chars
        try:
            results = parse_fast(path)
            assert results[0].has_text is False
            assert results[0].is_image_only is True
        finally:
            os.unlink(path)

    def test_text_content_returned(self):
        from app.parse import parse_fast
        content = "Total assets and liabilities summary " * 5
        path = _make_text_pdf([content])
        try:
            results = parse_fast(path)
            assert "Total assets" in results[0].text
        finally:
            os.unlink(path)

    def test_multiple_pages_ordered(self):
        from app.parse import parse_fast
        texts = [f"Page content number {i} " * 10 for i in range(5)]
        path = _make_text_pdf(texts)
        try:
            results = parse_fast(path)
            assert [r.pdf_page for r in results] == [1, 2, 3, 4, 5]
        finally:
            os.unlink(path)

    def test_error_result_never_crashes(self, monkeypatch):
        """
        Even if page.get_text raises, parse_fast must return a PageResult
        with error set and not raise.
        """
        import fitz as _fitz
        from app.parse import parse_fast

        original_open = _fitz.open

        class BadPage:
            def get_text(self, *a, **kw):
                raise RuntimeError("corrupt page")
            def get_images(self, *a, **kw):
                return []

        class FakeDoc:
            def __len__(self): return 2
            def __getitem__(self, idx):
                if idx == 1:
                    return BadPage()
                page = original_open(_make_text_pdf(["Good page " * 10]))[0]
                return page
            def close(self): pass

        monkeypatch.setattr(_fitz, "open", lambda *a, **kw: FakeDoc())
        results = parse_fast("fake.pdf")
        assert len(results) == 2
        # Page 2 must have an error recorded, not raise
        assert results[1].error is not None


class TestParseLayoutUnit:

    def test_returns_dict_with_requested_pages(self):
        from app.parse import parse_layout
        texts = [f"Financial statement line {i} revenue profit " * 5
                 for i in range(5)]
        path = _make_text_pdf(texts)
        try:
            result = parse_layout(path, [1, 3])
            assert set(result.keys()) == {1, 3}
        finally:
            os.unlink(path)

    def test_text_content_present(self):
        from app.parse import parse_layout
        content = "Total assets equity shareholders " * 8
        path = _make_text_pdf([content])
        try:
            result = parse_layout(path, [1])
            assert "Total assets" in result[1]
        finally:
            os.unlink(path)

    def test_unrequested_pages_absent(self):
        from app.parse import parse_layout
        path = _make_text_pdf(["Page one " * 10, "Page two " * 10])
        try:
            result = parse_layout(path, [1])
            assert 2 not in result
        finally:
            os.unlink(path)

    def test_empty_page_numbers_returns_empty(self):
        from app.parse import parse_layout
        path = _make_text_pdf(["Some content " * 10])
        try:
            result = parse_layout(path, [])
            assert result == {}
        finally:
            os.unlink(path)


# ─────────────────────────────────────────────────────────────────────────────
# Integration tests – real Infosys PDF
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.skipif(
    not _INFOSYS_AVAILABLE,
    reason=f"Infosys PDF not found at {_INFOSYS_PDF}",
)
class TestParseFastInfosys:

    @pytest.fixture(scope="class")
    @classmethod
    def results(cls):
        from app.parse import parse_fast
        return parse_fast(str(_INFOSYS_PDF))

    def test_page_count_is_369(self, results):
        assert len(results) == _LAST_PAGE, (
            f"Expected {_LAST_PAGE} pages, got {len(results)}"
        )

    def test_all_pages_one_indexed(self, results):
        for i, r in enumerate(results):
            assert r.pdf_page == i + 1

    def test_last_page_is_image_only(self, results):
        last = results[_LAST_PAGE - 1]
        assert last.is_image_only is True, (
            f"Page {_LAST_PAGE} expected image-only but has_text={last.has_text}, "
            f"text_len={len(last.text.strip())}"
        )

    def test_first_page_has_text(self, results):
        assert results[0].has_text is True

    def test_no_exceptions_on_any_page(self, results):
        errors = [(r.pdf_page, r.error) for r in results if r.error]
        assert errors == [], f"Pages with errors: {errors}"

    def test_image_only_count_reasonable(self, results):
        # Some pages should be image-only (charts, scanned pages)
        image_only_count = sum(1 for r in results if r.is_image_only)
        assert image_only_count >= 1, "Expected at least one image-only page"


@pytest.mark.skipif(
    not _INFOSYS_AVAILABLE,
    reason=f"Infosys PDF not found at {_INFOSYS_PDF}",
)
class TestParseLayoutInfosys:

    def test_balance_sheet_page_contains_total_assets(self):
        """
        Page 274 is the standalone Balance Sheet.
        layout=True extraction must keep 'Total assets' and '1,48,903'
        on the same line.
        """
        from app.parse import parse_layout
        result = parse_layout(str(_INFOSYS_PDF), [_BS_PAGE])
        text = result.get(_BS_PAGE, "")

        assert "Total assets" in text, (
            f"'Total assets' not found on page {_BS_PAGE}"
        )
        assert "1,48,903" in text, (
            f"'1,48,903' not found on page {_BS_PAGE}.  "
            "Check that layout=True is preserving column alignment."
        )

        # Verify both tokens are on the same line
        lines_with_total_assets = [
            line for line in text.splitlines()
            if "Total assets" in line
        ]
        assert any("1,48,903" in line for line in lines_with_total_assets), (
            f"'Total assets' and '1,48,903' are not on the same line.\n"
            f"Lines containing 'Total assets': {lines_with_total_assets}"
        )

    def test_layout_extracts_only_requested_pages(self):
        from app.parse import parse_layout
        result = parse_layout(str(_INFOSYS_PDF), [_BS_PAGE])
        assert set(result.keys()) == {_BS_PAGE}
