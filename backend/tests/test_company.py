"""Company-name normalisation, and the vector payload fix."""

from __future__ import annotations

import uuid

import pytest

from app.company import normalize_company


@pytest.mark.parametrize("raw,expected", [
    ("Infosys", "INFOSYS"),
    ("  infosys ", "INFOSYS"),
    ("Tata   Motors", "TATA MOTORS"),
    ("m&m", "M&M"),
    ("", None),
    ("   ", None),
    (None, None),
])
def test_normalize_company(raw, expected):
    assert normalize_company(raw) == expected


def test_set_company_updates_only_that_document(monkeypatch):
    from qdrant_client import QdrantClient
    from app import vectorstore

    client = QdrantClient(":memory:")
    monkeypatch.setattr(vectorstore, "get_client", lambda: client)
    monkeypatch.setattr(vectorstore.settings, "qdrant_collection", f"c-{uuid.uuid4().hex[:6]}")
    vectorstore._ensured.clear()

    d1, d2 = uuid.uuid4(), uuid.uuid4()
    for d, comp in ((d1, "Infosys"), (d2, "Reliance")):
        vectorstore.upsert([f"{d}:1:0", f"{d}:2:0"], ["a", "b"],
                           [{"doc_id": str(d), "company": comp}] * 2, [[0.1] * 768] * 2)

    assert vectorstore.set_company(d1, "INFOSYS") == 2
    res = vectorstore.search([0.1] * 768, limit=10)
    by_doc = {p.payload["doc_id"]: p.payload["company"] for p in res}
    assert by_doc[str(d1)] == "INFOSYS"
    assert by_doc[str(d2)] == "Reliance"      # untouched


def test_upload_stores_upper_case_company(monkeypatch):
    """The upload route normalises the name before saving."""
    import inspect
    from app.api import routes_sessions
    assert "normalize_company(company)" in inspect.getsource(routes_sessions.upload_document)
