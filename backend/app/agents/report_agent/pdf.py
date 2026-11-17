"""
PDF export for a report (ReportLab, pure Python: no system libraries, works on Windows).

Layout, top to bottom:
  banner          company, fiscal year, date
  at a glance     up to six headline numbers with year-on-year change
  sections        snapshot, key metrics, peer comparison, red flags, outlook
  footer          page number + "not investment advice"

``build_pdf(report) -> bytes`` takes the dict from ``graph.get_report_for_pdf``.
"""

from __future__ import annotations

import io
from datetime import datetime
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

_INK = colors.HexColor("#1b2733")
_MUTED = colors.HexColor("#5f6b7a")
_RULE = colors.HexColor("#d5dbe2")
_BRAND = colors.HexColor("#1d7a57")
_BRAND_SOFT = colors.HexColor("#e7f3ed")
_ZEBRA = colors.HexColor("#f6f8fa")
_UP, _DOWN = "#1d7a57", "#b42318"
_SEVERITY = {"High": "#b42318", "Medium": "#b54708", "Low": "#2f5d9f"}
_MARGIN = 18 * mm
_WIDTH = A4[0] - 2 * _MARGIN


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()["Normal"]

    def mk(name: str, **kw: Any) -> ParagraphStyle:
        kw.setdefault("textColor", _INK)
        return ParagraphStyle(name, parent=base, **kw)

    return {
        "banner_t": mk("bt", fontName="Helvetica-Bold", fontSize=21, leading=25, textColor=colors.white),
        "banner_s": mk("bs", fontSize=9.5, leading=13, textColor=colors.HexColor("#d7efe4")),
        "h": mk("h", fontName="Helvetica-Bold", fontSize=13, leading=16, textColor=_BRAND, spaceBefore=16, spaceAfter=3),
        "body": mk("b", fontSize=9.6, leading=14, spaceAfter=5),
        "src": mk("src", fontSize=8, leading=11, textColor=_MUTED, fontName="Helvetica-Oblique", spaceAfter=5, leftIndent=8),
        "note": mk("n", fontSize=8.3, leading=11.5, textColor=_MUTED, fontName="Helvetica-Oblique", spaceBefore=3, spaceAfter=4),
        "cell": mk("c", fontSize=8.6, leading=11),
        "cellh": mk("ch", fontSize=8.4, leading=11, fontName="Helvetica-Bold", textColor=_BRAND),
        "tt": mk("tt", fontName="Helvetica-Bold", fontSize=9.6, spaceBefore=8, spaceAfter=3),
        "kpi_l": mk("kl", fontSize=7.8, leading=10, textColor=_MUTED),
        "kpi_v": mk("kv", fontName="Helvetica-Bold", fontSize=12.5, leading=15),
        "kpi_c": mk("kc", fontSize=8, leading=10),
    }


def _clean(text: Any) -> str:
    """The PDF uses the built-in Helvetica (no font files to install), which cannot draw the
    rupee sign and would print a black square. Write it as "Rs."; drop anything else it cannot draw."""
    t = str(text).replace("\u20b9", "Rs.")
    return t.encode("cp1252", "ignore").decode("cp1252")


def _esc(text: Any) -> str:
    return escape(_clean(text))


def _p(text: Any, style: ParagraphStyle) -> Paragraph:
    return Paragraph(_esc(text), style)


def _page_ref(page: Any) -> str:
    return f" <font color='#5f6b7a' size='7.5'>[p. {page}]</font>" if page else ""


def _banner(report: dict[str, Any], st: dict[str, ParagraphStyle]) -> Table:
    left = [_p(report["company"], st["banner_t"]),
            _p(f"Fiscal year {report['fiscal_year']} research report", st["banner_s"])]
    right = _p(datetime.now().strftime("%d %b %Y"), st["banner_s"])
    t = Table([[left, right]], colWidths=[_WIDTH - 32 * mm, 32 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), _BRAND), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 13), ("BOTTOMPADDING", (0, 0), (-1, -1), 13),
    ]))
    return t


def _glance(highlights: list[dict[str, str]], st: dict[str, ParagraphStyle]) -> list:
    """Headline numbers as tiles, three per row."""
    if not highlights:
        return []
    tiles: list = []
    for h in highlights:
        change = h.get("change") or "—"
        color = _UP if change.startswith("+") else _DOWN if change.startswith("-") else "#5f6b7a"
        label = "vs last year" if change != "—" else ""
        tiles.append([_p(h["label"], st["kpi_l"]), _p(h["value"], st["kpi_v"]),
                      Paragraph(f"<font color='{color}'><b>{_esc(change)}</b></font> "
                                f"<font color='#5f6b7a'>{label}</font>", st["kpi_c"])])
    while len(tiles) % 3:
        tiles.append("")
    rows = [tiles[i:i + 3] for i in range(0, len(tiles), 3)]
    t = Table(rows, colWidths=[_WIDTH / 3] * 3)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), _BRAND_SOFT),
        ("LINEAFTER", (0, 0), (-2, -1), 3, colors.white), ("LINEBELOW", (0, 0), (-1, -2), 3, colors.white),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10), ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return [Spacer(1, 10), _p("At a glance", st["tt"]), t]


def _table(spec: dict[str, Any], st: dict[str, ParagraphStyle]) -> list:
    cols = list(spec["columns"])
    with_page = cols[-1] == "Change"                  # metrics tables carry the source page
    if with_page:
        cols.append("Page")
    data = [[_p(c, st["cellh"]) for c in cols]]
    for r in spec["rows"]:
        cells = list(r["cells"]) + ([str(r["page"]) if r.get("page") else "—"] if with_page else [])
        data.append([_p(c, st["cell"]) for c in cells])
    first = 0.30 * _WIDTH if len(cols) > 2 else 0.5 * _WIDTH
    rest = (_WIDTH - first) / max(1, len(cols) - 1)
    t = Table(data, colWidths=[first] + [rest] * (len(cols) - 1), repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), _BRAND_SOFT),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, _BRAND),
        ("LINEBELOW", (0, 1), (-1, -1), 0.25, _RULE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
    ]
    for i, r in enumerate(spec["rows"], start=1):
        if i % 2 == 0:
            style.append(("BACKGROUND", (0, i), (-1, i), _ZEBRA))
        if r.get("warn"):
            style.append(("TEXTCOLOR", (0, i), (-1, i), colors.HexColor("#b54708")))
    t.setStyle(TableStyle(style))
    return [KeepTogether([_p(spec["title"], st["tt"]), t]), Spacer(1, 4)]


def _flag(f: dict[str, Any], st: dict[str, ParagraphStyle]) -> KeepTogether:
    color = _SEVERITY.get(f["severity"], "#475467")
    body = [Paragraph(f"<font color='{color}'><b>{_esc(f['severity']).upper()}</b></font> &nbsp;"
                      f"<b>{_esc(f['title'])}</b>{_page_ref(f.get('page'))}", st["body"]),
            _p(f["description"], st["body"])]
    if f.get("evidence"):
        body.append(_p(f"Evidence: {f['evidence']}", st["src"]))
    t = Table([["", body]], colWidths=[3, _WIDTH - 3])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), colors.HexColor(color)),
        ("BOX", (0, 0), (-1, -1), 0.4, _RULE),
        ("LEFTPADDING", (1, 0), (1, 0), 9), ("TOPPADDING", (1, 0), (1, 0), 6), ("BOTTOMPADDING", (1, 0), (1, 0), 3),
        ("LEFTPADDING", (0, 0), (0, 0), 0), ("RIGHTPADDING", (0, 0), (0, 0), 0),
    ]))
    return KeepTogether([t, Spacer(1, 5)])


def _section(sec: dict[str, Any], st: dict[str, ParagraphStyle]) -> list:
    flow: list = [_p(sec["title"], st["h"]), HRFlowable(width="100%", thickness=0.6, color=_RULE, spaceAfter=5)]
    if sec.get("status") != "ready":
        flow.append(_p(sec.get("note") or "This section is not available.", st["note"]))
        return flow
    for s in sec.get("sentences", []):
        flow.append(Paragraph(f"{_esc(s['text'])}{_page_ref(s.get('page'))}", st["body"]))
        if s.get("quote"):
            flow.append(_p(f"“{s['quote']}”", st["src"]))
    for t in sec.get("tables", []):
        flow.extend(_table(t, st))
    for f in sec.get("flags", []):
        flow.append(_flag(f, st))
    if sec.get("note"):
        flow.append(_p(sec["note"], st["note"]))
    return flow


def _footer(canvas, doc) -> None:                 # pragma: no cover - drawing only
    canvas.saveState()
    canvas.setStrokeColor(_RULE)
    canvas.line(_MARGIN, 14 * mm, A4[0] - _MARGIN, 14 * mm)
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(_MUTED)
    canvas.drawString(_MARGIN, 10 * mm, "Generated from the uploaded annual report. Not investment advice. [p. N] = PDF page of the source.")
    canvas.drawRightString(A4[0] - _MARGIN, 10 * mm, f"Page {doc.page}")
    canvas.restoreState()


def build_pdf(report: dict[str, Any]) -> bytes:
    """Render a report dict to PDF bytes."""
    st = _styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=_MARGIN, rightMargin=_MARGIN,
                            topMargin=14 * mm, bottomMargin=20 * mm,
                            title=f"{report['company']} FY{report['fiscal_year']} research report")
    story: list = [_banner(report, st)]
    metrics = next((s for s in report.get("sections", []) if s["key"] == "metrics"), None)
    if metrics:
        story.extend(_glance(metrics.get("highlights", []), st))
    for sec in report.get("sections", []):
        story.extend(_section(sec, st))
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()
