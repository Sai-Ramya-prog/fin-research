"""
tests/test_graph.py
───────────────────────────────────────────────────────────────────────────────
Unit tests for app/agents/document_agent/graph.py (Task 8).

Strategy
--------
- Every node function is tested in isolation by passing a DocumentState dict
  directly and asserting on the returned state.
- We mock all external I/O (DB calls via _set_status, parse_fast, tag_pages,
  chunk_pages, index_chunks, download_to_temp) so no real files or network
  calls are needed.
- Graph-level tests invoke _graph.invoke() with all nodes mocked to verify
  routing (parse → tag → chunk → index → END, and error short-circuits to fail).

Run with:
    pytest tests/test_graph.py -v
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, List
from unittest.mock import MagicMock, patch, call

import pytest

from app.agents.document_agent.state import DocumentState


# ─────────────────────────────────────────────────────────────────────────────
# Minimal fakes that match the real types used in graph nodes
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FakePageResult:
    pdf_page: int
    text: str = "some text " * 10
    has_text: bool = True
    image_count: int = 0
    is_image_only: bool = False
    error: str | None = None


@dataclass
class FakeTagResult:
    pdf_page: int
    section: str = "other"
    scope: str = "consolidated"
    unit: str = ""
    content_type: str = "narrative"
    printed_page: int | None = None
    is_image_only: bool = False


def _make_state(**overrides) -> DocumentState:
    doc_id = uuid.uuid4()
    base: DocumentState = {
        "doc_id": doc_id,
        "pdf_path": "/tmp/test.pdf",
        "company": "TestCo",
        "fiscal_year": "FY2024",
        "session_id": uuid.uuid4(),
        "pages": [],
        "tags": [],
        "chunks": [],
        "error": None,
    }
    base.update(overrides)
    return base


# ─────────────────────────────────────────────────────────────────────────────
# Autouse fixture – suppress all DB / external calls by default
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _patch_env(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    monkeypatch.setenv("CHROMA_API_KEY", "key")
    monkeypatch.setenv("CHROMA_TENANT", "tenant")
    monkeypatch.setenv("CHROMA_DATABASE", "db")
    monkeypatch.setenv("CHROMA_COLLECTION", "col")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./test_graph.db")
    monkeypatch.setenv("GOOGLE_API_KEY", "dummy")


@pytest.fixture(autouse=True)
def _patch_set_status():
    """Suppress all DB status writes in every test."""
    with patch("app.agents.document_agent.graph._set_status"):
        yield


# ─────────────────────────────────────────────────────────────────────────────
# Node tests – _node_parse
# ─────────────────────────────────────────────────────────────────────────────

class TestNodeParse:
    def test_happy_path_returns_pages(self):
        from app.agents.document_agent.graph import _node_parse
        pages = [FakePageResult(pdf_page=i + 1) for i in range(5)]
        state = _make_state()

        with patch("app.agents.document_agent.graph.parse_fast", return_value=pages):
            result = _node_parse(state)

        assert result["error"] is None
        assert result["pages"] == pages
        assert len(result["pages"]) == 5

    def test_exception_sets_error(self):
        from app.agents.document_agent.graph import _node_parse
        state = _make_state()

        with patch(
            "app.agents.document_agent.graph.parse_fast",
            side_effect=RuntimeError("bad PDF"),
        ):
            result = _node_parse(state)

        assert result["error"] is not None
        assert "parse failed" in result["error"]
        assert "bad PDF" in result["error"]


# ─────────────────────────────────────────────────────────────────────────────
# Node tests – _node_tag
# ─────────────────────────────────────────────────────────────────────────────

class TestNodeTag:
    def test_happy_path_returns_tags(self):
        from app.agents.document_agent.graph import _node_tag
        pages = [FakePageResult(pdf_page=1)]
        tags = [FakeTagResult(pdf_page=1)]
        state = _make_state(pages=pages)

        with patch("app.agents.document_agent.graph.tag_pages", return_value=tags):
            result = _node_tag(state)

        assert result["error"] is None
        assert result["tags"] == tags

    def test_passes_through_if_error_already_set(self):
        from app.agents.document_agent.graph import _node_tag
        state = _make_state(error="upstream error")

        with patch("app.agents.document_agent.graph.tag_pages") as mock_tag:
            result = _node_tag(state)

        mock_tag.assert_not_called()
        assert result["error"] == "upstream error"

    def test_exception_sets_error(self):
        from app.agents.document_agent.graph import _node_tag
        state = _make_state(pages=[FakePageResult(pdf_page=1)])

        with patch(
            "app.agents.document_agent.graph.tag_pages",
            side_effect=ValueError("tagger crash"),
        ):
            result = _node_tag(state)

        assert "tag failed" in result["error"]


# ─────────────────────────────────────────────────────────────────────────────
# Node tests – _node_chunk
# ─────────────────────────────────────────────────────────────────────────────

class TestNodeChunk:
    def _fake_chunks(self, n: int) -> List[Any]:
        return [
            {"text": f"chunk {i}", "metadata": {"pdf_page": i + 1, "section": "other"}}
            for i in range(n)
        ]

    def test_happy_path_returns_chunks_with_extra_meta(self):
        from app.agents.document_agent.graph import _node_chunk
        pages = [FakePageResult(pdf_page=1)]
        tags = [FakeTagResult(pdf_page=1, content_type="narrative")]
        state = _make_state(pages=pages, tags=tags)

        raw = self._fake_chunks(3)
        with patch("app.agents.document_agent.graph.chunk_pages", return_value=raw):
            result = _node_chunk(state)

        assert result["error"] is None
        assert len(result["chunks"]) == 3
        # Extra metadata injected by the node
        for chunk in result["chunks"]:
            assert chunk["metadata"]["doc_id"] == str(state["doc_id"])
            assert chunk["metadata"]["company"] == "TestCo"
            assert chunk["metadata"]["fiscal_year"] == "FY2024"

    def test_passes_through_if_error_set(self):
        from app.agents.document_agent.graph import _node_chunk
        state = _make_state(error="upstream error")

        with patch("app.agents.document_agent.graph.chunk_pages") as mock_chunk:
            result = _node_chunk(state)

        mock_chunk.assert_not_called()
        assert result["error"] == "upstream error"

    def test_statement_pages_get_layout_text(self):
        """Table-type pages should be re-parsed with layout-preserving text."""
        from app.agents.document_agent.graph import _node_chunk
        pages = [FakePageResult(pdf_page=1, text="original")]
        tags = [FakeTagResult(pdf_page=1, content_type="table")]
        state = _make_state(pages=pages, tags=tags)

        layout_map = {1: "layout-preserved text"}
        raw = self._fake_chunks(1)

        with (
            patch("app.agents.document_agent.graph.parse_layout", return_value=layout_map),
            patch("app.agents.document_agent.graph.chunk_pages", return_value=raw) as mock_chunk,
        ):
            result = _node_chunk(state)

        # chunk_pages should have been called with the layout-replaced pages
        call_pages = mock_chunk.call_args[0][0]
        assert call_pages[0].text == "layout-preserved text"
        assert result["error"] is None

    def test_exception_sets_error(self):
        from app.agents.document_agent.graph import _node_chunk
        state = _make_state(
            pages=[FakePageResult(pdf_page=1)],
            tags=[FakeTagResult(pdf_page=1)],
        )

        with patch(
            "app.agents.document_agent.graph.chunk_pages",
            side_effect=RuntimeError("chunker crash"),
        ):
            result = _node_chunk(state)

        assert "chunk failed" in result["error"]


# ─────────────────────────────────────────────────────────────────────────────
# Node tests – _node_index
# ─────────────────────────────────────────────────────────────────────────────

class TestNodeIndex:
    def test_happy_path_calls_index_chunks(self):
        from app.agents.document_agent.graph import _node_index
        chunks = [{"text": "x", "metadata": {}}]
        state = _make_state(chunks=chunks)

        with patch("app.agents.document_agent.graph.index_chunks") as mock_idx:
            result = _node_index(state)

        mock_idx.assert_called_once_with(state["doc_id"], chunks)
        assert result["error"] is None

    def test_passes_through_if_error_set(self):
        from app.agents.document_agent.graph import _node_index
        state = _make_state(error="upstream error", chunks=[])

        with patch("app.agents.document_agent.graph.index_chunks") as mock_idx:
            result = _node_index(state)

        mock_idx.assert_not_called()

    def test_exception_sets_error(self):
        from app.agents.document_agent.graph import _node_index
        state = _make_state(chunks=[{"text": "x", "metadata": {}}])

        with patch(
            "app.agents.document_agent.graph.index_chunks",
            side_effect=RuntimeError("quota exceeded"),
        ):
            result = _node_index(state)

        assert "index failed" in result["error"]


# ─────────────────────────────────────────────────────────────────────────────
# Node tests – _node_fail
# ─────────────────────────────────────────────────────────────────────────────

class TestNodeFail:
    def test_calls_set_status_failed(self):
        from app.agents.document_agent.graph import _node_fail
        state = _make_state(error="something broke")

        with patch("app.agents.document_agent.graph._set_status") as mock_status:
            _node_fail(state)

        mock_status.assert_called_once_with(
            state["doc_id"], "failed", error_message="something broke"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Routing tests – _route
# ─────────────────────────────────────────────────────────────────────────────

class TestRoute:
    def test_no_error_returns_continue(self):
        from app.agents.document_agent.graph import _route
        assert _route(_make_state()) == "continue"

    def test_error_returns_fail(self):
        from app.agents.document_agent.graph import _route
        assert _route(_make_state(error="oops")) == "fail"


# ─────────────────────────────────────────────────────────────────────────────
# Graph-level integration tests (all I/O mocked)
# ─────────────────────────────────────────────────────────────────────────────

class TestGraphRouting:
    """Invoke the compiled graph with all nodes mocked to verify routing."""

    def _pages(self):
        return [FakePageResult(pdf_page=1)]

    def _tags(self):
        return [FakeTagResult(pdf_page=1)]

    def _chunks(self):
        return [{"text": "chunk", "metadata": {"pdf_page": 1}}]

    def test_happy_path_reaches_end(self):
        from app.agents.document_agent.graph import _graph
        state = _make_state()

        with (
            patch("app.agents.document_agent.graph.parse_fast", return_value=self._pages()),
            patch("app.agents.document_agent.graph.tag_pages", return_value=self._tags()),
            patch("app.agents.document_agent.graph.chunk_pages", return_value=self._chunks()),
            patch("app.agents.document_agent.graph.index_chunks"),
        ):
            final = _graph.invoke(state)

        assert final["error"] is None

    def test_parse_error_skips_to_fail(self):
        """If parse raises, the final state should carry an error field."""
        from app.agents.document_agent.graph import _graph
        state = _make_state()

        with (
            patch(
                "app.agents.document_agent.graph.parse_fast",
                side_effect=RuntimeError("bad file"),
            ),
            patch("app.agents.document_agent.graph._set_status"),
        ):
            final = _graph.invoke(state)

        # The fail node was reached – error is set in final state
        assert final.get("error") is not None
        assert "parse failed" in final["error"]

    def test_index_error_routes_to_fail(self):
        """If index raises, the final state should carry an error field."""
        from app.agents.document_agent.graph import _graph
        state = _make_state()

        with (
            patch("app.agents.document_agent.graph.parse_fast", return_value=self._pages()),
            patch("app.agents.document_agent.graph.tag_pages", return_value=self._tags()),
            patch("app.agents.document_agent.graph.chunk_pages", return_value=self._chunks()),
            patch(
                "app.agents.document_agent.graph.index_chunks",
                side_effect=RuntimeError("quota"),
            ),
            patch("app.agents.document_agent.graph._set_status"),
        ):
            final = _graph.invoke(state)

        assert final.get("error") is not None
        assert "index failed" in final["error"]

