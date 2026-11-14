"""
tests/test_retrieval.py
-----------------------------------------------------------------------------
Unit tests for app/retrieval.py.

Strategy
--------
- Qdrant runs in-process (``:memory:``), so no server, key or network is needed.
- We use the ``fake`` embedding provider (deterministic, offline, 768-dim).
- Tests pre-populate the collection with known chunks and assert that:
    * Metadata filters (company, section, scope, fiscal_year) work correctly -
      especially that a filter on company=X never returns chunks from company=Y.
    * Several filters are combined with AND.
    * An empty query raises ValueError.
    * An empty collection returns an empty list.

Run with:
    pytest tests/test_retrieval.py -v
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.retrieval import SearchResult, _build_filter, search


@pytest.fixture(autouse=True)
def _patch_env(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")


@pytest.fixture()
def ephemeral_collection(monkeypatch):
    """An empty in-memory Qdrant collection, fresh for every test."""
    from qdrant_client import QdrantClient
    from app import vectorstore

    client = QdrantClient(":memory:")
    monkeypatch.setattr(vectorstore, "get_client", lambda: client)
    monkeypatch.setattr(vectorstore.settings, "qdrant_collection", f"test-{uuid.uuid4().hex[:8]}")
    vectorstore._ensured.clear()
    return vectorstore


def _populate_collection(col) -> None:
    """Add a representative set of chunks (Infosys, TataMotors) to the collection.

    ``col`` is the ``app.vectorstore`` module (see the fixture)."""
    chunks = [
        {
            "id": "infosys-bs-274",
            "text": "Total assets consolidated balance sheet 1,48,903 crores equity",
            "meta": {
                "company": "Infosys",
                "section": "balance_sheet",
                "scope": "consolidated",
                "fiscal_year": "FY2024",
                "pdf_page": 274,
                "printed_page": 302,
                "unit": "In Rs crore",
            },
        },
        {
            "id": "infosys-bs-275",
            "text": "Total equity shareholders consolidated liabilities 1,48,903",
            "meta": {
                "company": "Infosys",
                "section": "balance_sheet",
                "scope": "consolidated",
                "fiscal_year": "FY2024",
                "pdf_page": 275,
                "printed_page": 303,
                "unit": "In Rs crore",
            },
        },
        {
            "id": "infosys-pl-276",
            "text": "Profit attributable to owners of the company 26,248 crores",
            "meta": {
                "company": "Infosys",
                "section": "profit_loss",
                "scope": "consolidated",
                "fiscal_year": "FY2024",
                "pdf_page": 276,
                "printed_page": 304,
                "unit": "In Rs crore",
            },
        },
        {
            "id": "infosys-pl-277",
            "text": "Basic earnings per share profit loss statement 39.46",
            "meta": {
                "company": "Infosys",
                "section": "profit_loss",
                "scope": "consolidated",
                "fiscal_year": "FY2024",
                "pdf_page": 277,
                "printed_page": 305,
                "unit": "In Rs crore",
            },
        },
        {
            "id": "tata-pl-313",
            "text": "Tata Motors profit loss consolidated revenue from operations",
            "meta": {
                "company": "TataMotors",
                "section": "profit_loss",
                "scope": "consolidated",
                "fiscal_year": "FY2024",
                "pdf_page": 313,
                "printed_page": -1,
                "unit": "Rs in crores",
            },
        },
        {
            "id": "infosys-standalone-274",
            "text": "Total assets standalone balance sheet 98,201",
            "meta": {
                "company": "Infosys",
                "section": "balance_sheet",
                "scope": "standalone",
                "fiscal_year": "FY2024",
                "pdf_page": 320,
                "printed_page": 348,
                "unit": "In Rs crore",
            },
        },
    ]
    from app.embeddings import get_embeddings
    emb = get_embeddings()
    for c in chunks:
        vector = emb.embed_documents([c["text"]])[0]
        col.upsert([c["id"]], [c["text"]], [c["meta"]], [vector])


# -----------------------------------------------------------------------------
# Tests - _build_filter (unit tests, no vector store needed)
# -----------------------------------------------------------------------------

class TestBuildFilter:
    def test_no_filters_returns_none(self):
        assert _build_filter(None, None, None, None) is None

    def test_single_company_filter(self):
        assert _build_filter("Infosys", None, None, None) == {"company": "Infosys"}

    def test_single_section_filter(self):
        assert _build_filter(None, "balance_sheet", None, None) == {"section": "balance_sheet"}

    def test_several_filters_are_all_kept(self):
        assert _build_filter("Infosys", "balance_sheet", None, None) == {
            "company": "Infosys", "section": "balance_sheet",
        }

    def test_all_four_filters(self):
        w = _build_filter("Infosys", "balance_sheet", "consolidated", "FY2024")
        assert w == {"company": "Infosys", "section": "balance_sheet",
                     "scope": "consolidated", "fiscal_year": "FY2024"}

    def test_fiscal_year_stored_as_string(self):
        assert _build_filter(None, None, None, 2025) == {"fiscal_year": "2025"}


# ─────────────────────────────────────────────────────────────────────────────
# Tests – search() function
# ─────────────────────────────────────────────────────────────────────────────

class TestSearch:
    def test_empty_query_raises(self, ephemeral_collection):
        with pytest.raises(ValueError, match="non-empty"):
            search("")

    def test_whitespace_only_query_raises(self, ephemeral_collection):
        with pytest.raises(ValueError):
            search("   ")

    def test_empty_collection_returns_empty_list(self, ephemeral_collection):
        # Collection has no chunks yet
        results = search("total assets", k=5)
        assert results == []

    def test_returns_search_result_namedtuples(self, ephemeral_collection):
        _populate_collection(ephemeral_collection)
        results = search("total assets", k=3)
        assert len(results) > 0
        for r in results:
            assert isinstance(r, SearchResult)
            assert isinstance(r.text, str)
            assert isinstance(r.metadata, dict)
            assert isinstance(r.score, float)
            assert -1.0 <= r.score <= 1.0   # cosine similarity

    def test_company_filter_excludes_other_companies(self, ephemeral_collection):
        """Filtering on company=Infosys must never return TataMotors chunks."""
        _populate_collection(ephemeral_collection)
        results = search("profit loss", company="Infosys", k=10)
        companies = {r.metadata.get("company") for r in results}
        assert "TataMotors" not in companies, (
            f"TataMotors chunk leaked through Infosys filter: {companies}"
        )

    def test_tata_filter_excludes_infosys(self, ephemeral_collection):
        """Filtering on company=TataMotors must never return Infosys chunks."""
        _populate_collection(ephemeral_collection)
        results = search("profit", company="TataMotors", k=10)
        companies = {r.metadata.get("company") for r in results}
        assert "Infosys" not in companies, (
            f"Infosys chunk leaked through TataMotors filter: {companies}"
        )

    def test_section_filter_works(self, ephemeral_collection):
        """section=profit_loss should not return balance_sheet chunks."""
        _populate_collection(ephemeral_collection)
        results = search("assets liabilities equity", section="profit_loss", k=10)
        sections = {r.metadata.get("section") for r in results}
        assert "balance_sheet" not in sections

    def test_scope_filter_consolidated_excludes_standalone(self, ephemeral_collection):
        """scope=consolidated should not return standalone chunks."""
        _populate_collection(ephemeral_collection)
        results = search("total assets balance sheet", scope="consolidated", k=10)
        scopes = {r.metadata.get("scope") for r in results}
        assert "standalone" not in scopes

    def test_combined_filter_company_and_section(self, ephemeral_collection):
        """company=Infosys + section=balance_sheet should return only matching chunks."""
        _populate_collection(ephemeral_collection)
        results = search(
            "total assets",
            company="Infosys",
            section="balance_sheet",
            scope="consolidated",
            k=10,
        )
        assert len(results) > 0
        for r in results:
            assert r.metadata["company"] == "Infosys"
            assert r.metadata["section"] == "balance_sheet"
            assert r.metadata["scope"] == "consolidated"

    def test_balance_sheet_pdf_pages_in_range(self, ephemeral_collection):
        """
        Semantic check (with fake provider: tests filter correctness).
        search('total assets', company=Infosys, section=balance_sheet,
        scope=consolidated) must return chunks with pdf_page in {274, 275}.
        """
        _populate_collection(ephemeral_collection)
        results = search(
            "total assets",
            company="Infosys",
            section="balance_sheet",
            scope="consolidated",
            k=5,
        )
        pdf_pages = {r.metadata.get("pdf_page") for r in results}
        assert pdf_pages.issubset({274, 275}), (
            f"Unexpected pages returned: {pdf_pages}"
        )

    def test_profit_loss_pdf_pages_in_range(self, ephemeral_collection):
        """
        search('profit attributable to owners', company=Infosys,
        section=profit_loss, scope=consolidated) must return pages 276 or 277.
        """
        _populate_collection(ephemeral_collection)
        results = search(
            "profit attributable to owners",
            company="Infosys",
            section="profit_loss",
            scope="consolidated",
            k=5,
        )
        pdf_pages = {r.metadata.get("pdf_page") for r in results}
        assert pdf_pages.issubset({276, 277}), (
            f"Unexpected pages returned: {pdf_pages}"
        )

    def test_k_limits_results(self, ephemeral_collection):
        """k=1 should return at most 1 result."""
        _populate_collection(ephemeral_collection)
        results = search("total assets equity", k=1)
        assert len(results) <= 1

    def test_score_is_cosine_similarity(self, ephemeral_collection):
        """Scores are cosine similarities, so always within [-1, 1]."""
        _populate_collection(ephemeral_collection)
        results = search("revenue profit", k=5)
        for r in results:
            assert -1.0 <= r.score <= 1.0, f"Score out of range: {r.score}"
