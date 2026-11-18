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


def clean_filename(name: Optional[str]) -> str:
    """Collapse a doubled extension: "infosys_fy2025.pdf.pdf" -> "infosys_fy2025.pdf".

    Windows hides known extensions, so a file renamed to "x.pdf" while it was really
    "x.pdf" ends up as "x.pdf.pdf". Only the shown name is tidied; the stored file is untouched."""
    return re.sub(r"(\.pdf)+$", ".pdf", (name or "").strip(), flags=re.IGNORECASE) or "upload.pdf"
