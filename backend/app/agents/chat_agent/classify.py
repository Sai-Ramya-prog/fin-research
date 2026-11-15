"""
Question routing in plain code (no model call):

* ``mentions_other_company``  - the question names a different company from the session
* ``match_metric``            - a plain "what is / what was <metric>" lookup that the
                                metrics table can answer exactly
* ``needs_rewrite``           - a short follow-up that only makes sense with history
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

# Longest phrase wins, so "revenue growth" is never read as "revenue".
_METRIC_PHRASES: dict[str, str] = {
    "operating cash flow": "operating_cash_flow", "cash flow from operations": "operating_cash_flow",
    "cash from operations": "operating_cash_flow", "free cash flow": "free_cash_flow",
    "revenue growth": "revenue_growth_pct", "sales growth": "revenue_growth_pct",
    "net profit growth": "net_profit_growth_pct", "profit growth": "net_profit_growth_pct",
    "receivables growth": "receivables_growth_pct", "debt growth": "total_debt_growth_pct",
    "gross margin": "gross_margin_pct", "operating margin": "operating_margin_pct",
    "ebit margin": "operating_margin_pct", "net margin": "net_margin_pct",
    "net profit margin": "net_margin_pct",
    "return on equity": "roe_pct", "roe": "roe_pct",
    "debt to equity": "debt_to_equity", "debt-to-equity": "debt_to_equity",
    "debt equity ratio": "debt_to_equity", "current ratio": "current_ratio",
    "total debt": "total_debt", "total assets": "total_assets",
    "total liabilities": "total_liabilities", "total equity": "total_equity",
    "current assets": "current_assets", "current liabilities": "current_liabilities",
    "cash and cash equivalents": "cash", "cash balance": "cash",
    "trade receivables": "receivables", "receivables": "receivables",
    "capital expenditure": "capex", "capex": "capex",
    "earnings per share": "eps", "eps": "eps",
    "gross profit": "gross_profit", "operating profit": "operating_profit",
    "operating income": "operating_profit", "ebit": "operating_profit",
    "net profit": "net_profit", "net income": "net_profit", "profit after tax": "net_profit",
    "pat": "net_profit", "net sales": "revenue", "total revenue": "revenue",
    "revenue from operations": "revenue", "revenue": "revenue", "sales": "revenue",
}
_PHRASES_LONGEST_FIRST = sorted(_METRIC_PHRASES, key=len, reverse=True)

# Words that mean the user wants an explanation, not a single number.
_EXPLAIN = {"why", "explain", "reason", "reasons", "driver", "drivers", "driven", "because",
            "outlook", "risk", "risks", "segment", "segments", "compare", "compared", "versus",
            "vs", "trend", "trends", "discuss", "mention", "mentions", "management", "strategy",
            "breakdown", "region", "geography", "impact", "affect", "affected", "change", "changed"}
_PRIOR = ("last year", "previous year", "prior year", "year before", "preceding year")
_FOLLOW_UP = {"it", "its", "this", "that", "they", "their", "them", "those", "these", "he", "she",
              "same", "also", "then", "previous", "earlier", "above", "former", "latter"}


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9&'/-]+", text.lower())


def match_metric(question: str, fiscal_year: int) -> Optional[tuple[str, str]]:
    """(metric_name, period) when the question is a plain value lookup, else None."""
    q = question.lower().strip()
    if set(_words(q)) & _EXPLAIN:
        return None
    # A year other than this report's two years means "not the data I hold".
    for y in re.findall(r"\b(?:fy)?\s?(20\d{2})\b", q):
        if int(y) not in (fiscal_year, fiscal_year - 1):
            return None
    for phrase in _PHRASES_LONGEST_FIRST:
        if re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", q):
            prior = any(p in q for p in _PRIOR) or bool(re.search(rf"\b(?:fy)?\s?{fiscal_year - 1}\b", q))
            return _METRIC_PHRASES[phrase], "prior" if prior else "current"
    return None


def mentions_other_company(question: str, selected: str, others: Iterable[str]) -> Optional[str]:
    """Name of another session company the question is about, if any.

    Matches the full upper-case name, or the first word of a multi-word name
    (``Tata`` for ``TATA MOTORS``) when it is 4+ letters and not part of the
    selected company's own name.
    """
    q = question.upper()
    own_tokens = set(re.findall(r"[A-Z0-9&]+", selected.upper()))
    for other in others:
        if other.upper() == selected.upper():
            continue
        if re.search(rf"(?<![A-Z0-9&]){re.escape(other.upper())}(?![A-Z0-9&])", q):
            return other
        tokens = re.findall(r"[A-Z0-9&]+", other.upper())
        if len(tokens) > 1 and len(tokens[0]) >= 4 and tokens[0] not in own_tokens:
            if re.search(rf"(?<![A-Z0-9&]){re.escape(tokens[0])}(?![A-Z0-9&])", q):
                return other
    return None


def needs_rewrite(question: str, has_history: bool) -> bool:
    """A short or pronoun-led follow-up that cannot be searched on its own."""
    if not has_history:
        return False
    words = _words(question)
    return len(words) <= 7 or bool(set(words) & _FOLLOW_UP)


# ── small talk ────────────────────────────────────────────────────────────────
_SMALLTALK = [
    ("greeting", r"(hi+|hello+|hey+|hola|namaste|yo|good (morning|afternoon|evening)|greetings)( there| team| bot)?"),
    ("how_are_you", r"(how are you|how r u|how are you doing|how is it going|hows it going|whats up|sup)"),
    ("thanks", r"((ok(ay)? )?(thanks|thank you|thx|ty|thanks a lot|many thanks)( so much)?|great|nice|cool|ok(ay)?|got it|perfect)"),
    ("bye", r"(bye|goodbye|see you|see ya|good night|cya)"),
    ("about", r"(who are you|what are you|what can you do|what do you do|help|help me|what can i ask|how do you work|how does this work)"),
]


def smalltalk(question: str) -> Optional[str]:
    """Kind of small talk when the WHOLE message is a greeting / thanks / goodbye /
    "what can you do". Anything that also asks about the report returns None."""
    q = re.sub(r"[^a-z0-9' ]+", " ", question.lower()).strip()
    q = re.sub(r"\s+", " ", q).replace("'", "")
    if not q or len(q.split()) > 6:
        return None
    for kind, pattern in _SMALLTALK:
        if re.fullmatch(pattern, q):
            return kind
    return None
