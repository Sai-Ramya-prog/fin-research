"""
tests/test_agents.py
Extraction Agent, Red Flag Agent and the orchestrator, against the Apple
FY2025 extract PDF and a fake Gemini that answers from a golden file.
No network, no API keys: DB is in-memory SQLite.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

for _k, _v in dict(
    GOOGLE_API_KEY="x", CHROMA_API_KEY="x", CHROMA_TENANT="x", CHROMA_DATABASE="x",
    DATABASE_URL="postgresql+psycopg://u:p@localhost/db", EMBEDDING_PROVIDER="fake", STORAGE_BACKEND="local",
).items():
    os.environ.setdefault(_k, _v)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agents.extraction_agent import graph as ext_graph
from app.agents.extraction_agent.metrics import DebtLine, Item, StatementAnswer
from app.agents.extraction_agent.ratios import compute_ratios
from app.agents.extraction_agent.verify import consistency_check, numbers_on_page, source_check
from app.agents.red_flag_agent.auditor import scan_auditor_pages
from app.agents.red_flag_agent.rules import run_rules
from app.db.models import Base, Document, Metric, PageTag, RedFlag, Session
from app.parse import parse_fast
from app.tag import tag_pages

ROOT = Path(__file__).parent
GOLDEN = json.loads((ROOT / "golden" / "apple_fy2025.json").read_text())
PDF = ROOT / "data" / "apple_fy2025_extract.pdf"


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def db(monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def scope():
        s = maker()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    for mod in ("app.pages", "app.agents.extraction_agent.graph",
                "app.agents.red_flag_agent.graph", "app.agents.orchestrator"):
        monkeypatch.setattr(f"{mod}.session_scope", scope)
    return scope


@pytest.fixture()
def apple_doc(db):
    """A Document row whose page store holds the real parsed + tagged Apple PDF."""
    pages = parse_fast(str(PDF))
    tags = tag_pages(pages, "apple.pdf")
    doc_id = uuid.uuid4()
    with db() as s:
        sess = Session(name="t")
        s.add(sess)
        s.flush()
        s.add(Document(id=doc_id, session_id=sess.id, filename="apple.pdf", storage_key=f"k{doc_id}",
                       company="Apple", fiscal_year=2025, status="ready", page_count=len(pages)))
        s.flush()
        for p, t in zip(pages, tags):
            s.add(PageTag(document_id=doc_id, pdf_page=t.pdf_page, section=t.section, scope=t.scope,
                          unit=t.unit, content_type=t.content_type, is_image_only=t.is_image_only,
                          tier=t.tier, text=p.text))
    return doc_id


def _golden_answer(prompt: str, corrupt: dict | None = None) -> StatementAnswer:
    """What a perfect model would answer for this prompt (read from the golden file)."""
    section = re.search(r"Statement type: (\w+ ?\w*)", prompt).group(1).replace(" ", "_")
    page = int(re.search(r"=== PAGE (\d+) ===", prompt).group(1))
    spec = GOLDEN[section]
    items = []
    for metric, v in spec.items():
        if metric == "debt_lines":
            continue
        cur, pri = v[0], v[1]
        if corrupt and metric in corrupt:
            cur = corrupt[metric]
        items.append(Item(metric=metric, current=cur, prior=pri, page=page, source_line=v[2]))
    debts = [DebtLine(label=l, current=c, prior=p, page=page, source_line=l) for l, c, p in spec.get("debt_lines", [])]
    return StatementAnswer(items=items, debt_lines=debts)


@pytest.fixture()
def fake_llm():
    def make(corrupt=None):
        return patch.object(ext_graph.llm, "call_structured",
                            side_effect=lambda schema, system, user: _golden_answer(user, corrupt))
    return make


def _metrics(db, doc_id):
    with db() as s:
        rows = s.query(Metric).filter(Metric.document_id == doc_id).all()
        return {(r.name, r.year): r for r in rows}


# ── verify ────────────────────────────────────────────────────────────────────

class TestVerify:
    def test_numbers_on_page_ignores_commas_and_brackets(self):
        nums = numbers_on_page("Net income 1,12,010 (14,264) 7.46")
        assert {112010.0, 14264.0, 7.46} <= nums

    def test_number_not_on_page_needs_review(self):
        rows = [{"name": "revenue", "period": "current", "value": 999.0, "page": 2, "kind": "extracted",
                 "status": "verified", "note": None}]
        source_check(rows, {2: "Total net sales 416,161 391,035"})
        assert rows[0]["status"] == "needs_review"

    def test_wrong_cited_page_is_corrected(self):
        rows = [{"name": "revenue", "period": "current", "value": 416161.0, "page": 9, "kind": "extracted",
                 "status": "verified", "note": None}]
        source_check(rows, {2: "Total net sales 416,161", 3: "other"})
        assert rows[0]["status"] == "verified" and rows[0]["page"] == 2

    def test_balance_sheet_mismatch_flags_all_three(self):
        def r(n, v):
            return {"name": n, "period": "current", "value": v, "kind": "extracted", "status": "verified", "note": None}
        rows = [r("total_assets", 1000), r("total_liabilities", 400), r("total_equity", 300)]
        consistency_check(rows)
        assert {x["status"] for x in rows} == {"needs_review"}


# ── ratios ────────────────────────────────────────────────────────────────────

def _row(name, period, value, status="verified"):
    return {"name": name, "period": period, "value": value, "unit": "$ in million", "page": 2,
            "kind": "extracted", "status": status, "note": None, "source_text": None, "formula": None}


class TestRatios:
    def test_margin_and_growth(self):
        rows = [_row("revenue", "current", 200.0), _row("revenue", "prior", 100.0),
                _row("net_profit", "current", 20.0), _row("net_profit", "prior", 20.0)]
        out = {(r["name"], r["period"]): r for r in compute_ratios(rows)}
        assert out[("net_margin_pct", "current")]["value"] == 10.0
        assert out[("net_margin_pct", "prior")]["value"] == 20.0
        assert out[("revenue_growth_pct", "current")]["value"] == 100.0

    def test_ratio_inherits_needs_review(self):
        rows = [_row("revenue", "current", 200.0), _row("net_profit", "current", 20.0, "needs_review")]
        out = compute_ratios(rows)
        assert out[0]["status"] == "needs_review"

    def test_zero_denominator_is_skipped(self):
        rows = [_row("revenue", "current", 0.0), _row("net_profit", "current", 5.0)]
        assert compute_ratios(rows) == []


# ── red-flag rules ────────────────────────────────────────────────────────────

def _ratio(name, period, value, status="verified"):
    return {"name": name, "period": period, "value": value, "unit": "%", "page": 3, "status": status}


class TestRules:
    def test_falling_margin_high(self):
        flags = run_rules([_ratio("net_margin_pct", "current", 10), _ratio("net_margin_pct", "prior", 18)])
        assert [f["type"] for f in flags] == ["falling_margin"] and flags[0]["severity"] == "High"

    def test_small_margin_move_is_not_flagged(self):
        assert run_rules([_ratio("net_margin_pct", "current", 17.5), _ratio("net_margin_pct", "prior", 18)]) == []

    def test_rising_debt_and_leverage(self):
        flags = run_rules([_ratio("total_debt_growth_pct", "current", 60), _ratio("debt_to_equity", "current", 2.5)])
        assert {f["type"] for f in flags} == {"rising_debt", "high_leverage"}

    def test_net_loss(self):
        flags = run_rules([{"name": "net_profit", "period": "current", "value": -5.0, "unit": "", "page": 2, "status": "verified"}])
        assert flags[0]["type"] == "net_loss"

    def test_unverified_inputs_do_not_trigger_rules(self):
        assert run_rules([_ratio("debt_to_equity", "current", 9, "needs_review")]) == []

    def test_cash_profit_gap(self):
        flags = run_rules([_ratio("ocf_to_net_profit", "current", 0.2)])
        assert flags[0]["type"] == "cash_profit_gap"


class TestAuditor:
    def test_qualified_opinion_heading_is_found_with_page(self):
        pages = [{"pdf_page": 7, "text": "Independent Auditor's Report\nQualified Opinion\nWe have audited ... except for the effects of ..."}]
        flags = scan_auditor_pages(pages)
        assert flags[0]["type"] == "auditor_qualified_opinion" and flags[0]["source_page"] == 7

    def test_clean_report_with_generic_wording_is_not_flagged(self):
        text = ("Opinion\nIn our opinion the accounts give a true and fair view.\n"
                "We conclude on the appropriateness of the use of the going concern basis and whether a material "
                "uncertainty exists related to events or conditions.\nKey Audit Matters\nRevenue recognition")
        assert scan_auditor_pages([{"pdf_page": 1, "text": text}]) == []

    def test_going_concern_heading(self):
        flags = scan_auditor_pages([{"pdf_page": 4, "text": "Material Uncertainty Related to Going Concern\nWe draw attention to note 9"}])
        assert flags[0]["type"] == "going_concern" and flags[0]["severity"] == "High"


# ── extraction graph (Apple PDF + golden fake LLM) ────────────────────────────

class TestExtractionGraph:
    def test_all_core_metrics_saved_and_verified(self, db, apple_doc, fake_llm):
        with fake_llm():
            assert ext_graph.run_extraction_agent(apple_doc) is True
        m = _metrics(db, apple_doc)
        assert m[("revenue", 2025)].value == 416161
        assert m[("revenue", 2024)].value == 391035
        assert m[("capex", 2025)].value == -12715
        assert m[("revenue", 2025)].source_page == 2
        for key, row in m.items():
            if row.kind == "extracted":
                assert row.status == "verified", (key, row.note)

    def test_debt_is_summed_in_code(self, db, apple_doc, fake_llm):
        with fake_llm():
            ext_graph.run_extraction_agent(apple_doc)
        m = _metrics(db, apple_doc)
        assert m[("total_debt", 2025)].value == GOLDEN["expected_ratios"]["total_debt_current"]
        assert "Commercial paper" in m[("total_debt", 2025)].source_text

    def test_ratios_match_golden(self, db, apple_doc, fake_llm):
        with fake_llm():
            ext_graph.run_extraction_agent(apple_doc)
        m = _metrics(db, apple_doc)
        exp = GOLDEN["expected_ratios"]
        assert float(m[("operating_margin_pct", 2025)].value) == pytest.approx(exp["operating_margin_pct"][0], abs=0.01)
        assert float(m[("net_margin_pct", 2024)].value) == pytest.approx(exp["net_margin_pct"][1], abs=0.01)
        assert float(m[("current_ratio", 2025)].value) == pytest.approx(exp["current_ratio"][0], abs=0.001)
        assert float(m[("revenue_growth_pct", 2025)].value) == pytest.approx(exp["revenue_growth_pct"], abs=0.01)

    def test_made_up_number_is_caught(self, db, apple_doc, fake_llm):
        with fake_llm(corrupt={"revenue": 555555}):
            ext_graph.run_extraction_agent(apple_doc)
        m = _metrics(db, apple_doc)
        assert m[("revenue", 2025)].status == "needs_review"
        assert "not found" in m[("revenue", 2025)].note
        assert m[("operating_margin_pct", 2025)].status == "needs_review"   # inherits

    def test_no_statement_pages_fails_cleanly(self, db, fake_llm):
        doc_id = uuid.uuid4()
        with db() as s:
            sess = Session(name="t"); s.add(sess); s.flush()
            s.add(Document(id=doc_id, session_id=sess.id, filename="x.pdf", storage_key="kx", status="ready", fiscal_year=2025))
        with fake_llm():
            assert ext_graph.run_extraction_agent(doc_id) is False
        with db() as s:
            d = s.get(Document, doc_id)
            assert d.extraction_status == "failed" and "no statement pages" in d.analysis_error

    def test_quota_error_marks_failed(self, db, apple_doc):
        from app.embeddings import QuotaExhaustedError
        with patch.object(ext_graph.llm, "call_structured", side_effect=QuotaExhaustedError("daily quota")):
            assert ext_graph.run_extraction_agent(apple_doc) is False
        with db() as s:
            assert "quota" in s.get(Document, apple_doc).analysis_error.lower()


# ── orchestrator end to end ───────────────────────────────────────────────────

class TestOrchestrator:
    def _run(self, doc_id, fake_llm):
        from app.agents import orchestrator
        with patch.object(orchestrator, "run_document_agent"), fake_llm():
            orchestrator.run_pipeline(doc_id)

    def test_full_pipeline_on_apple(self, db, apple_doc, fake_llm):
        self._run(apple_doc, fake_llm)
        with db() as s:
            d = s.get(Document, apple_doc)
            assert (d.status, d.extraction_status, d.red_flag_status) == ("ready", "done", "done")
            flags = s.query(RedFlag).filter(RedFlag.document_id == apple_doc).all()
        # Apple FY2025: debt fell and margins rose, cash tracks profit. What is
        # left is real: debt is 1.34x equity (Medium) and the current ratio is
        # 0.89 (Low). Nothing else may fire.
        assert sorted((f.type, f.severity) for f in flags) == [("high_leverage", "Medium"), ("weak_liquidity", "Low")]
        assert flags[0].source_page is not None

    def test_extraction_failure_skips_red_flags_but_keeps_document_ready(self, db, apple_doc):
        from app.agents import orchestrator
        with patch.object(orchestrator, "run_document_agent"), \
             patch.object(ext_graph.llm, "call_structured", side_effect=RuntimeError("boom")):
            orchestrator.run_pipeline(apple_doc)
        with db() as s:
            d = s.get(Document, apple_doc)
            assert d.status == "ready"
            assert (d.extraction_status, d.red_flag_status) == ("failed", "skipped")

    def test_document_agent_failure_skips_both(self, db, apple_doc):
        from app.agents import orchestrator
        with db() as s:
            s.get(Document, apple_doc).status = "failed"
        with patch.object(orchestrator, "run_document_agent"):
            orchestrator.run_pipeline(apple_doc)
        with db() as s:
            d = s.get(Document, apple_doc)
            assert (d.extraction_status, d.red_flag_status) == ("skipped", "skipped")
