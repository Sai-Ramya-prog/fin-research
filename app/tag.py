"""
app/tag.py
─────────────────────────────────────────────────────────────────────────────
Page tagging – Document Agent, Node 2.

For every non-image-only page this module produces:
  section       – balance_sheet | profit_loss | cash_flow | equity_changes |
                  auditor_report | notes | mdna_narrative | other
  scope         – consolidated | standalone | unknown
  unit          – raw string captured from header (e.g. "₹ in crore")
  content_type  – table | narrative
  printed_page  – best-effort page number from header/footer, or None

Strategy
────────
  1. Strip boilerplate header lines (page numbers, company name, report title).
  2. Rule-based heading matching on first 12 cleaned lines.
  3. If no heading match, try content-body fallback patterns (handles pages
     where the section heading appeared on a preceding divider page, e.g.
     Infosys p274, Reliance consolidated statements).
  4. Numeric-density check (≥25 numeric tokens → table content_type).
  5. (contd.) pages inherit the previous page's section, scope, unit.
  6. Scope detection on the full heading zone (handles "CONSOLIDATED" banners).

Public API
──────────
  tag_pages(pages, pdf_name="") → list[TagResult]   (each result has tier 1 or 2)
  build_page_map(tag_results, company, output_path) → None
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.parse import PageResult

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

SECTION_OTHER          = "other"
SECTION_BALANCE_SHEET  = "balance_sheet"
SECTION_PROFIT_LOSS    = "profit_loss"
SECTION_CASH_FLOW      = "cash_flow"
SECTION_EQUITY_CHANGES = "equity_changes"
SECTION_AUDITOR_REPORT = "auditor_report"
SECTION_NOTES          = "notes"
SECTION_MDNA           = "mdna_narrative"

VALID_SECTIONS = {
    SECTION_BALANCE_SHEET, SECTION_PROFIT_LOSS, SECTION_CASH_FLOW,
    SECTION_EQUITY_CHANGES, SECTION_AUDITOR_REPORT, SECTION_NOTES,
    SECTION_MDNA, SECTION_OTHER,
}

STATEMENT_SECTIONS = {
    SECTION_BALANCE_SHEET, SECTION_PROFIT_LOSS,
    SECTION_CASH_FLOW, SECTION_EQUITY_CHANGES,
}

# Tier 1 = pages every agent needs, embedded immediately after upload:
# the four statements, the auditor's report, MD&A and the notes.
# Tier 2 = everything else (cover, business overview, ESG ...): stored as text
# and embedded later, only when a question needs it.
TIER1_SECTIONS = STATEMENT_SECTIONS | {
    SECTION_AUDITOR_REPORT, SECTION_NOTES, SECTION_MDNA,
}

# Lines from the top to scan for the heading (after boilerplate stripping)
_HEADING_LINES = 12

# Minimum numeric tokens to classify a page as a "table" type
_MIN_NUMERIC_TOKENS = 25

# Pattern: digit followed by digit/comma run of ≥3 more → numeric token
_NUMERIC_TOKEN_RE = re.compile(r"\d[\d,]{3,}")

# ── Boilerplate line filter ───────────────────────────────────────────────────
# Lines matching this are skipped before heading detection so that annual
# report banners, company names and standalone page numbers don't mislead
# the heading matcher.
_BOILERPLATE_RE = re.compile(
    r"^\s*("
    r"\d{1,4}"                                   # isolated page number
    r"|\d{4}-\d{2}"                              # year range e.g. 2024-25
    r"|integrated\s+annual\s+report.*"           # "Infosys Integrated Annual Report 2024-25"
    r"|annual\s+report.*"
    r"|.{5,60}\s+limited"                        # company name ending in "Limited"
    r"|.{5,60}\s+ltd\.?"
    r"|(standalone|consolidated)\s+financials"   # sidebar nav tabs
    r"|financials"
    r"|statutory"
    r")\s*$",
    re.I,
)

# ── Heading patterns (explicit section titles) ────────────────────────────────
_HEADING_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(
        r"balance\s+sheet|statement\s+of\s+(financial\s+position|assets\s+and\s+liabilit)",
        re.I,
    ), SECTION_BALANCE_SHEET),
    (re.compile(
        r"(statements?\s+of\s+)?(profit\s+(and|&)\s+loss|income\s+(and\s+expenditure|statement)|"
        r"statements?\s+of\s+(operations|comprehensive\s+income))",
        re.I,
    ), SECTION_PROFIT_LOSS),
    (re.compile(r"(statement\s+of\s+)?cash\s+flow", re.I), SECTION_CASH_FLOW),
    (re.compile(
        r"(statement\s+of\s+)?(changes?\s+in\s+equity|equity\s+changes?)", re.I,
    ), SECTION_EQUITY_CHANGES),
    (re.compile(
        r"independent\s+auditor.{0,5}s?\s+report|auditor.{0,5}s?\s+report", re.I,
    ), SECTION_AUDITOR_REPORT),
    (re.compile(
        r"notes?\s+(to|forming\s+part\s+of)\s+(the\s+)?(financial\s+statements?|accounts?)",
        re.I,
    ), SECTION_NOTES),
    (re.compile(
        r"management.{0,10}(discussion|report|review)|director.{0,5}s?\s+report", re.I,
    ), SECTION_MDNA),
]

# ── Content-body fallback patterns ────────────────────────────────────────────
# Used only when no heading title is found (e.g. Infosys p274 whose title sat on
# the divider page). To avoid tagging notes / MD&A pages as statements, a page
# qualifies ONLY if it contains ALL the markers of that statement.
_BODY_FALLBACK_ALL: list[tuple[list[re.Pattern], str]] = [
    ([re.compile(r"total\s+assets", re.I),
      re.compile(r"equity\s+and\s+liabilit", re.I)], SECTION_BALANCE_SHEET),
    ([re.compile(r"revenue\s+from\s+operations", re.I),
      re.compile(r"profit\s+before\s+tax", re.I),
      re.compile(r"earnings?\s+per\s+(equity\s+)?share", re.I)], SECTION_PROFIT_LOSS),
    ([re.compile(r"operating\s+activit", re.I),
      re.compile(r"investing\s+activit", re.I),
      re.compile(r"financing\s+activit", re.I)], SECTION_CASH_FLOW),
]

# Body fallback needs at least this many numeric tokens (statements are dense)
_BODY_FALLBACK_MIN_NUMERIC = 20

# ── Scope patterns ────────────────────────────────────────────────────────────
_SCOPE_CONSOLIDATED_RE = re.compile(r"consolidated", re.I)
_SCOPE_STANDALONE_RE   = re.compile(r"\bstandalone\b|\bseparate\b|\bunconsolidated\b", re.I)

# ── Unit patterns ─────────────────────────────────────────────────────────────
_UNIT_RE = re.compile(
    r"(?P<cur>₹|rs\.?|inr|usd?|us\$|\bc\b|`)\s*(?:in\s+)?"
    r"(?P<scale>crores?|lakhs?|millions?|billions?|thousands?)",
    re.I,
)

# ── Printed page number ───────────────────────────────────────────────────────
_PRINTED_PAGE_RE = re.compile(r"(?:^|\|\s*)(\d{1,4})\s*(?:\||$)", re.M)

# ── Continuation marker ───────────────────────────────────────────────────────
_CONTD_RE = re.compile(r"\(\s*(contd\.?|continued)\s*\)", re.I)


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class TagResult:
    """Tagging output for a single PDF page."""
    pdf_page: int
    section: str = SECTION_OTHER
    scope: str = "unknown"
    unit: str = ""
    content_type: str = "narrative"
    printed_page: Optional[int] = None
    is_image_only: bool = False
    tier: int = 2   # 1 = embed immediately, 2 = embed on demand


# ── Helpers ───────────────────────────────────────────────────────────────────

def _cleaned_heading_lines(text: str) -> list[str]:
    """
    Return the first _HEADING_LINES non-empty lines, with boilerplate stripped.
    This makes heading detection work even when annual report banners, company
    names or isolated page numbers dominate the top of the page.
    """
    raw = [l.strip() for l in text.splitlines() if l.strip()]
    cleaned = [l for l in raw if not _BOILERPLATE_RE.match(l)]
    return cleaned[:_HEADING_LINES]


_TITLE_MAX_CHARS = 100
_FOOTER_LINES = 6


_TITLE_PREFIX_RE = re.compile(r"^(the\s+)?((consolidated|standalone)\s+)?(financial\s+)?", re.I)


def _strip_title_prefix(line: str) -> str:
    """"Consolidated Balance Sheet" -> "Balance Sheet" so the title must START the line."""
    return _TITLE_PREFIX_RE.sub("", line.strip(), count=1)


def _title_lines(text: str) -> list[str]:
    """Candidate title lines: first cleaned lines plus the LAST few lines
    (Infosys prints "Consolidated Balance Sheet" at the bottom of the page)."""
    raw = [l.strip() for l in text.splitlines() if l.strip()]
    cleaned = [l for l in raw if not _BOILERPLATE_RE.match(l)]
    head = cleaned[:_HEADING_LINES]
    foot = cleaned[-_FOOTER_LINES:]
    seen, out = set(), []
    for l in head + foot:
        if l not in seen:
            seen.add(l)
            out.append(l)
    return out


def _heading_text(text: str) -> str:
    """Joined heading zone (cleaned lines + raw first lines for scope)."""
    raw_lines = [l.strip() for l in text.splitlines() if l.strip()]
    cleaned = _cleaned_heading_lines(text)
    # Include raw first 6 lines so scope keywords like "CONSOLIDATED" aren't lost
    combined = raw_lines[:6] + cleaned
    return " ".join(combined)


def _numeric_density(text: str) -> int:
    return len(_NUMERIC_TOKEN_RE.findall(text))


def _detect_section(heading: str, full_text: str) -> Optional[str]:
    """
    A section is recognised only from a SHORT title line (≤100 chars) near the
    top or bottom of the page - not from a phrase buried in a sentence, which is
    what caused notes and narrative pages to be tagged as statements.
    If no title is found, a strict body fallback (all markers of a statement +
    dense numbers) handles pages whose title sits on a divider page.
    """
    for line in _title_lines(full_text):
        if len(line) > _TITLE_MAX_CHARS:
            continue
        is_notes_line = bool(re.match(r"\s*notes?\b", line, re.I))
        for pattern, section in _HEADING_PATTERNS:
            if section == SECTION_NOTES:
                if pattern.search(line):
                    return section
                continue
            if is_notes_line:
                continue  # "Notes to the balance sheet" is a note, not the statement
            if pattern.match(_strip_title_prefix(line)):
                # A real statement page is dense with numbers. A titled page
                # without them (MD&A discussion, a note mentioning "cash flow")
                # is not the statement itself.
                if section in STATEMENT_SECTIONS and _numeric_density(full_text) < _BODY_FALLBACK_MIN_NUMERIC:
                    continue
                return section
    if _numeric_density(full_text) >= _BODY_FALLBACK_MIN_NUMERIC:
        for patterns, section in _BODY_FALLBACK_ALL:
            if all(p.search(full_text) for p in patterns):
                return section
    return None


def _detect_scope(heading: str) -> str:
    if _SCOPE_CONSOLIDATED_RE.search(heading):
        return "consolidated"
    if _SCOPE_STANDALONE_RE.search(heading):
        return "standalone"
    return "unknown"


def _detect_unit(text: str) -> str:
    """Return a normalised unit such as "₹ in crore" or "US$ in million"."""
    m = _UNIT_RE.search(text[:1200])
    if not m:
        return ""
    scale = m.group("scale").lower().rstrip("s")
    cur = m.group("cur").lower()
    symbol = "US$" if cur in ("us$", "usd", "us") else "₹"
    return f"{symbol} in {scale}"


def _detect_printed_page(text: str) -> Optional[int]:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    candidates = lines[:3] + lines[-3:]
    candidate_text = "\n".join(candidates)
    for m in _PRINTED_PAGE_RE.finditer(candidate_text):
        num = int(m.group(1))
        if 1 <= num <= 2000:
            return num
    return None


def _resolve_printed_pages(pdf_pages: list[int], candidates: list[Optional[int]]) -> list[Optional[int]]:
    """
    Turn noisy per-page candidates into consistent printed page numbers.

    Printed page = pdf_page + offset, and the offset is constant over long runs
    of pages. For each page, vote on the offset among neighbours (±4 pages). If
    at least 2 neighbours agree, use pdf_page + winning offset (replacing stray
    numbers picked up from tables/headers); otherwise return None.
    """
    from collections import Counter

    n = len(pdf_pages)
    out: list[Optional[int]] = []
    for i in range(n):
        votes: Counter[int] = Counter()
        for k in range(max(0, i - 4), min(n, i + 5)):
            c = candidates[k]
            if c is not None:
                votes[c - pdf_pages[k]] += 1
        if votes:
            offset, count = votes.most_common(1)[0]
            if count >= 2 and 1 <= pdf_pages[i] + offset <= 2000:
                out.append(pdf_pages[i] + offset)
                continue
        out.append(None)
    return out


# ── Core tagger ───────────────────────────────────────────────────────────────

def tag_pages(
    pages: list[PageResult],
    pdf_name: str = "",
) -> list[TagResult]:
    """
    Tag every page in *pages* and return a :class:`TagResult` per page.

    Rules applied in order:
      1. Image-only pages → section=other, skip.
      2. Strip boilerplate; build heading zone (12 cleaned lines).
      3. Match explicit heading patterns.
      4. If no match → strict body fallback (all statement markers + dense numbers).
      5. (contd.) pages inherit previous section/scope/unit.
      6. Numeric density ≥ _MIN_NUMERIC_TOKENS → content_type = table.
      7. Statement-section pages are always content_type = table.
      8. Printed page: per-page candidate, then offset-voting over neighbours.
    """
    results: list[TagResult] = []
    printed_candidates: list[Optional[int]] = []

    prev_section: str = SECTION_OTHER
    prev_scope: str = "unknown"
    prev_unit: str = ""

    for pr in pages:
        tag = TagResult(pdf_page=pr.pdf_page)

        if pr.is_image_only:
            tag.is_image_only = True
            printed_candidates.append(None)
            results.append(tag)
            continue

        text = pr.text
        heading = _heading_text(text)
        title_zone = heading + " " + " ".join(_title_lines(text))
        is_contd = any(_CONTD_RE.search(l) for l in _title_lines(text) if len(l) <= _TITLE_MAX_CHARS)

        # "(contd.)" inherits ONLY from the immediately preceding statement page and
        # only if this page is numeric - "Accounting policies (contd.)" is not a statement.
        prev_is_statement = bool(results) and results[-1].section in STATEMENT_SECTIONS
        if is_contd and prev_is_statement and _numeric_density(text) >= _BODY_FALLBACK_MIN_NUMERIC:
            tag.section = prev_section
            tag.scope   = prev_scope
            tag.unit    = prev_unit
        else:
            detected = _detect_section(heading, text)
            tag.section = detected if detected else SECTION_OTHER
            tag.scope   = _detect_scope(title_zone)
            tag.unit    = _detect_unit(text)

            # Propagate scope if not detected locally
            if tag.scope == "unknown" and prev_scope != "unknown":
                tag.scope = prev_scope
            if not tag.unit and prev_unit:
                tag.unit = prev_unit

        # Content type
        numeric_count = _numeric_density(text)
        if tag.section in STATEMENT_SECTIONS or numeric_count >= _MIN_NUMERIC_TOKENS:
            tag.content_type = "table"
        else:
            tag.content_type = "narrative"

        printed_candidates.append(_detect_printed_page(text))

        # Carry forward
        if tag.section != SECTION_OTHER:
            prev_section = tag.section
        if tag.scope != "unknown":
            prev_scope = tag.scope
        if tag.unit:
            prev_unit = tag.unit

        results.append(tag)

        logger.debug(
            "%s p%d → section=%s scope=%s unit=%r ct=%s numeric=%d printed=%s",
            pdf_name, pr.pdf_page, tag.section, tag.scope, tag.unit,
            tag.content_type, numeric_count, tag.printed_page,
        )

    resolved = _resolve_printed_pages([r.pdf_page for r in results], printed_candidates)
    for r, pp in zip(results, resolved):
        r.printed_page = pp
        r.tier = 1 if (r.section in TIER1_SECTIONS and not r.is_image_only) else 2

    logger.info(
        "tag_pages: %s → %d pages, %d statement pages",
        pdf_name or "PDF", len(results),
        sum(1 for t in results if t.section in STATEMENT_SECTIONS),
    )
    return results


# ── Page map writer ───────────────────────────────────────────────────────────

def build_page_map(
    tag_results: list[TagResult],
    company: str,
    output_path: str | Path,
) -> None:
    """
    Write a human-readable Markdown table of all non-'other' tagged pages.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    interesting = [t for t in tag_results if t.section != SECTION_OTHER]

    lines = [
        f"# Page Map – {company}",
        "",
        f"Total pages tagged: {len(tag_results)}  ",
        f"Statement / named pages: {len(interesting)}",
        "",
        "| pdf_page | printed_page | section | scope | unit | content_type |",
        "|----------|-------------|---------|-------|------|--------------|",
    ]
    for t in interesting:
        pp = str(t.printed_page) if t.printed_page is not None else "—"
        lines.append(
            f"| {t.pdf_page} | {pp} | {t.section} | {t.scope} | "
            f"{t.unit or '—'} | {t.content_type} |"
        )

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("Page map written → %s (%d rows)", output_path, len(interesting))
