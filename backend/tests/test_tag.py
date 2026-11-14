"""
tests/test_tag.py
─────────────────────────────────────────────────────────────────────────────
Task 5 acceptance tests for app/tag.py.

Test classes
────────────
  TestTagUnit          – synthetic PageResults, no PDF needed.
  TestTagInfosys       – real Infosys PDF (skipped if file missing).
  TestTagTataMotors    – real Tata Motors PDF (skipped if file missing).
  TestTagReliance      – real Reliance PDF (skipped if file missing).
  TestTagMahindra      – real M&M PDF (skipped if file missing).
  TestPageMap          – page_map.md generation.

Run with:
    pytest tests/test_tag.py -v
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from app.parse import PageResult
from app.tag import (
    SECTION_BALANCE_SHEET,
    SECTION_CASH_FLOW,
    SECTION_EQUITY_CHANGES,
    SECTION_OTHER,
    SECTION_PROFIT_LOSS,
    STATEMENT_SECTIONS,
    TagResult,
    build_page_map,
    tag_pages,
)

# ── Seed PDF paths ─────────────────────────────────────────────────────────────
_SEED = Path(__file__).parent.parent / "data" / "seed"
_INFOSYS_PDF  = _SEED / "infosys_fy2025.pdf.pdf"
_TATA_PDF     = _SEED / "tata_motors_fy2025.pdf.pdf"
_RELIANCE_PDF = _SEED / "reliance_fy2025.pdf.pdf"
_MNM_PDF      = _SEED / "mahindra_fy2025.pdf.pdf"


# ─────────────────────────────────────────────────────────────────────────────
# Helper
# ─────────────────────────────────────────────────────────────────────────────

def _pr(pdf_page: int, text: str = "", image_only: bool = False) -> PageResult:
    has_text = len(text.strip()) >= 50
    return PageResult(
        pdf_page=pdf_page,
        text=text,
        has_text=has_text,
        image_count=0,
        is_image_only=image_only or not has_text,
    )


def _rich(pdf_page: int, text: str) -> PageResult:
    """PageResult with enough text to not be image-only."""
    padding = " extra content padding " * 5
    full = text + padding
    return PageResult(
        pdf_page=pdf_page,
        text=full,
        has_text=True,
        image_count=0,
        is_image_only=False,
    )


def _numeric_body() -> str:
    """Return text with 30 large numeric tokens – simulates a statement page."""
    nums = " ".join([f"{i},234" for i in range(100, 130)])
    return nums + " " * 10


def _parse_pdf(pdf_path: Path):
    from app.parse import parse_fast
    return parse_fast(str(pdf_path))


# ─────────────────────────────────────────────────────────────────────────────
# Unit tests
# ─────────────────────────────────────────────────────────────────────────────

class TestTagUnit:

    def test_image_only_page_tagged_as_other(self):
        pages = [_pr(1, "", image_only=True)]
        tags = tag_pages(pages)
        assert tags[0].section == SECTION_OTHER
        assert tags[0].is_image_only is True

    def test_balance_sheet_heading_detected(self):
        text = "Balance Sheet\nas at March 31 2025\n" + _numeric_body()
        tags = tag_pages([_rich(1, text)])
        assert tags[0].section == SECTION_BALANCE_SHEET

    def test_profit_loss_heading_detected(self):
        text = "Statement of Profit and Loss\nfor the year ended\n" + _numeric_body()
        tags = tag_pages([_rich(1, text)])
        assert tags[0].section == SECTION_PROFIT_LOSS

    def test_cash_flow_heading_detected(self):
        text = "Statement of Cash Flows\n" + _numeric_body()
        tags = tag_pages([_rich(1, text)])
        assert tags[0].section == SECTION_CASH_FLOW

    def test_consolidated_scope_detected(self):
        text = "Consolidated Balance Sheet\nas at March 31 2025\n" + _numeric_body()
        tags = tag_pages([_rich(1, text)])
        assert tags[0].scope == "consolidated"

    def test_standalone_scope_detected(self):
        text = "Standalone Statement of Profit and Loss\n" + _numeric_body()
        tags = tag_pages([_rich(1, text)])
        assert tags[0].scope == "standalone"

    def test_unit_captured_from_header(self):
        text = "Balance Sheet\n₹ in crore\n" + _numeric_body()
        tags = tag_pages([_rich(1, text)])
        assert "crore" in tags[0].unit.lower()

    def test_statement_page_is_table_content_type(self):
        text = "Balance Sheet\n₹ in crore\n" + _numeric_body()
        tags = tag_pages([_rich(1, text)])
        assert tags[0].content_type == "table"

    def test_narrative_page_is_narrative_content_type(self):
        text = "Director's Report\nDear Shareholders, " + "This is a narrative report. " * 10
        tags = tag_pages([_rich(1, text)])
        assert tags[0].content_type == "narrative"

    def test_contd_page_inherits_section(self):
        p1 = _rich(1, "Consolidated Balance Sheet\n₹ in crore\n" + _numeric_body())
        p2 = _rich(2, "(Contd.)\n" + _numeric_body())
        tags = tag_pages([p1, p2])
        assert tags[1].section == SECTION_BALANCE_SHEET

    def test_contd_page_inherits_scope(self):
        p1 = _rich(1, "Consolidated Balance Sheet\n" + _numeric_body())
        p2 = _rich(2, "(Contd.)\n" + _numeric_body())
        tags = tag_pages([p1, p2])
        assert tags[1].scope == "consolidated"

    def test_contd_page_inherits_unit(self):
        p1 = _rich(1, "Balance Sheet\n₹ in crore\n" + _numeric_body())
        p2 = _rich(2, "(Contd.)\n" + _numeric_body())
        tags = tag_pages([p1, p2])
        assert "crore" in tags[1].unit.lower()

    def test_high_numeric_density_gives_table_type(self):
        text = "Some header\n" + _numeric_body()
        tags = tag_pages([_rich(1, text)])
        assert tags[0].content_type == "table"

    def test_all_pages_have_result(self):
        pages = [_rich(i, f"Page {i} content " * 10) for i in range(1, 6)]
        tags = tag_pages(pages)
        assert len(tags) == 5
        assert [t.pdf_page for t in tags] == [1, 2, 3, 4, 5]

    def test_unknown_heading_gives_other(self):
        text = "Annual General Meeting Notice " * 5
        tags = tag_pages([_rich(1, text)])
        assert tags[0].section == SECTION_OTHER

    def test_auditor_report_detected(self):
        text = "Independent Auditor's Report\nTo the Members of Acme Ltd " * 3
        tags = tag_pages([_rich(1, text)])
        from app.tag import SECTION_AUDITOR_REPORT
        assert tags[0].section == SECTION_AUDITOR_REPORT

    def test_returns_tag_result_objects(self):
        tags = tag_pages([_rich(1, "Balance Sheet\n" + _numeric_body())])
        assert isinstance(tags[0], TagResult)


# ─────────────────────────────────────────────────────────────────────────────
# Infosys integration tests
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.skipif(not _INFOSYS_PDF.exists(), reason="Infosys PDF not found")
class TestTagInfosys:

    @pytest.fixture(scope="class")
    @classmethod
    def tags(cls):
        pages = _parse_pdf(_INFOSYS_PDF)
        return tag_pages(pages, pdf_name="infosys")

    def _tag_for(self, tags, pdf_page):
        return next((t for t in tags if t.pdf_page == pdf_page), None)

    def test_pages_274_275_are_balance_sheet(self, tags):
        for p in [274, 275]:
            t = self._tag_for(tags, p)
            assert t is not None, f"Tag missing for page {p}"
            assert t.section == SECTION_BALANCE_SHEET, (
                f"Page {p}: expected balance_sheet, got {t.section}"
            )

    def test_pages_274_275_are_consolidated(self, tags):
        for p in [274, 275]:
            t = self._tag_for(tags, p)
            assert t.scope == "consolidated", (
                f"Page {p}: expected consolidated, got {t.scope}"
            )

    def test_pages_274_275_unit_contains_crore(self, tags):
        for p in [274, 275]:
            t = self._tag_for(tags, p)
            assert "crore" in t.unit.lower(), (
                f"Page {p}: unit={t.unit!r} doesn't contain 'crore'"
            )

    def test_pages_276_277_are_profit_loss(self, tags):
        for p in [276, 277]:
            t = self._tag_for(tags, p)
            assert t is not None
            assert t.section == SECTION_PROFIT_LOSS, (
                f"Page {p}: expected profit_loss, got {t.section}"
            )

    def test_pages_283_284_are_cash_flow(self, tags):
        for p in [283, 284]:
            t = self._tag_for(tags, p)
            assert t is not None
            assert t.section == SECTION_CASH_FLOW, (
                f"Page {p}: expected cash_flow, got {t.section}"
            )

    def test_page_274_printed_page_is_reasonable(self, tags):
        t = self._tag_for(tags, 274)
        # Must be None or a plausible number (never wrong)
        if t.printed_page is not None:
            assert 1 <= t.printed_page <= 2000, (
                f"printed_page={t.printed_page} is out of plausible range"
            )

    def test_total_tagged_pages_equals_parse_output(self, tags):
        assert len(tags) == 369

    def test_page_map_written(self, tags, tmp_path):
        out = tmp_path / "infosys_page_map.md"
        build_page_map(tags, "Infosys", out)
        assert out.exists()
        content = out.read_text()
        assert "balance_sheet" in content
        assert "profit_loss" in content


# ─────────────────────────────────────────────────────────────────────────────
# Tata Motors integration tests
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.skipif(not _TATA_PDF.exists(), reason="Tata Motors PDF not found")
class TestTagTataMotors:

    @pytest.fixture(scope="class")
    @classmethod
    def tags(cls):
        pages = _parse_pdf(_TATA_PDF)
        return tag_pages(pages, pdf_name="tata_motors")

    def _tag_for(self, tags, pdf_page):
        return next((t for t in tags if t.pdf_page == pdf_page), None)

    def test_page_312_is_balance_sheet(self, tags):
        t = self._tag_for(tags, 312)
        assert t is not None
        assert t.section == SECTION_BALANCE_SHEET, (
            f"Page 312: expected balance_sheet, got {t.section}"
        )

    def test_page_313_is_profit_loss(self, tags):
        t = self._tag_for(tags, 313)
        assert t is not None
        assert t.section == SECTION_PROFIT_LOSS, (
            f"Page 313: expected profit_loss, got {t.section}"
        )

    def test_page_313_is_consolidated(self, tags):
        t = self._tag_for(tags, 313)
        assert t.scope == "consolidated", (
            f"Page 313: expected consolidated, got {t.scope}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Reliance integration tests
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.skipif(not _RELIANCE_PDF.exists(), reason="Reliance PDF not found")
class TestTagReliance:

    @pytest.fixture(scope="class")
    @classmethod
    def tags(cls):
        pages = _parse_pdf(_RELIANCE_PDF)
        return tag_pages(pages, pdf_name="reliance")

    def _tag_for(self, tags, pdf_page):
        return next((t for t in tags if t.pdf_page == pdf_page), None)

    def test_consolidated_statements_found(self, tags):
        """At least one page around 99-100 must be a consolidated statement."""
        candidate_pages = range(95, 115)
        consolidated_statement_pages = [
            t for t in tags
            if t.pdf_page in candidate_pages
            and t.section in STATEMENT_SECTIONS
            and t.scope == "consolidated"
        ]
        assert len(consolidated_statement_pages) >= 1, (
            f"No consolidated statement pages found near pages 99-100. "
            f"Pages 95-115 tags: "
            f"{[(t.pdf_page, t.section, t.scope) for t in tags if t.pdf_page in candidate_pages]}"
        )

    def test_page_map_written(self, tags, tmp_path):
        out = tmp_path / "reliance_page_map.md"
        build_page_map(tags, "Reliance Industries", out)
        assert out.exists()
        content = out.read_text()
        assert "Reliance Industries" in content


# ─────────────────────────────────────────────────────────────────────────────
# M&M integration tests
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.skipif(not _MNM_PDF.exists(), reason="M&M PDF not found")
class TestTagMahindra:

    @pytest.fixture(scope="class")
    @classmethod
    def tags(cls):
        pages = _parse_pdf(_MNM_PDF)
        return tag_pages(pages, pdf_name="mahindra")

    def test_statement_pages_discovered(self, tags):
        statement_pages = [t for t in tags if t.section in STATEMENT_SECTIONS]
        assert len(statement_pages) >= 4, (
            f"Expected ≥4 statement pages for M&M, found {len(statement_pages)}"
        )

    def test_page_map_written_and_lists_statements(self, tags, tmp_path):
        out = tmp_path / "mahindra_page_map.md"
        build_page_map(tags, "Mahindra & Mahindra", out)
        assert out.exists()
        content = out.read_text()
        assert "Mahindra" in content
        # Must have at least one statement row
        assert any(s in content for s in [
            "balance_sheet", "profit_loss", "cash_flow"
        ]), "page_map.md has no statement rows for M&M"


# ─────────────────────────────────────────────────────────────────────────────
# Page map tests
# ─────────────────────────────────────────────────────────────────────────────

class TestPageMap:

    def _make_tags(self) -> list[TagResult]:
        return [
            TagResult(pdf_page=1, section="other", scope="unknown"),
            TagResult(pdf_page=2, section=SECTION_BALANCE_SHEET,
                      scope="consolidated", unit="₹ in crore",
                      content_type="table", printed_page=50),
            TagResult(pdf_page=3, section=SECTION_PROFIT_LOSS,
                      scope="standalone", unit="₹ in crore",
                      content_type="table", printed_page=51),
        ]

    def test_file_is_created(self, tmp_path):
        out = tmp_path / "test_page_map.md"
        build_page_map(self._make_tags(), "TestCo", out)
        assert out.exists()

    def test_only_non_other_pages_in_table(self, tmp_path):
        out = tmp_path / "test_page_map.md"
        build_page_map(self._make_tags(), "TestCo", out)
        content = out.read_text()
        # Page 1 (other) should not appear in the table rows
        rows = [l for l in content.splitlines() if l.startswith("|") and "section" not in l and "---" not in l]
        page_nums = [r.split("|")[1].strip() for r in rows]
        assert "1" not in page_nums

    def test_company_name_in_header(self, tmp_path):
        out = tmp_path / "test_page_map.md"
        build_page_map(self._make_tags(), "Acme Corp", out)
        assert "Acme Corp" in out.read_text()

    def test_printed_page_shown(self, tmp_path):
        out = tmp_path / "test_page_map.md"
        build_page_map(self._make_tags(), "TestCo", out)
        assert "50" in out.read_text()

    def test_parent_dirs_created(self, tmp_path):
        out = tmp_path / "subdir" / "nested" / "map.md"
        build_page_map(self._make_tags(), "TestCo", out)
        assert out.exists()
