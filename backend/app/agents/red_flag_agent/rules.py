"""
Red-flag rules. Plain code over the metrics the Extraction Agent saved.

A rule only fires when every number it uses is ``verified`` - a flag built on
a doubtful number would be worse than no flag.

Each flag: type (rule id), severity (High / Medium / Low), title, description,
evidence (the numbers), source_page.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

Metric = dict   # name, period ('current'|'prior'), value, unit, page, status
Flag = dict

# ── thresholds (change here, nowhere else) ────────────────────────────────────
MARGIN_DROP_MEDIUM_PP = 2.0      # percentage points, year on year
MARGIN_DROP_HIGH_PP = 5.0
DEBT_GROWTH_MEDIUM = 20.0        # percent
DEBT_GROWTH_HIGH = 50.0
DE_MEDIUM = 1.0                  # debt / equity
DE_HIGH = 2.0
CURRENT_RATIO_LOW = 1.0
CURRENT_RATIO_MEDIUM = 0.8
CURRENT_RATIO_HIGH = 0.5
OCF_PROFIT_MEDIUM = 0.5          # operating cash flow / net profit
RECEIVABLES_GAP_PP = 15.0        # receivables growth minus revenue growth
REVENUE_DECLINE_HIGH = -10.0     # percent


class _Book:
    """Small lookup helper: get(name, period) -> metric if verified, else None."""

    def __init__(self, rows: List[Metric]):
        self.by: Dict[tuple, Metric] = {(r["name"], r["period"]): r for r in rows if r["value"] is not None}

    def get(self, name: str, period: str = "current") -> Optional[Metric]:
        r = self.by.get((name, period))
        return r if r is not None and r["status"] == "verified" else None


def _flag(rule, severity, title, description, evidence, page=None) -> Flag:
    return {"type": rule, "severity": severity, "title": title, "description": description,
            "evidence": evidence, "source_page": page}


def _fmt(m: Metric) -> str:
    return f"{m['value']:,.2f}".rstrip("0").rstrip(".") + (f" {m['unit']}" if m.get("unit") else "")


def run_rules(rows: List[Metric]) -> List[Flag]:
    b = _Book(rows)
    flags: List[Flag] = []

    # 1. Net loss
    npf = b.get("net_profit")
    if npf and npf["value"] < 0:
        flags.append(_flag("net_loss", "High", "Net loss for the year",
                           "The company reported a net loss this year.",
                           f"Net profit = {_fmt(npf)}", npf["page"]))

    # 2. Falling revenue
    g = b.get("revenue_growth_pct")
    if g and g["value"] < 0:
        rev, rev_p = b.get("revenue"), b.get("revenue", "prior")
        sev = "High" if g["value"] <= REVENUE_DECLINE_HIGH else "Medium"
        ev = f"Revenue growth = {g['value']:.1f}%"
        if rev and rev_p:
            ev += f" ({_fmt(rev_p)} -> {_fmt(rev)})"
        flags.append(_flag("revenue_decline", sev, "Revenue fell year on year",
                           "Sales are lower than last year.", ev, g["page"]))

    # 3. Falling margins
    for name, label in (("operating_margin_pct", "Operating margin"), ("net_margin_pct", "Net margin")):
        cur, pri = b.get(name), b.get(name, "prior")
        if cur and pri:
            drop = pri["value"] - cur["value"]
            if drop >= MARGIN_DROP_MEDIUM_PP:
                sev = "High" if drop >= MARGIN_DROP_HIGH_PP else "Medium"
                flags.append(_flag("falling_margin", sev, f"{label} fell by {drop:.1f} points",
                                   f"{label} is lower than last year, so each unit of sales earns less profit.",
                                   f"{label}: {pri['value']:.1f}% -> {cur['value']:.1f}%", cur["page"]))

    # 4. Debt: growing fast, or high compared with equity
    dg = b.get("total_debt_growth_pct")
    if dg and dg["value"] >= DEBT_GROWTH_MEDIUM:
        d, d_p = b.get("total_debt"), b.get("total_debt", "prior")
        sev = "High" if dg["value"] >= DEBT_GROWTH_HIGH else "Medium"
        ev = f"Total debt grew {dg['value']:.1f}%"
        if d and d_p:
            ev += f" ({_fmt(d_p)} -> {_fmt(d)})"
        flags.append(_flag("rising_debt", sev, "Debt is rising quickly",
                           "Borrowings grew much faster than is usual year to year.", ev, dg["page"]))
    de = b.get("debt_to_equity")
    if de and de["value"] >= DE_MEDIUM:
        sev = "High" if de["value"] >= DE_HIGH else "Medium"
        flags.append(_flag("high_leverage", sev, f"Debt is {de['value']:.1f}x equity",
                           "Borrowings are large compared with shareholders' money.",
                           f"Debt to equity = {de['value']:.2f}x", de["page"]))

    # 5. Liquidity
    cr = b.get("current_ratio")
    if cr and cr["value"] < CURRENT_RATIO_LOW:
        sev = "High" if cr["value"] < CURRENT_RATIO_HIGH else "Medium" if cr["value"] < CURRENT_RATIO_MEDIUM else "Low"
        flags.append(_flag("weak_liquidity", sev, f"Current ratio is {cr['value']:.2f}",
                           "Short-term liabilities are larger than short-term assets.",
                           f"Current ratio = {cr['value']:.2f}x (below 1.0)", cr["page"]))

    # 6. Profit not backed by cash
    oc = b.get("ocf_to_net_profit")
    ocf = b.get("operating_cash_flow")
    if oc and oc["value"] < OCF_PROFIT_MEDIUM:
        sev = "High" if oc["value"] < 0 else "Medium"
        ev = f"Operating cash flow / net profit = {oc['value']:.2f}x"
        if ocf:
            ev += f" (operating cash flow {_fmt(ocf)})"
        flags.append(_flag("cash_profit_gap", sev, "Profit is not turning into cash",
                           "Reported profit is much higher than the cash the business generated.",
                           ev, oc["page"]))

    # 7. Receivables growing much faster than sales
    rg, sg = b.get("receivables_growth_pct"), b.get("revenue_growth_pct")
    if rg and sg and rg["value"] - sg["value"] >= RECEIVABLES_GAP_PP:
        flags.append(_flag("receivables_outpacing_sales", "Medium", "Receivables growing faster than sales",
                           "Customers may be paying more slowly, or sales may be booked early.",
                           f"Receivables +{rg['value']:.1f}% vs revenue {sg['value']:+.1f}%", rg["page"]))
    return flags
