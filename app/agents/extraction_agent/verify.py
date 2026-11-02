"""
Checks that keep the Extraction Agent honest.

1. Source check   – every number the model returns must literally appear on the
                    page it cites (commas and brackets ignored). A number that
                    is not on the page is flagged ``needs_review``.
2. Consistency    – statements must agree with themselves (assets = liabilities
                    + equity, current assets <= total assets, ...).

Both are plain code, so the result does not depend on the model being right.
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, Optional

_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")

# a row is a plain dict: name, period, value, unit, page, source_text, kind,
# formula, status, note   (see graph.py)
Row = dict


def numbers_on_page(text: str) -> set[float]:
    """Every number printed on the page, as positive floats."""
    out: set[float] = set()
    for tok in _NUM_RE.findall(text or ""):
        try:
            out.add(round(float(tok.replace(",", "")), 4))
        except ValueError:
            continue
    return out


def value_on_page(value: float, text: str) -> bool:
    return round(abs(float(value)), 4) in numbers_on_page(text)


def source_check(rows: Iterable[Row], pages_by_no: Dict[int, str]) -> None:
    """Mark rows whose number cannot be found on a statement page."""
    cache: Dict[int, set[float]] = {n: numbers_on_page(t) for n, t in pages_by_no.items()}
    for r in rows:
        if r["kind"] == "ratio" or r["value"] is None:
            continue
        target = round(abs(float(r["value"])), 4)
        page = r.get("page")
        if page in cache and target in cache[page]:
            continue
        # The model may have cited the wrong page: look on the other statement pages.
        found = [n for n, nums in cache.items() if target in nums]
        if found:
            r["page"] = found[0]
            r["note"] = (r.get("note") or "") + f" Page corrected to {found[0]}."
            continue
        r["status"] = "needs_review"
        r["note"] = (r.get("note") or "") + " Number not found on any statement page."


def _get(by: Dict[tuple, Row], name: str, period: str) -> Optional[float]:
    r = by.get((name, period))
    return None if r is None else r["value"]


def _flag(by: Dict[tuple, Row], names: list[str], period: str, msg: str) -> None:
    for n in names:
        r = by.get((n, period))
        if r is not None and r["status"] == "verified":
            r["status"] = "needs_review"
            r["note"] = (r.get("note") or "") + " " + msg


def consistency_check(rows: Iterable[Row], tolerance: float = 0.005) -> None:
    """Cross-check numbers against each other. Mismatches -> needs_review."""
    by = {(r["name"], r["period"]): r for r in rows if r["kind"] != "ratio"}
    for period in ("current", "prior"):
        ta = _get(by, "total_assets", period)
        tl = _get(by, "total_liabilities", period)
        te = _get(by, "total_equity", period)
        if ta and tl is not None and te is not None:
            if abs(ta - (tl + te)) > tolerance * abs(ta):
                _flag(by, ["total_assets", "total_liabilities", "total_equity"], period,
                      f"Assets {ta:,.0f} != liabilities + equity {tl + te:,.0f}.")
        ca = _get(by, "current_assets", period)
        if ta and ca is not None and ca > ta * (1 + tolerance):
            _flag(by, ["current_assets", "total_assets"], period, "Current assets exceed total assets.")
        cash = _get(by, "cash", period)
        if ca and cash is not None and cash > ca * (1 + tolerance):
            _flag(by, ["cash", "current_assets"], period, "Cash exceeds current assets.")
        rev = _get(by, "revenue", period)
        gp = _get(by, "gross_profit", period)
        if rev and gp is not None and gp > rev * (1 + tolerance):
            _flag(by, ["gross_profit", "revenue"], period, "Gross profit exceeds revenue.")
        npf = _get(by, "net_profit", period)
        if rev and npf is not None and npf > rev * (1 + tolerance):
            _flag(by, ["net_profit", "revenue"], period, "Net profit exceeds revenue.")
