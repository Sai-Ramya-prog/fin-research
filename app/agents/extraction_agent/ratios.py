"""
Ratios and growth rates, calculated in code from the extracted numbers.

Every ratio row carries its formula and is marked ``needs_review`` when any
input is. The LLM never produces a ratio.
"""

from __future__ import annotations

from typing import Dict, List, Optional

Row = dict


def _row(name, period, value, unit, formula, inputs: List[Row], kind="ratio") -> Row:
    bad = [i["name"] for i in inputs if i["status"] != "verified"]
    pages = sorted({i["page"] for i in inputs if i.get("page")})
    return {
        "name": name,
        "period": period,
        "value": round(value, 4),
        "unit": unit,
        "page": pages[0] if pages else None,
        "source_text": None,
        "kind": kind,
        "formula": formula,
        "status": "needs_review" if bad else "verified",
        "note": f"Inputs need review: {', '.join(bad)}." if bad else None,
    }


def compute_ratios(rows: List[Row]) -> List[Row]:
    by: Dict[tuple, Row] = {(r["name"], r["period"]): r for r in rows if r["kind"] != "ratio"}
    out: List[Row] = []

    def g(name: str, period: str) -> Optional[Row]:
        r = by.get((name, period))
        return r if r is not None and r["value"] is not None else None

    for period in ("current", "prior"):
        rev = g("revenue", period)
        for src, name in (("gross_profit", "gross_margin_pct"),
                          ("operating_profit", "operating_margin_pct"),
                          ("net_profit", "net_margin_pct")):
            num = g(src, period)
            if num and rev and rev["value"]:
                out.append(_row(name, period, num["value"] / rev["value"] * 100, "%",
                                f"{src} / revenue x 100", [num, rev]))

        debt, eq = g("total_debt", period), g("total_equity", period)
        if debt and eq and eq["value"]:
            out.append(_row("debt_to_equity", period, debt["value"] / eq["value"], "x",
                            "total_debt / total_equity", [debt, eq]))

        ca, cl = g("current_assets", period), g("current_liabilities", period)
        if ca and cl and cl["value"]:
            out.append(_row("current_ratio", period, ca["value"] / cl["value"], "x",
                            "current_assets / current_liabilities", [ca, cl]))

        npf = g("net_profit", period)
        if npf and eq and eq["value"]:
            out.append(_row("roe_pct", period, npf["value"] / eq["value"] * 100, "%",
                            "net_profit / total_equity x 100 (closing equity)", [npf, eq]))

        ocf, capex = g("operating_cash_flow", period), g("capex", period)
        if ocf and npf and npf["value"] and npf["value"] > 0:
            out.append(_row("ocf_to_net_profit", period, ocf["value"] / npf["value"], "x",
                            "operating_cash_flow / net_profit", [ocf, npf]))
        if ocf and capex:
            unit = ocf["unit"]
            out.append(_row("free_cash_flow", period, ocf["value"] - abs(capex["value"]), unit,
                            "operating_cash_flow - |capex|", [ocf, capex], kind="derived"))

    # Growth: latest year versus previous year (reported in the "current" slot).
    for name, label in (("revenue", "revenue_growth_pct"),
                        ("net_profit", "net_profit_growth_pct"),
                        ("receivables", "receivables_growth_pct"),
                        ("total_debt", "total_debt_growth_pct")):
        cur, pri = g(name, "current"), g(name, "prior")
        if cur and pri and pri["value"] and pri["value"] > 0:
            out.append(_row(label, "current", (cur["value"] / pri["value"] - 1) * 100, "%",
                            f"({name} this year / {name} last year - 1) x 100", [cur, pri]))
    return out
