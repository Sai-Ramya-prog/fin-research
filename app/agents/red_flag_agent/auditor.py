"""
Auditor's-report scan.

Looks for the headings an auditor uses when something is wrong. Clean reports
contain generic wording about going concern and key audit matters, so only
HEADING lines count (a line that is nothing but the heading). That keeps false
alarms down. Each flag quotes the heading and the sentence after it.
"""

from __future__ import annotations

import re
from typing import Dict, List

# (rule id, severity, title, heading regex)
_PATTERNS = [
    ("auditor_qualified_opinion", "High", "Auditor gave a qualified opinion",
     re.compile(r"^\W*(basis\s+for\s+)?qualified\s+opinion\W*$", re.I | re.M)),
    ("auditor_qualified_opinion", "High", "Auditor gave a qualified opinion",
     re.compile(r"except\s+for\s+the\s+(possible\s+)?effects?\s+of", re.I)),
    ("auditor_adverse_opinion", "High", "Auditor gave an adverse opinion",
     re.compile(r"^\W*(basis\s+for\s+)?adverse\s+opinion\W*$", re.I | re.M)),
    ("auditor_disclaimer", "High", "Auditor could not give an opinion",
     re.compile(r"^\W*(basis\s+for\s+)?disclaimer\s+of\s+opinion\W*$", re.I | re.M)),
    ("going_concern", "High", "Going-concern uncertainty flagged",
     re.compile(r"^\W*material\s+uncertainty\s+related\s+to\s+going\s+concern\W*$", re.I | re.M)),
    ("emphasis_of_matter", "Medium", "Auditor added an emphasis-of-matter paragraph",
     re.compile(r"^\W*emphasis\s+of\s+matter\W*$", re.I | re.M)),
]

_DESCRIPTIONS = {
    "auditor_qualified_opinion": "The auditor disagreed with, or could not verify, part of the accounts.",
    "auditor_adverse_opinion": "The auditor says the accounts do not give a true and fair view.",
    "auditor_disclaimer": "The auditor could not get enough evidence to give any opinion.",
    "going_concern": "The auditor sees real doubt that the company can keep operating.",
    "emphasis_of_matter": "The auditor drew attention to a matter in the accounts (opinion unchanged).",
}


def scan_auditor_pages(pages: List[dict]) -> List[dict]:
    """Return one flag per rule, citing the first page where it matched."""
    found: Dict[str, dict] = {}
    for page in pages:
        text = page.get("text") or ""
        for rule, severity, title, rx in _PATTERNS:
            if rule in found:
                continue
            m = rx.search(text)
            if not m:
                continue
            snippet = " ".join(text[m.start(): m.start() + 320].split())
            found[rule] = {
                "type": rule, "severity": severity, "title": title,
                "description": _DESCRIPTIONS[rule],
                "evidence": snippet, "source_page": page["pdf_page"],
            }
    return list(found.values())
