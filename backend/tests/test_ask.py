"""
tests/test_ask.py
Ask & report: grounding checks, the five report sections, the report agent, the
chat agent and the API. A fake model and an in-memory SQLite database: no
network, no API keys.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import llm
from app.agents.chat_agent import graph as chat
from app.agents.chat_agent.classify import match_metric, mentions_other_company, needs_rewrite, smalltalk
from app.agents.report_agent import graph as rg
from app.agents.report_agent import sections as sec
from app.agents.report_agent.pdf import build_pdf
from app.agents.report_agent.graph import get_report_for_pdf
from app.db.models import Base, ChatMessage, Document, Metric, PageTag, RedFlag, Report, Session
from app.embeddings import QuotaExhaustedError
from app.grounding import fill_placeholders, fmt_value, norm, numbers_supported, quote_on_page

MDNA = ("Infosys is a global leader in next-generation digital services and consulting. "
        "We operate in North America, Europe and India. Management expects demand for AI "
        "services to remain strong in the coming year.")
PL = "Revenue from operations 1,53,670 1,46,767\nProfit for the year 26,713 24,095"


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def env(monkeypatch):
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

    for mod in ("app.pages", "app.agents.report_agent.data", "app.agents.report_agent.graph",
                "app.agents.chat_agent.graph", "app.api.routes_ask"):
        monkeypatch.setattr(f"{mod}.session_scope", scope)
    return SimpleNamespace(scope=scope, maker=maker)


def _metric(doc_id, name, value, year, page, unit="INR crore", kind="extracted", status="verified",
            source_text=None, formula=None):
    return Metric(document_id=doc_id, name=name, value=value, unit=unit, year=year, source_page=page,
                  kind=kind, status=status, source_text=source_text, formula=formula)


def _make_doc(s, session_id, company, fy=2025, ready=True, with_data=True):
    doc = Document(id=uuid.uuid4(), session_id=session_id, filename=f"{company}.pdf",
                   storage_key=f"k{uuid.uuid4()}", company=company, fiscal_year=fy,
                   status="ready" if ready else "indexing", page_count=9,
                   extraction_status="done" if ready else None,
                   red_flag_status="done" if ready else None)
    s.add(doc)
    s.flush()
    if with_data:
        s.add(PageTag(document_id=doc.id, pdf_page=2, section="mdna_narrative", scope="consolidated",
                      content_type="narrative", is_image_only=False, tier=1, text=MDNA))
        s.add(PageTag(document_id=doc.id, pdf_page=5, section="profit_loss", scope="consolidated",
                      unit="INR crore", content_type="table", is_image_only=False, tier=1, text=PL))
        s.add_all([
            _metric(doc.id, "revenue", 153670, fy, 5, source_text="Revenue from operations 1,53,670 1,46,767"),
            _metric(doc.id, "revenue", 146767, fy - 1, 5),
            _metric(doc.id, "net_profit", 26713, fy, 5, source_text="Profit for the year 26,713 24,095"),
            _metric(doc.id, "net_profit", 24095, fy - 1, 5),
            _metric(doc.id, "operating_margin_pct", 21.1, fy, 5, unit="%", kind="ratio",
                    formula="operating_profit / revenue x 100"),
            _metric(doc.id, "operating_margin_pct", 20.0, fy - 1, 5, unit="%", kind="ratio"),
            _metric(doc.id, "net_margin_pct", 17.4, fy, 5, unit="%", kind="ratio"),
            _metric(doc.id, "revenue_growth_pct", 4.7, fy, 5, unit="%", kind="ratio"),
            _metric(doc.id, "total_assets", 99.0, fy, 5, status="needs_review"),
        ])
        s.add(RedFlag(document_id=doc.id, type="falling_margin", severity="Medium", title="Margin fell",
                      description="Operating margin fell.", evidence="20.0% -> 21.1%", source_page=5))
    return doc


@pytest.fixture()
def world(env):
    """A session with INFOSYS FY2025 (analysed) and TCS FY2025 (peer)."""
    with env.scope() as s:
        sess = Session(name="s")
        s.add(sess)
        s.flush()
        infy = _make_doc(s, sess.id, "INFOSYS")
        tcs = _make_doc(s, sess.id, "TCS")
        # make the peer's numbers differ
        for m in s.query(Metric).filter(Metric.document_id == tcs.id, Metric.name == "operating_margin_pct",
                                        Metric.year == 2025):
            m.value = 25.0
        return SimpleNamespace(session_id=sess.id, infy=infy.id, tcs=tcs.id)


# ── grounding ─────────────────────────────────────────────────────────────────

class TestGrounding:
    def test_quote_survives_line_wraps_and_curly_quotes(self):
        page = "The company’s opera-\nting margin was\n  healthy this year."
        assert quote_on_page("company's operating margin was healthy", page)

    def test_quote_not_on_page_or_too_short(self):
        assert not quote_on_page("revenue doubled overnight", MDNA)
        assert not quote_on_page("AI", MDNA)
        assert not quote_on_page(None, MDNA)

    def test_fill_placeholders(self):
        vals = {"revenue": "1,000 INR crore"}
        assert fill_placeholders("Revenue was {revenue}.", vals) == "Revenue was 1,000 INR crore."
        assert fill_placeholders("Margin was {operating_margin_pct}.", vals) is None

    def test_numbers_supported(self):
        assert numbers_supported("It operates in 3 regions", "We have 3 regions")
        assert not numbers_supported("Revenue rose 99%", "Revenue rose 5%")
        assert numbers_supported("In 2025 revenue was {revenue}", "no digits", allowed=(2025,))

    @pytest.mark.parametrize("value,unit,expected", [
        (153670.0, "INR crore", "153,670 INR crore"), (12.3, "%", "12.3%"), (1.5, "x", "1.5x"),
        (7.0, None, "7")])
    def test_fmt_value(self, value, unit, expected):
        assert fmt_value(value, unit) == expected

    def test_norm_flattens(self):
        assert norm("A–B  C") == "a-b c"


# ── sections ──────────────────────────────────────────────────────────────────

def _m(name, cur, pri=None, unit="INR crore", page=5, status="verified", kind="extracted"):
    rows = [{"name": name, "period": "current", "value": cur, "unit": unit, "page": page,
             "status": status, "kind": kind, "formula": None, "source_text": None, "note": None}]
    if pri is not None:
        rows.append({**rows[0], "period": "prior", "value": pri})
    return rows


class TestSections:
    PAGES = {2: MDNA}
    VALUES = {"revenue": "1,53,670 INR crore"}

    def _draft(self, *sentences):
        return sec.SectionDraft(sentences=[sec.DraftSentence(text=t, page=p, quote=q) for t, p, q in sentences])

    def test_good_sentence_is_kept_and_placeholder_filled(self):
        d = self._draft(("Revenue was {revenue}.", 2, None),
                        ("It operates in North America, Europe and India.", 2, "North America, Europe and India"))
        kept, dropped = sec.check_sentences(d, self.PAGES, self.VALUES, 2025)
        assert dropped == 0
        assert kept[0]["text"] == "Revenue was 1,53,670 INR crore."
        assert kept[1]["quote"] == "North America, Europe and India"

    @pytest.mark.parametrize("sentence", [
        ("Revenue was {nonexistent}.", 2, None),                    # unknown placeholder
        ("Revenue grew 99% last year.", 2, None),                   # invented number
        ("It is a global leader.", 7, None),                        # page was not provided
        ("It is a global leader.", 2, "a completely made up quote"),  # quote not on the page
    ])
    def test_bad_sentences_are_dropped(self, sentence):
        kept, dropped = sec.check_sentences(self._draft(sentence), self.PAGES, self.VALUES, 2025)
        assert kept == [] and dropped == 1

    def test_fiscal_year_is_allowed_in_text(self):
        kept, _ = sec.check_sentences(self._draft(("In 2025 the firm grew.", 2, None)), self.PAGES, {}, 2025)
        assert len(kept) == 1

    def test_placeholder_values_use_verified_only(self):
        rows = _m("revenue", 100.0, 90.0) + _m("total_assets", 5.0, status="needs_review")
        vals = sec.placeholder_values(rows)
        assert vals["revenue"] == "100 INR crore" and vals["revenue__prior"] == "90 INR crore"
        assert "total_assets" not in vals

    def test_metrics_section_tables_change_and_review_flag(self):
        rows = (_m("revenue", 110.0, 100.0) + _m("total_assets", 5.0, status="needs_review")
                + _m("operating_margin_pct", 21.0, 20.0, unit="%", kind="ratio")
                + _m("revenue_growth_pct", 10.0, unit="%", kind="ratio"))
        s = sec.build_metrics_section(rows)
        figures, ratios = s["tables"]
        by = {r["cells"][0]: r for r in figures["rows"] + ratios["rows"]}
        assert by["Revenue"]["cells"][3] == "+10.0%"
        assert by["Operating margin"]["cells"][3] == "+1.0 pp"
        assert by["Revenue growth"]["cells"][2] == "—"
        assert by["Total assets"]["warn"] and "needs review" in by["Total assets"]["cells"][1]
        assert s["note"]

    def test_metrics_section_empty(self):
        assert sec.build_metrics_section([])["status"] == "unavailable"

    def test_peers_without_peers_says_upload_another(self):
        s = sec.build_peers_section("INFOSYS", 2025, [], [])
        assert s["status"] == "ready" and "Upload another company" in s["note"]

    def test_peers_rank_and_year_suffix(self):
        mine = _m("operating_margin_pct", 21.0, unit="%", kind="ratio")
        theirs = {"company": "TCS", "fiscal_year": 2024, "metrics": _m("operating_margin_pct", 25.0, unit="%", kind="ratio")}
        s = sec.build_peers_section("INFOSYS", 2025, mine, [theirs])
        assert s["tables"][0]["columns"] == ["Ratio", "INFOSYS", "TCS (FY2024)"]
        assert "ranks 2nd of 2" in s["sentences"][0]["text"] and s["sentences"][0]["page"] == 5

    def test_peers_lower_debt_ratio_is_better(self):
        mine = _m("debt_to_equity", 0.2, unit="x", kind="ratio")
        theirs = {"company": "TCS", "fiscal_year": 2025, "metrics": _m("debt_to_equity", 0.9, unit="x", kind="ratio")}
        s = sec.build_peers_section("INFOSYS", 2025, mine, [theirs])
        assert "ranks 1st of 2" in s["sentences"][0]["text"]

    def test_peers_tie_is_called_a_tie(self):
        mine = _m("current_ratio", 2.0, unit="x", kind="ratio")
        theirs = {"company": "TCS", "fiscal_year": 2025, "metrics": _m("current_ratio", 2.0, unit="x", kind="ratio")}
        s = sec.build_peers_section("INFOSYS", 2025, mine, [theirs])
        assert "tied for 1st of 2" in s["sentences"][0]["text"]

    def test_flags_section(self):
        flags = [{"title": "T", "severity": "High", "description": "d", "evidence": "e", "page": 3, "type": "x"}]
        assert sec.build_flags_section(flags, "done")["flags"][0]["title"] == "T"
        assert "not a guarantee" in sec.build_flags_section([], "done")["note"]
        assert sec.build_flags_section([], "failed")["status"] == "unavailable"


# ── classify ──────────────────────────────────────────────────────────────────

class TestClassify:
    @pytest.mark.parametrize("q,expected", [
        ("What is the revenue?", ("revenue", "current")),
        ("what was revenue growth", ("revenue_growth_pct", "current")),
        ("net profit last year", ("net_profit", "prior")),
        ("What was the operating margin in FY2024?", ("operating_margin_pct", "prior")),
        ("What is the debt-to-equity ratio", ("debt_to_equity", "current")),
        ("current ratio?", ("current_ratio", "current")),
    ])
    def test_numeric_lookups(self, q, expected):
        assert match_metric(q, 2025) == expected

    @pytest.mark.parametrize("q", [
        "Why did revenue fall?", "Explain the margin drivers", "What are the key risks?",
        "revenue in 2019", "Who is the CEO?", "compare revenue with last quarter"])
    def test_not_numeric(self, q):
        assert match_metric(q, 2025) is None

    def test_other_company(self):
        others = ["INFOSYS", "TATA MOTORS", "TCS"]
        assert mentions_other_company("How is TCS doing?", "INFOSYS", others) == "TCS"
        assert mentions_other_company("compare with Tata", "INFOSYS", others) == "TATA MOTORS"
        assert mentions_other_company("revenue of Infosys", "INFOSYS", others) is None
        assert mentions_other_company("What about TCS-like peers?", "TCS", others) is None

    @pytest.mark.parametrize("q,kind", [
        ("hi", "greeting"), ("Hello!", "greeting"), ("hiii", "greeting"), ("Good morning", "greeting"),
        ("how are you?", "how_are_you"), ("thanks", "thanks"), ("Thank you!", "thanks"), ("ok thanks", "thanks"),
        ("bye", "bye"), ("what can you do?", "about"), ("help", "about"), ("who are you", "about")])
    def test_smalltalk(self, q, kind):
        assert smalltalk(q) == kind

    @pytest.mark.parametrize("q", [
        "hi what is the revenue", "What is the revenue?", "hello TCS", "thanks, but why did margin fall",
        "tell me a joke about the company and its many subsidiaries today", ""])
    def test_not_smalltalk(self, q):
        assert smalltalk(q) is None

    def test_needs_rewrite(self):
        assert not needs_rewrite("What about it?", has_history=False)
        assert needs_rewrite("and last year?", has_history=True)
        assert needs_rewrite("Why did that change over the period of the year in the segments?", True)
        assert not needs_rewrite("What is the company's revenue recognition policy for fixed price contracts?", True)


# ── report agent ──────────────────────────────────────────────────────────────

def fake_writer(bad: bool = False):
    """A model that writes one good sentence per call (or an unsupported one)."""
    def call(schema, system, user):
        assert schema is sec.SectionDraft
        if bad:
            return sec.SectionDraft(sentences=[sec.DraftSentence(text="Revenue exploded 900%.", page=2, quote=None)])
        return sec.SectionDraft(sentences=[
            sec.DraftSentence(text="Revenue was {revenue}.", page=2, quote=None),
            sec.DraftSentence(text="It operates in North America, Europe and India.", page=2,
                              quote="North America, Europe and India"),
        ])
    return patch.object(llm, "call_structured", side_effect=call)


@pytest.fixture()
def no_vectors():
    with patch("app.agents.report_agent.graph.retrieval.search", return_value=[]):
        yield


class TestReportAgent:
    def test_full_run_saves_the_two_written_sections(self, world, no_vectors):
        rid, run = rg.start_report(world.session_id, "infosys", 2025)
        assert run
        with fake_writer():
            assert rg.run_report(rid)
        rep = rg.get_report(world.session_id, "Infosys", 2025)
        assert rep["status"] == "ready" and rep["company"] == "INFOSYS"
        assert [s["key"] for s in rep["sections"]] == ["snapshot", "outlook"]
        assert rep["progress"] == {"done": 2, "total": 2}
        assert rep["sections"][0]["sentences"][0]["text"] == "Revenue was 153,670 INR crore."

    def test_metrics_peers_and_red_flags_are_not_in_the_report(self, world, no_vectors):
        rid, _ = rg.start_report(world.session_id, "INFOSYS", 2025)
        with fake_writer():
            rg.run_report(rid)
        keys = {s["key"] for s in rg.get_report(world.session_id, "INFOSYS", 2025)["sections"]}
        assert keys.isdisjoint({"metrics", "peers", "red_flags"})

    def test_old_saved_reports_hide_removed_sections(self, env, world):
        rid, _ = rg.start_report(world.session_id, "INFOSYS", 2025)
        with env.scope() as s:
            row = s.get(Report, rid)
            row.status = "ready"
            row.sections = [{"key": k, "title": k, "status": "ready", "sentences": [], "tables": [],
                             "flags": [], "note": None} for k in ("snapshot", "metrics", "peers", "red_flags", "outlook")]
        rep = rg.get_report(world.session_id, "INFOSYS", 2025)
        assert [s["key"] for s in rep["sections"]] == ["snapshot", "outlook"]
        assert build_pdf(get_report_for_pdf(world.session_id, "INFOSYS", 2025)).startswith(b"%PDF")

    def test_progress_is_saved_section_by_section(self, world, no_vectors):
        rid, _ = rg.start_report(world.session_id, "INFOSYS", 2025)
        seen = []

        def spy(report_id, section):
            seen.append(section["key"])
            real(report_id, section)
        real = rg._save_section
        with fake_writer(), patch.object(rg, "_save_section", side_effect=spy):
            rg.run_report(rid)
        assert seen == ["snapshot", "outlook"]

    def test_second_start_uses_cache_unless_regenerate(self, world, no_vectors):
        rid, _ = rg.start_report(world.session_id, "INFOSYS", 2025)
        with fake_writer():
            rg.run_report(rid)
        again, run = rg.start_report(world.session_id, "INFOSYS", 2025)
        assert again == rid and run is False
        regen, run = rg.start_report(world.session_id, "INFOSYS", 2025, regenerate=True)
        assert regen == rid and run is True
        assert rg.get_report(world.session_id, "INFOSYS", 2025)["sections"] == []

    def test_running_report_is_not_started_twice(self, world):
        rid, _ = rg.start_report(world.session_id, "INFOSYS", 2025)
        _, run = rg.start_report(world.session_id, "INFOSYS", 2025)
        assert run is False

    def test_unsupported_text_is_removed_and_section_unavailable(self, world, no_vectors):
        rid, _ = rg.start_report(world.session_id, "INFOSYS", 2025)
        with fake_writer(bad=True):
            rg.run_report(rid)
        rep = rg.get_report(world.session_id, "INFOSYS", 2025)
        assert rep["status"] == "ready"      # nothing crashed; the section just says why
        assert rep["sections"][0]["status"] == "unavailable"

    def test_model_failure_gives_partial_report(self, world, no_vectors):
        rid, _ = rg.start_report(world.session_id, "INFOSYS", 2025)
        with patch.object(llm, "call_structured", side_effect=RuntimeError("boom")):
            assert rg.run_report(rid)
        rep = rg.get_report(world.session_id, "INFOSYS", 2025)
        assert rep["status"] == "partial" and "Regenerate" in rep["error"]
        assert "boom" not in rep["error"]               # raw errors never reach the screen
        assert {s["key"]: s["status"] for s in rep["sections"]} == {
            "snapshot": "unavailable", "outlook": "unavailable"}

    def test_quota_exhausted_skips_second_llm_call(self, world, no_vectors):
        rid, _ = rg.start_report(world.session_id, "INFOSYS", 2025)
        with patch.object(llm, "call_structured", side_effect=QuotaExhaustedError("daily")) as m:
            rg.run_report(rid)
        assert m.call_count == 1
        rep = rg.get_report(world.session_id, "INFOSYS", 2025)
        assert rep["status"] == "partial" and "quota" in rep["sections"][-1]["note"].lower()
        assert "usage limit" in rep["error"] and "QuotaExhausted" not in rep["error"]

    def test_not_found_and_not_ready(self, env, world):
        with pytest.raises(rg.NotFound):
            rg.start_report(world.session_id, "NOPE", 2025)
        with env.scope() as s:
            _make_doc(s, world.session_id, "SLOW", ready=False, with_data=False)
        with pytest.raises(rg.NotReady):
            rg.start_report(world.session_id, "slow", 2025)

    def test_stale_running_report_is_marked_failed(self, env, world):
        rid, _ = rg.start_report(world.session_id, "INFOSYS", 2025)
        with env.scope() as s:
            s.get(Report, rid).updated_at = datetime.now(timezone.utc) - timedelta(minutes=30)
        rep = rg.get_report(world.session_id, "INFOSYS", 2025)
        assert rep["status"] == "failed" and "Regenerate" in rep["error"]

    def test_vector_store_down_does_not_break_report(self, world):
        rid, _ = rg.start_report(world.session_id, "INFOSYS", 2025)
        with fake_writer(), patch("app.agents.report_agent.graph.retrieval.search", side_effect=RuntimeError("down")):
            rg.run_report(rid)
        assert rg.get_report(world.session_id, "INFOSYS", 2025)["sections"][0]["status"] == "ready"

    def test_pdf_builds_from_a_real_report(self, world, no_vectors):
        rid, _ = rg.start_report(world.session_id, "INFOSYS", 2025)
        with fake_writer():
            rg.run_report(rid)
        pdf = build_pdf(get_report_for_pdf(world.session_id, "INFOSYS", 2025))
        assert pdf.startswith(b"%PDF") and len(pdf) > 3000

    def test_pdf_has_all_five_sections_but_the_screen_has_two(self, world, no_vectors):
        rid, _ = rg.start_report(world.session_id, "INFOSYS", 2025)
        with fake_writer():
            rg.run_report(rid)
        screen = [s["key"] for s in rg.get_report(world.session_id, "INFOSYS", 2025)["sections"]]
        full = get_report_for_pdf(world.session_id, "INFOSYS", 2025)
        assert screen == ["snapshot", "outlook"]
        assert [s["key"] for s in full["sections"]] == ["snapshot", "metrics", "peers", "red_flags", "outlook"]
        glance = {h["label"]: h for h in full["sections"][1]["highlights"]}
        assert glance["Revenue"]["value"] == "153,670 INR crore" and glance["Revenue"]["change"] == "+4.7%"

    def test_pdf_leaves_out_peers_when_there_is_nobody_to_compare(self, env, world, no_vectors):
        with env.scope() as s:
            s.query(Document).filter(Document.id == world.tcs).delete()
        rid, _ = rg.start_report(world.session_id, "INFOSYS", 2025)
        with fake_writer():
            rg.run_report(rid)
        keys = [s["key"] for s in get_report_for_pdf(world.session_id, "INFOSYS", 2025)["sections"]]
        assert keys == ["snapshot", "metrics", "red_flags", "outlook"]

    def test_pdf_works_for_a_report_saved_before_this_change(self, env, world):
        rid, _ = rg.start_report(world.session_id, "INFOSYS", 2025)
        with env.scope() as s:
            row = s.get(Report, rid)
            row.status = "ready"
            row.sections = [{"key": "snapshot", "title": "Company snapshot", "status": "ready", "tables": [],
                             "flags": [], "note": None, "sentences": [{"text": "Hi.", "page": 2, "quote": None}]}]
        full = get_report_for_pdf(world.session_id, "INFOSYS", 2025)
        assert "metrics" in [s["key"] for s in full["sections"]] and build_pdf(full).startswith(b"%PDF")


# ── chat agent ────────────────────────────────────────────────────────────────

def _hit(page):
    return SimpleNamespace(metadata={"pdf_page": str(page)}, score=0.9, text="x")


def answer_with(*answers):
    seq = iter(answers)
    return patch.object(llm, "call_structured", side_effect=lambda schema, s, u: next(seq))


GOOD = chat.Answer(found=True, answer="It operates in North America, Europe and India.",
                   citations=[chat.Cite(page=2, quote="North America, Europe and India")])


@pytest.fixture()
def vectors():
    with patch("app.agents.chat_agent.graph.retrieval.search", return_value=[_hit(2), _hit(2), _hit(5)]), \
            patch("app.lazy_index.index_more", return_value=[]):
        yield


class TestChatAgent:
    def test_plain_number_answers_from_metrics_without_the_model(self, world):
        with patch.object(llm, "call_structured", side_effect=AssertionError("no LLM call expected")):
            msg = chat.ask(world.session_id, "infosys", 2025, "What is the revenue?")
        assert msg["kind"] == "numeric" and "153,670 INR crore" in msg["content"]
        assert msg["citations"] == [{"pdf_page": 5, "quote": "Revenue from operations 1,53,670 1,46,767", "verified": True}]

    def test_prior_year_and_calculated_ratio(self, world):
        msg = chat.ask(world.session_id, "INFOSYS", 2025, "operating margin last year")
        assert "FY2024" in msg["content"] and "20" in msg["content"]
        msg = chat.ask(world.session_id, "INFOSYS", 2025, "What is the operating margin?")
        assert "Calculated: operating_profit / revenue" in msg["content"]

    def test_unreviewed_metric_carries_a_warning(self, world):
        msg = chat.ask(world.session_id, "INFOSYS", 2025, "total assets")
        assert "automatic consistency check" in msg["content"]

    def test_greeting_gets_a_friendly_reply_without_the_model(self, world):
        with patch.object(llm, "call_structured", side_effect=AssertionError("no LLM call expected")):
            msg = chat.ask(world.session_id, "INFOSYS", 2025, "hi")
        assert msg["kind"] == "smalltalk" and "INFOSYS" in msg["content"] and msg["citations"] == []
        hist = chat.history(world.session_id, "INFOSYS", 2025)
        assert [(m["role"], m["kind"]) for m in hist] == [("user", "question"), ("assistant", "smalltalk")]

    def test_other_company_is_refused_without_the_model(self, world):
        with patch.object(llm, "call_structured", side_effect=AssertionError("no LLM call expected")):
            msg = chat.ask(world.session_id, "INFOSYS", 2025, "What is TCS revenue?")
        assert msg["kind"] == "other_company" and "TCS" in msg["content"] and "INFOSYS" in msg["content"]

    def test_grounded_answer_with_verified_citation(self, world, vectors):
        with answer_with(GOOD):
            msg = chat.ask(world.session_id, "INFOSYS", 2025, "Where does the company operate?")
        assert msg["kind"] == "answer"
        assert msg["citations"] == [{"pdf_page": 2, "quote": "North America, Europe and India", "verified": True}]

    def test_not_found_when_model_says_so(self, world, vectors):
        with answer_with(chat.Answer(found=False)):
            msg = chat.ask(world.session_id, "INFOSYS", 2025, "Who is the CEO?")
        assert msg["kind"] == "not_found" and msg["citations"] == []
        assert "INFOSYS" in msg["content"] and "won't guess" in msg["content"]

    def test_off_topic_gets_a_polite_redirect(self, world, vectors):
        with answer_with(chat.Answer(found=False, off_topic=True)):
            msg = chat.ask(world.session_id, "INFOSYS", 2025, "Tell me a joke")
        assert msg["kind"] == "off_topic" and "outside what I can help with" in msg["content"]

    def test_not_found_when_nothing_retrieved(self, world):
        with patch("app.agents.chat_agent.graph.retrieval.search", return_value=[]), \
                patch("app.lazy_index.index_more", return_value=[]), \
                patch("app.agents.chat_agent.graph.find_pages", return_value=[]), \
                patch.object(llm, "call_structured", side_effect=AssertionError("must not ask the model")):
            msg = chat.ask(world.session_id, "INFOSYS", 2025, "Tell me about dividends")
        assert msg["kind"] == "not_found"

    def test_bad_quote_is_retried_once_then_succeeds(self, world, vectors):
        bad = chat.Answer(found=True, answer="They operate worldwide.", citations=[chat.Cite(page=2, quote="invented text here")])
        with answer_with(bad, GOOD) as m:
            msg = chat.ask(world.session_id, "INFOSYS", 2025, "Where does the company operate?")
        assert msg["kind"] == "answer" and m.call_count == 2

    def test_still_bad_after_retry_is_marked_unverified(self, world, vectors):
        bad = chat.Answer(found=True, answer="They operate worldwide.", citations=[chat.Cite(page=2, quote="invented text here")])
        with answer_with(bad, bad) as m:
            msg = chat.ask(world.session_id, "INFOSYS", 2025, "Where does the company operate?")
        assert msg["kind"] == "unverified" and m.call_count == 2
        assert msg["citations"][0]["verified"] is False

    def test_invented_number_fails_verification(self, world, vectors):
        bad = chat.Answer(found=True, answer="They operate in 47 countries.",
                          citations=[chat.Cite(page=2, quote="North America, Europe and India")])
        with answer_with(bad, bad):
            msg = chat.ask(world.session_id, "INFOSYS", 2025, "How many countries?")
        assert msg["kind"] == "unverified"

    def test_history_saved_in_order_and_follow_up_is_rewritten(self, world, vectors):
        with answer_with(GOOD):
            chat.ask(world.session_id, "INFOSYS", 2025, "Where does the company operate?")
        seen = []

        def fake(schema, system, user):
            seen.append(schema.__name__)
            return chat.Rewrite(question="Where does Infosys operate in Europe?") if schema is chat.Rewrite else GOOD
        with patch.object(llm, "call_structured", side_effect=fake):
            chat.ask(world.session_id, "INFOSYS", 2025, "and in Europe?")
        assert seen == ["Rewrite", "Answer"]
        hist = chat.history(world.session_id, "infosys", 2025)
        assert [(m["role"], m["kind"]) for m in hist] == [
            ("user", "question"), ("assistant", "answer"), ("user", "question"), ("assistant", "answer")]
        assert hist[2]["content"] == "and in Europe?"        # the user's own words are stored

    def test_chat_is_per_company(self, world, vectors):
        with answer_with(GOOD):
            chat.ask(world.session_id, "INFOSYS", 2025, "Where does the company operate?")
        assert chat.history(world.session_id, "TCS", 2025) == []

    def test_clear_history(self, world):
        chat.ask(world.session_id, "INFOSYS", 2025, "revenue")
        assert chat.clear_history(world.session_id, "INFOSYS", 2025) == 2
        assert chat.history(world.session_id, "INFOSYS", 2025) == []

    def test_not_ready_and_unknown_company(self, env, world):
        with env.scope() as s:
            _make_doc(s, world.session_id, "SLOW", ready=False, with_data=False)
        with pytest.raises(rg.NotReady):
            chat.ask(world.session_id, "SLOW", 2025, "revenue")
        with pytest.raises(rg.NotFound):
            chat.ask(world.session_id, "NOPE", 2025, "revenue")
        with pytest.raises(ValueError):
            chat.ask(world.session_id, "INFOSYS", 2025, "   ")


# ── API ───────────────────────────────────────────────────────────────────────

@pytest.fixture()
def client(env):
    from app.api.main import app
    from app.api.routes_ask import _db

    def override():
        s = env.maker()
        try:
            yield s
        finally:
            s.close()
    app.dependency_overrides[_db] = override
    with patch("app.api.main.ping_vector_store", return_value=True), \
            patch("app.api.main.check_connection", return_value=True), \
            patch("app.api.main._seed_in_background", create=True), \
            TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


class TestApi:
    def test_companies_lists_names_years_and_readiness(self, client, env, world):
        with env.scope() as s:
            _make_doc(s, world.session_id, "SLOW", fy=2024, ready=False, with_data=False)
        body = client.get(f"/sessions/{world.session_id}/companies").json()
        assert [c["company"] for c in body] == ["INFOSYS", "SLOW", "TCS"]
        slow = next(c for c in body if c["company"] == "SLOW")["years"][0]
        assert slow["ready"] is False and "processing" in slow["reason"]
        assert body[0]["years"][0]["ready"] is True

    def test_unknown_session_404(self, client):
        assert client.get(f"/sessions/{uuid.uuid4()}/companies").status_code == 404

    def test_report_state_generate_and_pdf(self, client, world, no_vectors):
        base = f"/sessions/{world.session_id}"
        q = {"company": "infosys", "fiscal_year": 2025}
        state = client.get(f"{base}/report", params=q).json()
        assert state["company"] == "INFOSYS" and state["readiness"]["ready"] and state["report"] is None
        assert client.get(f"{base}/report/pdf", params=q).status_code == 409

        with fake_writer():
            r = client.post(f"{base}/report/generate", params=q)       # background task runs before the response returns
        assert r.status_code == 202
        rep = client.get(f"{base}/report", params=q).json()["report"]
        assert rep["status"] == "ready" and rep["progress"]["done"] == 2

        pdf = client.get(f"{base}/report/pdf", params=q)
        assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
        assert "INFOSYS_FY2025_report.pdf" in pdf.headers["content-disposition"]

    def test_generate_errors(self, client, env, world):
        base = f"/sessions/{world.session_id}"
        assert client.post(f"{base}/report/generate", params={"company": "NOPE", "fiscal_year": 2025}).status_code == 404
        with env.scope() as s:
            _make_doc(s, world.session_id, "SLOW", ready=False, with_data=False)
        r = client.post(f"{base}/report/generate", params={"company": "SLOW", "fiscal_year": 2025})
        assert r.status_code == 409 and "processing" in r.json()["detail"]

    def test_chat_roundtrip(self, client, world):
        base = f"/sessions/{world.session_id}/chat"
        r = client.post(base, json={"company": "Infosys", "fiscal_year": 2025, "question": "What is the revenue?"})
        assert r.status_code == 200 and r.json()["kind"] == "numeric"
        hist = client.get(base, params={"company": "INFOSYS", "fiscal_year": 2025}).json()
        assert [m["role"] for m in hist] == ["user", "assistant"]
        assert client.delete(base, params={"company": "INFOSYS", "fiscal_year": 2025}).status_code == 204
        assert client.get(base, params={"company": "INFOSYS", "fiscal_year": 2025}).json() == []

    def test_chat_model_errors_are_clean(self, client, world, vectors):
        base = f"/sessions/{world.session_id}/chat"
        body = {"company": "INFOSYS", "fiscal_year": 2025, "question": "Where does it operate?"}
        with patch.object(llm, "call_structured", side_effect=QuotaExhaustedError("daily quota")):
            assert client.post(base, json=body).status_code == 503
        with patch.object(llm, "call_structured", side_effect=RuntimeError("boom")):
            r = client.post(base, json=body)
        assert r.status_code == 502 and "boom" not in r.text
        assert client.get(base, params={"company": "INFOSYS", "fiscal_year": 2025}).json() == []   # nothing saved

    def test_chat_validation(self, client, world):
        base = f"/sessions/{world.session_id}/chat"
        assert client.post(base, json={"company": "INFOSYS", "fiscal_year": 2025, "question": ""}).status_code == 422
        r = client.post(base, json={"company": "NOPE", "fiscal_year": 2025, "question": "revenue"})
        assert r.status_code == 404
