"""
app/grounding.py
─────────────────────────────────────────────────────────────────────────────
Plain-code checks that keep every LLM sentence tied to the document.

Used by the report writer and the chat agent:

* ``quote_on_page``        – a quoted phrase must literally appear on its page.
* ``fill_placeholders``    – the model writes ``{operating_margin_pct}``; code
                             puts the verified number in. The model never types
                             a financial figure itself.
* ``numbers_supported``    – any digit the model DID type must be printed on
                             the cited page (or be the fiscal year).

None of this calls a model, so a wrong answer can be caught, not argued with.
"""

from __future__ import annotations

import re
from typing import Iterable, Mapping, Optional

from app.agents.extraction_agent.verify import numbers_on_page

_PLACEHOLDER_RE = re.compile(r"\{([a-z0-9_]+)\}")
_DASHES = str.maketrans({"–": "-", "—": "-", "−": "-", "‘": "'", "’": "'",
                         "“": '"', "”": '"', " ": " "})


def norm(text: str) -> str:
    """Lower-case, one-space-separated, curly quotes/dashes flattened, hyphenated
    line breaks joined: makes quote matching robust to PDF line wrapping."""
    t = (text or "").translate(_DASHES)
    t = re.sub(r"-\s*\n\s*", "", t)          # "mar-\nket" -> "market"
    t = re.sub(r"\s+", " ", t)
    return t.strip().lower()


def quote_on_page(quote: Optional[str], page_text: str) -> bool:
    """True when ``quote`` (at least a few words) appears verbatim on the page."""
    q = norm(quote or "")
    if len(q) < 8:
        return False
    return q in norm(page_text)


def fmt_value(value: float, unit: Optional[str]) -> str:
    """12345.6 + 'INR crore' -> '12,345.6 INR crore'; 12.3 + '%' -> '12.3%'."""
    number = f"{value:,.2f}".rstrip("0").rstrip(".")
    if not unit:
        return number
    if unit in ("%",):
        return f"{number}%"
    if unit == "x":
        return f"{number}x"
    return f"{number} {unit}"


def placeholders_in(template: str) -> list[str]:
    return _PLACEHOLDER_RE.findall(template or "")


def fill_placeholders(template: str, values: Mapping[str, str]) -> Optional[str]:
    """Replace ``{name}`` with its formatted value. Returns None when the
    template uses a placeholder that has no verified value (sentence is dropped)."""
    for name in placeholders_in(template):
        if name not in values:
            return None
    return _PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], template)


def numbers_supported(
    template: str,
    page_text: str,
    allowed: Iterable[float] = (),
) -> bool:
    """Every number typed by the model (outside placeholders) must be printed on
    the cited page, or be in ``allowed`` (e.g. the fiscal year)."""
    typed = numbers_on_page(_PLACEHOLDER_RE.sub(" ", template or ""))
    if not typed:
        return True
    on_page = numbers_on_page(page_text)
    ok = on_page | {round(float(a), 4) for a in allowed}
    return all(n in ok for n in typed)
