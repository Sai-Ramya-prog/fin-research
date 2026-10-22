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
  tag_pages(pages, pdf_name="") → list[TagResult]
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
        r"(statement\s+of\s+)?(profit\s+(and|&)\s+loss|income\s+(and\s+expenditure|statement)|"
        r"statement\s+of\s+(operations|comprehensive\s+income))",
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
# Used when no explicit heading title is found (e.g. Infosys p274 which has
# no "Balance Sheet" title – it was on the preceding divider page).
# Matched against the full page text (not just heading lines).
_BODY_FALLBACK_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(
        r"non.?current\s+assets|equity\s+and\s+liabilit|total\s+assets"
        r"|property,?\s+plant",
        re.I,
    ), SECTION_BALANCE_SHEET),
    (re.compile(
        r"revenue\s+from\s+operations|other\s+income|total\s+income"
        r"|profit\s+before\s+tax|earnings?\s+per\s+(equity\s+)?share",
        re.I,
    ), SECTION_PROFIT_LOSS),
    (re.compile(
        r"cash\s+(flow|used\s+in|generated\s+from|from)\s+(operating|investing|financing)"
        r"|(operating|investing|financing)\s+activit",
        re.I,
    ), SECTION_CASH_FLOW),
    (re.compile(
        r"changes?\s+in\s+(other\s+)?equity|other\s+comprehensive\s+income.*equity",
        re.I,
    ), SECTION_EQUITY_CHANGES),
]

# ── Scope patterns ────────────────────────────────────────────────────────────
_SCOPE_CONSOLIDATED_RE = re.compile(r"consolidated", re.I)
_SCOPE_STANDALONE_RE   = re.compile(r"\bstandalone\b|\bseparate\b|\bunconsolidated\b", re.I)

# ── Unit patterns ─────────────────────────────────────────────────────────────
_UNIT_RE = re.compile(
    r"(?:in\s+)?(?:₹|rs\.?|inr|usd?|us\$|\bc\b)\s*(?:in\s+)?"
    r"(?:crore|crores?|lakh|million|billion|thousand)",
    re.I,
)

# ── Printed page number ───────────────────────────────────────────────────────
_PRINTED_PAGE_RE = re.compile(r"(?:^|\|\s*)(\d{1,4})\s*(?:\||$)", re.M)

# ── Continuation marker ───────────────────────────────────────────────────────
_CONTD_RE = re.compile(r"\(\s*contd\.?\s*\)|\bcontinued\b", re.I)


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
    Try heading patterns first; fall back to body patterns if none match.
    Body fallback only activates when the page has substantial numeric content
    (≥10 numeric tokens) to avoid false positives in narrative pages.
    """
    for pattern, section in _HEADING_PATTERNS:
        if pattern.search(heading):
            return section
    # Body fallback – only for pages with numbers (financial data pages)
    if _numeric_density(full_text) >= 10:
        for pattern, section in _BODY_FALLBACK_PATTERNS:
            if pattern.search(full_text):
                return section
    return None


def _detect_scope(heading: str) -> str:
    if _SCOPE_CONSOLIDATED_RE.search(heading):
        return "consolidated"
    if _SCOPE_STANDALONE_RE.search(heading):
        return "standalone"
    return "unknown"


def _detect_unit(text: str) -> str:
    m = _UNIT_RE.search(text[:1200])
    return m.group(0).strip() if m else ""


def _detect_printed_page(text: str) -> Optional[int]:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    candidates = lines[:3] + lines[-3:]
    candidate_text = "\n".join(candidates)
    for m in _PRINTED_PAGE_RE.finditer(candidate_text):
        num = int(m.group(1))
        if 1 <= num <= 2000:
            return num
    return None


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
      4. If no match and page has numeric data → try body fallback patterns.
      5. (contd.) pages inherit previous section/scope/unit.
      6. Numeric density ≥ _MIN_NUMERIC_TOKENS → content_type = table.
      7. Statement-section pages are always content_type = table.
      8. Printed page detected from header/footer.
    """
    results: list[TagResult] = []

    prev_section: str = SECTION_OTHER
    prev_scope: str = "unknown"
    prev_unit: str = ""

    for pr in pages:
        tag = TagResult(pdf_page=pr.pdf_page)

        if pr.is_image_only:
            tag.is_image_only = True
            results.append(tag)
            continue

        text = pr.text
        heading = _heading_text(text)
        is_contd = bool(_CONTD_RE.search(heading))

        if is_contd and prev_section != SECTION_OTHER:
            tag.section = prev_section
            tag.scope   = prev_scope
            tag.unit    = prev_unit
        else:
            detected = _detect_section(heading, text)
            tag.section = detected if detected else SECTION_OTHER
            tag.scope   = _detect_scope(heading)
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

        tag.printed_page = _detect_printed_page(text)

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
