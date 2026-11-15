"""
PDF export for a saved report (ReportLab, pure Python: no system libraries,
works on Windows). ``build_pdf(report) -> bytes``.
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
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

_INK = colors.HexColor("#1f2933")
_MUTED = colors.HexColor("#616e7c")
_RULE = colors.HexColor("#cbd2d9")
_HEAD_BG = colors.HexColor("#eef2f6")
_SEVERITY = {"High": "#b42318", "Medium": "#b54708", "Low": "#475467"}


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=base["Title"], fontSize=20, leading=24, textColor=_INK, alignment=0),
        "sub": ParagraphStyle("s", parent=base["Normal"], fontSize=10, textColor=_MUTED, spaceAfter=10),
        "h": ParagraphStyle("h", parent=base["Heading2"], fontSize=13, textColor=_INK, spaceBefore=14, spaceAfter=4),
        "body": ParagraphStyle("b", parent=base["Normal"], fontSize=9.5, leading=13.5, textColor=_INK, spaceAfter=4),
        "note": ParagraphStyle("n", parent=base["Normal"], fontSize=8.5, leading=11, textColor=_MUTED,
                               fontName="Helvetica-Oblique", spaceAfter=4),
        "cell": ParagraphStyle("c", parent=base["Normal"], fontSize=8.5, leading=11, textColor=_INK),
        "cellh": ParagraphStyle("ch", parent=base["Normal"], fontSize=8.5, leading=11, textColor=_INK,
                                fontName="Helvetica-Bold"),
        "tt": ParagraphStyle("tt", parent=base["Normal"], fontSize=9.5, fontName="Helvetica-Bold",
                             textColor=_INK, spaceBefore=6, spaceAfter=3),
    }


def _p(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(str(text)), style)


def _table(spec: dict[str, Any], st: dict[str, ParagraphStyle], width: float) -> list:
    cols = list(spec["columns"])
    with_page = cols[-1] == "Change"                  # metrics tables carry the source page
    if with_page:
        cols.append("Page")
    data = [[_p(c, st["cellh"]) for c in cols]]
    for r in spec["rows"]:
        cells = list(r["cells"]) + ([str(r["page"]) if r.get("page") else "—"] if with_page else [])
        data.append([_p(c, st["cell"]) for c in cells])
    first = 0.30 * width if len(cols) > 2 else 0.5 * width
    rest = (width - first) / max(1, len(cols) - 1)
    t = Table(data, colWidths=[first] + [rest] * (len(cols) - 1), repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), _HEAD_BG),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, _RULE),
        ("LINEBELOW", (0, 1), (-1, -1), 0.25, _RULE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    for i, r in enumerate(spec["rows"], start=1):
        if r.get("warn"):
            style.append(("TEXTCOLOR", (0, i), (-1, i), colors.HexColor("#b54708")))
    t.setStyle(TableStyle(style))
    return [_p(spec["title"], st["tt"]), t, Spacer(1, 4)]


def _section_flow(sec: dict[str, Any], st: dict[str, ParagraphStyle], width: float) -> list:
    flow: list = [_p(sec["title"], st["h"])]
    if sec.get("status") != "ready":
        flow.append(_p(sec.get("note") or "This section is not available.", st["note"]))
        return flow
    for s in sec.get("sentences", []):
        flow.append(Paragraph(f"{escape(s['text'])} <font color='#616e7c' size='8'>[p. {s['page']}]</font>", st["body"]))
    for t in sec.get("tables", []):
        flow.extend(_table(t, st, width))
    for f in sec.get("flags", []):
        color = _SEVERITY.get(f["severity"], "#475467")
        head = (f"<font color='{color}'><b>{escape(f['severity'])}</b></font> &nbsp;<b>{escape(f['title'])}</b>"
                + (f" <font color='#616e7c' size='8'>[p. {f['page']}]</font>" if f.get("page") else ""))
        block = [Paragraph(head, st["body"]), _p(f["description"], st["body"])]
        if f.get("evidence"):
            block.append(_p(f"Evidence: {f['evidence']}", st["note"]))
        flow.append(KeepTogether(block + [Spacer(1, 3)]))
    if sec.get("note"):
        flow.append(_p(sec["note"], st["note"]))
    return flow


def _footer(canvas, doc) -> None:                 # pragma: no cover - drawing only
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(_MUTED)
    canvas.drawString(18 * mm, 10 * mm, "Generated from the uploaded documents. Not investment advice.")
    canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {doc.page}")
    canvas.restoreState()


def build_pdf(report: dict[str, Any]) -> bytes:
    """Render a report dict (as returned by ``get_report``) to PDF bytes."""
    st = _styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=18 * mm,
                            title=f"{report['company']} FY{report['fiscal_year']} research report")
    width = A4[0] - 36 * mm
    story: list = [
        _p(f"{report['company']} — FY{report['fiscal_year']} research report", st["title"]),
        _p(f"Generated {datetime.now().strftime('%d %b %Y')} from the uploaded annual report. "
           f"Page references point to PDF pages.", st["sub"]),
    ]
    for sec in report.get("sections", []):
        story.extend(_section_flow(sec, st, width))
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()
