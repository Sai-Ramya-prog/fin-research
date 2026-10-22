"""
tests/test_tiering.py
Tiered indexing: tier labels, keyword page search, on-demand page selection.
"""

from __future__ import annotations

import os

for _k, _v in dict(
    GOOGLE_API_KEY="x", CHROMA_API_KEY="x", CHROMA_TENANT="x", CHROMA_DATABASE="x",
    DATABASE_URL="postgresql+psycopg://u:p@localhost/db",
    STORAGE_ENDPOINT_URL="https://x.example", STORAGE_BUCKET="b",
    STORAGE_ACCESS_KEY_ID="x", STORAGE_SECRET_ACCESS_KEY="x",
).items():
    os.environ.setdefault(_k, _v)

from app.pages import score_pages
from app.parse import PageResult
from app.tag import TIER1_SECTIONS, tag_pages


def _page(n: int, text: str) -> PageResult:
    return PageResult(pdf_page=n, text=text, has_text=True, image_count=0, is_image_only=False)


class TestTierLabels:
    def test_tier1_sections(self):
        assert {"balance_sheet", "profit_loss", "cash_flow", "equity_changes",
                "auditor_report", "notes", "mdna_narrative"} == set(TIER1_SECTIONS)

    def test_statement_page_is_tier1_and_cover_is_tier2(self):
        numbers = "\n".join(f"Item {i}  {1000 + i},234  {2000 + i},567" for i in range(40))
        statement = _page(1, "Consolidated Balance Sheet\n(₹ in crore)\n" + numbers)
        cover = _page(2, "Our year in pictures. Sustainability and people.")
        tags = tag_pages([statement, cover], "t.pdf")
        assert tags[0].section == "balance_sheet" and tags[0].tier == 1
        assert tags[1].section == "other" and tags[1].tier == 2

    def test_image_only_page_is_tier2(self):
        img = PageResult(pdf_page=1, text="", has_text=False, image_count=1, is_image_only=True)
        assert tag_pages([img], "t.pdf")[0].tier == 2


class TestScorePages:
    ROWS = [
        {"pdf_page": 10, "text": "Operating margin fell because of wage hikes and subcontractor cost."},
        {"pdf_page": 11, "text": "Our board of directors met six times."},
        {"pdf_page": 12, "text": "Margin pressure from wage hikes; operating margin down 1.5 points."},
    ]

    def test_best_match_first(self):
        top = score_pages("why did operating margin fall wage hikes", self.ROWS, limit=2)
        assert [r["pdf_page"] for r in top] == [12, 10] or [r["pdf_page"] for r in top] == [10, 12]
        assert 11 not in [r["pdf_page"] for r in top]

    def test_no_match_returns_empty(self):
        assert score_pages("quantum entanglement", self.ROWS) == []

    def test_only_stopwords_returns_empty(self):
        assert score_pages("why did the", self.ROWS) == []

    def test_limit_respected(self):
        assert len(score_pages("margin", self.ROWS, limit=1)) == 1
