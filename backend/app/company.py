"""
app/company.py
─────────────────────────────────────────────────────────────────────────────
One rule for company names, used everywhere (upload, seed, report, chat,
vector payloads), so "Infosys", "infosys " and "INFOSYS" are the same company.

    normalize_company("  Tata   Motors ") -> "TATA MOTORS"

Only case and spacing are fixed. Different spellings ("INFOSYS LTD") stay
different, which is why the Ask and report screen offers a dropdown of the
companies that really exist in the session instead of free typing.
"""

from __future__ import annotations

import re
from typing import Optional


def normalize_company(name: Optional[str]) -> Optional[str]:
    """Trim, collapse inner whitespace, upper-case. Empty input -> None."""
    if name is None:
        return None
    cleaned = re.sub(r"\s+", " ", name).strip().upper()
    return cleaned or None
