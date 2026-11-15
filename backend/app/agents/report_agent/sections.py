"""
The five report sections.

  snapshot   LLM writes, every sentence cites a page          (in the report)
  outlook    LLM writes from MD&A text + verified metrics     (in the report)
  metrics / peers / red_flags are built from the database when the PDF is downloaded.

A section is a plain dict (stored as JSON in ``reports.sections``):

  {"key", "title", "status": "ready" | "unavailable",
   "sentences": [{"text", "page", "quote"}],
   "tables":    [{"title", "columns": [...], "rows": [{"cells": [...], "page", "warn"}]}],
   "flags":     [{"title", "severity", "description", "evidence", "page"}],
   "note":      str | None}

The LLM never types a financial figure: it writes ``{metric_id}`` placeholders
and code fills in the verified value. Numbers it types anyway must be printed on
the page it cites, and quotes must appear on that page - otherwise the sentence
is dropped.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from pydantic import BaseModel, Field

from app import llm
from app.config import settings
from app.grounding import (
    fill_placeholders,
    fit_pages,
    fmt_value,
    numbers_supported,
    quote_on_page,
)

logger = logging.getLogger(__name__)

# The screen shows only the two written sections (metrics, peers and red flags have their
# own pages). The PDF adds those three, built fresh at download time (see graph.get_report_for_pdf).
SECTION_ORDER = [
    ("snapshot", "Company snapshot"),
    ("outlook", "Outlook and summary"),
]
TITLES = {**dict(SECTION_ORDER), "metrics": "Key metrics and ratios",
          "peers": "Peer comparison", "red_flags": "Red flags"}

LABELS = {
    "revenue": "Revenue", "gross_profit": "Gross profit", "operating_profit": "Operating profit",
    "net_profit": "Net profit", "eps": "Earnings per share",
    "total_assets": "Total assets", "total_liabilities": "Total liabilities",
    "total_equity": "Total equity", "current_assets": "Current assets",
    "current_liabilities": "Current liabilities", "cash": "Cash and equivalents",
    "receivables": "Receivables", "total_debt": "Total debt",
    "operating_cash_flow": "Operating cash flow", "capex": "Capital expenditure",
    "free_cash_flow": "Free cash flow",
    "gross_margin_pct": "Gross margin", "operating_margin_pct": "Operating margin",
    "net_margin_pct": "Net margin", "roe_pct": "Return on equity",
    "debt_to_equity": "Debt to equity", "current_ratio": "Current ratio",
    "ocf_to_net_profit": "Operating cash flow / net profit",
    "revenue_growth_pct": "Revenue growth", "net_profit_growth_pct": "Net profit growth",
    "receivables_growth_pct": "Receivables growth", "total_debt_growth_pct": "Total debt growth",
}
FIGURES = ["revenue", "gross_profit", "operating_profit", "net_profit", "eps", "total_assets",
           "total_liabilities", "total_equity", "current_assets", "current_liabilities", "cash",
           "receivables", "total_debt", "operating_cash_flow", "capex", "free_cash_flow"]
RATIOS = ["gross_margin_pct", "operating_margin_pct", "net_margin_pct", "roe_pct", "debt_to_equity",
          "current_ratio", "ocf_to_net_profit", "revenue_growth_pct", "net_profit_growth_pct",
          "receivables_growth_pct", "total_debt_growth_pct"]
HIGHLIGHTS = ["revenue", "net_profit", "operating_margin_pct", "net_margin_pct", "roe_pct", "debt_to_equity"]
GROWTH_ONLY_CURRENT = {"revenue_growth_pct", "net_profit_growth_pct", "receivables_growth_pct",
                       "total_debt_growth_pct"}
# Ratios compared across companies, and whether a higher value is better.
PEER_RATIOS = {
    "revenue_growth_pct": True, "operating_margin_pct": True, "net_margin_pct": True,
    "roe_pct": True, "debt_to_equity": False, "current_ratio": True, "ocf_to_net_profit": True,
}
_ORDINAL = {1: "1st", 2: "2nd", 3: "3rd"}


def _section(key: str, **kw: Any) -> dict[str, Any]:
    base = {"key": key, "title": TITLES[key], "status": "ready",
            "sentences": [], "tables": [], "flags": [], "highlights": [], "note": None}
    base.update(kw)
    return base


def unavailable(key: str, note: str) -> dict[str, Any]:
    return _section(key, status="unavailable", note=note)


def _index(metrics: list[dict]) -> dict[tuple, dict]:
    return {(m["name"], m["period"]): m for m in metrics if m["value"] is not None}


def _verified(idx: dict[tuple, dict], name: str, period: str = "current") -> Optional[dict]:
    m = idx.get((name, period))
    return m if m and m["status"] == "verified" else None


# ── 2. key metrics (no LLM) ───────────────────────────────────────────────────

def _change(cur: dict, pri: Optional[dict]) -> str:
    if pri is None or cur["status"] != "verified" or pri["status"] != "verified":
        return "—"
    if cur["unit"] in ("%", "x"):
        diff = cur["value"] - pri["value"]
        return f"{diff:+.1f} {'pp' if cur['unit'] == '%' else 'x'}"
    if not pri["value"] or pri["value"] < 0 or cur["value"] < 0:
        return "—"
    return f"{(cur['value'] / pri['value'] - 1) * 100:+.1f}%"


def build_metrics_section(metrics: list[dict]) -> dict[str, Any]:
    idx = _index(metrics)

    def rows_for(names: list[str]) -> list[dict]:
        out = []
        for name in names:
            cur = idx.get((name, "current"))
            if cur is None:
                continue
            pri = idx.get((name, "prior")) if name not in GROWTH_ONLY_CURRENT else None
            warn = cur["status"] != "verified"
            out.append({
                "cells": [
                    LABELS.get(name, name),
                    fmt_value(cur["value"], cur["unit"]) + (" (needs review)" if warn else ""),
                    fmt_value(pri["value"], pri["unit"]) if pri else "—",
                    _change(cur, pri) if name not in GROWTH_ONLY_CURRENT else "—",
                ],
                "page": cur.get("page"), "warn": warn,
            })
        return out

    figures, ratios = rows_for(FIGURES), rows_for(RATIOS)
    if not figures and not ratios:
        return unavailable("metrics", "No metrics were extracted for this document.")
    cols = ["Metric", "This year", "Last year", "Change"]
    tables = []
    if figures:
        tables.append({"title": "Reported figures", "columns": cols, "rows": figures})
    if ratios:
        tables.append({"title": "Ratios and growth (calculated)", "columns": cols, "rows": ratios})
    highlights = []
    for name in HIGHLIGHTS:
        cur = _verified(idx, name)
        if cur:
            highlights.append({"label": LABELS[name], "value": fmt_value(cur["value"], cur["unit"]),
                               "change": _change(cur, _verified(idx, name, "prior")) if name not in GROWTH_ONLY_CURRENT else "—"})
    note = None
    if any(r["warn"] for t in tables for r in t["rows"]):
        note = "Figures marked 'needs review' failed an automatic check and are shown for transparency only."
    return _section("metrics", tables=tables, highlights=highlights, note=note)


# ── 3. peer comparison (no LLM) ───────────────────────────────────────────────

def build_peers_section(company: str, fiscal_year: int, metrics: list[dict],
                        peers: list[dict]) -> dict[str, Any]:
    if not peers:
        return _section("peers", status="ready",
                        note="Upload another company in this session to compare.")
    entries = [{"company": company, "fiscal_year": fiscal_year, "idx": _index(metrics)}]
    entries += [{"company": p["company"], "fiscal_year": p["fiscal_year"], "idx": _index(p["metrics"])}
                for p in peers]

    def head(e: dict) -> str:
        suffix = "" if e["fiscal_year"] == fiscal_year else f" (FY{e['fiscal_year']})"
        return f"{e['company']}{suffix}"

    rows, sentences = [], []
    for name, higher_better in PEER_RATIOS.items():
        vals = [(_verified(e["idx"], name)) for e in entries]
        if not any(vals):
            continue
        rows.append({
            "cells": [LABELS[name]] + [fmt_value(v["value"], v["unit"]) if v else "—" for v in vals],
            "page": vals[0]["page"] if vals[0] else None, "warn": False,
        })
        mine = vals[0]
        have = [v for v in vals if v]
        if mine and len(have) >= 2 and mine.get("page"):
            values = [v["value"] for v in have]
            better = [x for x in values if (x > mine["value"] if higher_better else x < mine["value"])]
            rank = len(better) + 1
            tied = values.count(mine["value"]) > 1
            where = f"{'tied for ' if tied else 'ranks '}{_ORDINAL.get(rank, f'{rank}th')}"
            sentences.append({
                "text": (f"{LABELS[name]}: {company} {where} of {len(have)} "
                         f"({'higher' if higher_better else 'lower'} is better)."),
                "page": mine["page"], "quote": None,
            })
    if not rows:
        return _section("peers", note="No verified ratios are available to compare.")
    note = ("Ratios only: companies report in different currencies and units, so absolute "
            "figures are not compared. Each peer uses the fiscal year shown.")
    return _section("peers", sentences=sentences, note=note,
                    tables=[{"title": "Ratios (latest year)", "columns": ["Ratio"] + [head(e) for e in entries],
                             "rows": rows}])


# ── 4. red flags (no LLM) ─────────────────────────────────────────────────────

def build_flags_section(flags: list[dict], red_flag_status: Optional[str]) -> dict[str, Any]:
    if red_flag_status in (None, "failed", "skipped"):
        return unavailable("red_flags", "Red-flag analysis did not run for this document.")
    if not flags:
        return _section("red_flags",
                        note="None of the rule-based checks triggered. The rules cover margins, debt, "
                             "liquidity, cash conversion and the auditor's opinion; this is not a guarantee.")
    return _section("red_flags", flags=[
        {k: f[k] for k in ("title", "severity", "description", "evidence", "page")} for f in flags
    ])


# ── 1 and 5. grounded LLM sections ────────────────────────────────────────────

class DraftSentence(BaseModel):
    text: str = Field(description=(
        "One sentence. Write every financial figure as a {metric_id} placeholder taken from the "
        "AVAILABLE FIGURES list (e.g. {revenue}, {operating_margin_pct__prior}). Never type a "
        "number you did not copy from the cited page."))
    page: int = Field(description="The PDF page number (from the === PAGE n === header) this sentence is based on.")
    quote: Optional[str] = Field(
        None, description="A short phrase (4-20 words) copied verbatim from that page that supports the sentence.")


class SectionDraft(BaseModel):
    sentences: list[DraftSentence] = Field(default_factory=list)


SYSTEM_PROMPT = """You write one section of an equity research report using ONLY the pages provided.

Rules:
- Every sentence must be supported by one of the provided pages; cite its PDF page number.
- Do NOT type financial figures yourself. Use {metric_id} placeholders from AVAILABLE FIGURES;
  code replaces them with verified numbers that ALREADY include the unit (e.g. "12,345 INR crore" or
  "21.1%"), so never write a unit, currency or "crore" after a placeholder. Words like "grew" or "fell" are fine, but check the
  figures' direction before using them.
- Copy a short verbatim quote from the cited page for each sentence when you can.
- Do not use outside knowledge, do not speculate, do not give investment advice.
- If the pages do not support something, leave it out. Fewer sentences is fine.
"""

_BRIEFS = {
    "snapshot": ("Company snapshot: what the company does, its main businesses or segments, where it "
                 "operates, and the headline scale of the business. 4 to 6 sentences."),
    "outlook": ("Outlook and summary: management's stated outlook, strategy, risks and opportunities as "
                "written in the pages, then a 2-sentence summary of the financial picture using the "
                "figures. 5 to 8 sentences. Report only what the pages say; label management's views as "
                "management's views."),
}


def placeholder_values(metrics: list[dict]) -> dict[str, str]:
    """Verified metrics as ``{metric_id: '12,345 INR crore'}`` (``__prior`` for last year)."""
    out: dict[str, str] = {}
    for m in metrics:
        if m["value"] is None or m["status"] != "verified":
            continue
        key = m["name"] if m["period"] == "current" else f"{m['name']}__prior"
        out[key] = fmt_value(m["value"], m["unit"])
    return out


def _user_prompt(key: str, company: str, fiscal_year: int, pages: dict[int, str],
                 values: dict[str, str]) -> str:
    figs = "\n".join(f"- {{{k}}} = {v}" for k, v in sorted(values.items())) or "(none)"
    room = max(2000, settings.llm_max_prompt_chars - len(figs) - len(SYSTEM_PROMPT) - 400)
    shown = fit_pages(pages, _BRIEFS[key], room)          # page order = priority (MD&A first)
    body = "\n\n".join(f"=== PAGE {n} ===\n{t}" for n, t in sorted(shown.items()))
    return (f"Company: {company}\nFiscal year: {fiscal_year}\n\nTask: {_BRIEFS[key]}\n\n"
            f"AVAILABLE FIGURES (use as placeholders):\n{figs}\n\nPAGES:\n{body}")


def check_sentences(draft: SectionDraft, pages: dict[int, str], values: dict[str, str],
                    fiscal_year: int) -> tuple[list[dict], int]:
    """Keep only sentences that pass every check. Returns (kept, dropped_count)."""
    kept: list[dict] = []
    dropped = 0
    for s in draft.sentences:
        page_text = pages.get(s.page)
        if page_text is None:                                   # cited a page we did not provide
            dropped += 1
            continue
        if not numbers_supported(s.text, page_text, allowed=(fiscal_year, fiscal_year - 1)):
            dropped += 1
            continue
        if s.quote and not quote_on_page(s.quote, page_text):
            dropped += 1
            continue
        text = fill_placeholders(s.text, values)
        if text is None or not text.strip():
            dropped += 1
            continue
        kept.append({"text": text.strip(), "page": s.page, "quote": s.quote or None})
    return kept, dropped


def build_grounded_section(key: str, company: str, fiscal_year: int, pages: dict[int, str],
                           metrics: list[dict]) -> dict[str, Any]:
    """Ask the model, then verify. ``pages`` is {pdf_page: text} for the context."""
    if not pages:
        return unavailable(key, "No suitable pages were found in the document for this section.")
    values = placeholder_values(metrics)
    draft = llm.call_structured(SectionDraft, SYSTEM_PROMPT,
                                _user_prompt(key, company, fiscal_year, pages, values))
    kept, dropped = check_sentences(draft, pages, values, fiscal_year)
    logger.info("[report:%s] sentences kept=%d dropped=%d", key, len(kept), dropped)
    if not kept:
        return unavailable(key, "The model's text could not be verified against the document, so nothing is shown.")
    note = (f"{dropped} unsupported sentence(s) were removed by the automatic checks." if dropped else None)
    return _section(key, sentences=kept, note=note)
