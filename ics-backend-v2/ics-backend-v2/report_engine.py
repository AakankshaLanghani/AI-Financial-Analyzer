"""
Report Engine — Auto-generated PDF Analytics Report
=====================================================
Called after /upload. Uses the existing analytics_engine + kpi_engine to
compute all sections deterministically, then renders a multi-page PDF with:

  Page 1  — Cover (ICS logo, filename, date)
  Page 2  — KPI Summary cards
  Page 3  — Sales by Region/Division + by Product Category (bar charts)
  Page 4  — GP% by Category + Gross Profit by Salesperson
  Page 5  — Customer Type + Top Products (full ranked table)
  Page 6  — AI Insights (LLM narrative)

No numbers are hardcoded. Every value is computed via analytics_engine.
"""

import io
import math
import datetime
from typing import Optional, List, Tuple, Dict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm, cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle,
    PageBreak, HRFlowable, KeepTogether,
)
from reportlab.graphics.shapes import Drawing, Rect, String, Line, Group
from reportlab.graphics import renderPDF

from analytics_engine import execute_plan, AnalyticsResult
from query_planner import build_query_plan, QueryPlan
from kpi_engine import is_ratio_metric

# ── Brand colours ──────────────────────────────────────────────────────────────
ICS_RED   = colors.HexColor("#C31D27")
ICS_DARK  = colors.HexColor("#1a1f2e")
ICS_GREY  = colors.HexColor("#6b7280")
ICS_LIGHT = colors.HexColor("#f0f2f5")
ICS_GREEN = colors.HexColor("#059669")
ICS_BLUE  = colors.HexColor("#2563eb")
ICS_PURP  = colors.HexColor("#7c3aed")

CHART_PALETTE = ["#C31D27", "#2563eb", "#059669", "#7c3aed", "#ea580c",
                 "#0891b2", "#dc2626", "#16a34a", "#9333ea", "#c2410c"]

W, H = A4  # 595 x 842 pt


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _fmt(val: Optional[float], is_pct: bool = False) -> str:
    if val is None:
        return "N/A"
    if is_pct:
        return f"{val:.1f}%"
    if abs(val) >= 1e9:
        return f"{val / 1e9:.2f}B"
    if abs(val) >= 1e6:
        return f"{val / 1e6:.2f}M"
    if abs(val) >= 1e3:
        return f"{val:,.0f}"
    return f"{val:.2f}"


def _run_query(question: str, parsed: dict) -> AnalyticsResult:
    plan = build_query_plan(question, parsed)
    return execute_plan(plan, parsed)


def _chart_bytes(fig) -> io.BytesIO:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight",
                facecolor="white", edgecolor="none")
    plt.close(fig)
    buf.seek(0)
    return buf


def _hbar_chart(
    labels: List[str],
    values: List[float],
    title: str,
    color: str = "#C31D27",
    fmt_pct: bool = False,
    width_in: float = 6.5,
    height_in: float = 3.2,
) -> io.BytesIO:
    """Horizontal bar chart — returns PNG bytes."""
    n = len(labels)
    fig, ax = plt.subplots(figsize=(width_in, max(height_in, n * 0.45 + 0.6)))
    fig.patch.set_facecolor("white")

    y = np.arange(n)
    bars = ax.barh(y, values, color=color, height=0.6,
                   edgecolor="none", zorder=3)

    # Value labels
    max_v = max(abs(v) for v in values) if values else 1
    for bar, val in zip(bars, values):
        label = f"{val:.1f}%" if fmt_pct else _fmt(val)
        offset = max_v * 0.015
        ax.text(bar.get_width() + offset, bar.get_y() + bar.get_height() / 2,
                label, va="center", ha="left", fontsize=8.5,
                color="#374151", fontweight="500")

    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=9.5, color="#1a1f2e")
    ax.invert_yaxis()
    ax.set_title(title, fontsize=11, fontweight="700", color="#1a1f2e",
                 pad=10, loc="left")
    ax.set_xlim(0, max_v * 1.22)
    ax.axvline(0, color="#e5e7eb", linewidth=0.8)
    ax.grid(axis="x", color="#f3f4f6", linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#e5e7eb")
    ax.spines["bottom"].set_color("#e5e7eb")
    ax.tick_params(axis="x", labelsize=8, colors="#9ca3af")
    ax.tick_params(axis="y", length=0)
    plt.tight_layout(pad=0.6)
    return _chart_bytes(fig)


def _pie_chart(
    labels: List[str],
    values: List[float],
    title: str,
    width_in: float = 4.0,
    height_in: float = 3.5,
) -> io.BytesIO:
    fig, ax = plt.subplots(figsize=(width_in, height_in))
    fig.patch.set_facecolor("white")
    colors_list = CHART_PALETTE[:len(labels)]
    wedges, texts, autotexts = ax.pie(
        values, labels=None, colors=colors_list,
        autopct="%1.1f%%", pctdistance=0.82,
        startangle=90, wedgeprops=dict(linewidth=0.5, edgecolor="white"),
    )
    for t in autotexts:
        t.set_fontsize(8)
        t.set_color("white")
        t.set_fontweight("600")
    ax.legend(labels, loc="lower center", bbox_to_anchor=(0.5, -0.12),
              ncol=2, fontsize=7.5, frameon=False)
    ax.set_title(title, fontsize=10.5, fontweight="700", color="#1a1f2e", pad=8)
    plt.tight_layout(pad=0.4)
    return _chart_bytes(fig)


# ─────────────────────────────────────────────────────────────────────────────
# ICS LOGO  (drawn programmatically via ReportLab — no external file needed)
# ─────────────────────────────────────────────────────────────────────────────

def _ics_logo_drawing(width: float = 120, height: float = 36) -> Drawing:
    """Recreate the ICS logo (asterisk + wordmark) as a ReportLab Drawing."""
    d = Drawing(width, height)
    cx = height * 0.48
    cy = height * 0.52
    bar_w = height * 0.16
    bar_h = height * 0.84
    rx = bar_w * 0.4
    r_col = colors.HexColor("#C31D27")

    def _rotated_rect(angle_deg):
        """Return a transformed rect at given rotation."""
        import math
        rad = math.radians(angle_deg)
        cos_a, sin_a = math.cos(rad), math.sin(rad)
        # Build corners of the rectangle centred at (0,0)
        hw, hh = bar_w / 2, bar_h / 2
        corners = [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)]
        rotated = [(cx + x * cos_a - y * sin_a, cy + x * sin_a + y * cos_a)
                   for x, y in corners]
        pts = []
        for px, py in rotated:
            pts += [px, py]
        from reportlab.graphics.shapes import Polygon
        return Polygon(pts, fillColor=r_col, strokeColor=None)

    for angle in (0, 60, 120):
        d.add(_rotated_rect(angle))

    # "ICS" text
    from reportlab.graphics.shapes import String
    from reportlab.lib.fonts import addMapping
    d.add(String(
        height * 1.05, height * 0.18,
        "ICS",
        fontSize=height * 0.78,
        fillColor=colors.HexColor("#111111"),
        fontName="Times-Roman",
    ))
    return d


# ─────────────────────────────────────────────────────────────────────────────
# STYLES
# ─────────────────────────────────────────────────────────────────────────────

def _styles():
    base = getSampleStyleSheet()
    styles = {}
    styles["h1"] = ParagraphStyle(
        "h1", parent=base["Normal"],
        fontSize=26, fontName="Helvetica-Bold", textColor=ICS_DARK,
        spaceAfter=6, letterSpacing=-0.5,
    )
    styles["h2"] = ParagraphStyle(
        "h2", parent=base["Normal"],
        fontSize=15, fontName="Helvetica-Bold", textColor=ICS_DARK,
        spaceAfter=4, spaceBefore=10,
    )
    styles["h3"] = ParagraphStyle(
        "h3", parent=base["Normal"],
        fontSize=11.5, fontName="Helvetica-Bold", textColor=ICS_DARK,
        spaceAfter=3, spaceBefore=6,
    )
    styles["body"] = ParagraphStyle(
        "body", parent=base["Normal"],
        fontSize=9.5, fontName="Helvetica", textColor=colors.HexColor("#374151"),
        spaceAfter=4, leading=15,
    )
    styles["small"] = ParagraphStyle(
        "small", parent=base["Normal"],
        fontSize=8.5, fontName="Helvetica", textColor=ICS_GREY,
        spaceAfter=2, leading=13,
    )
    styles["caption"] = ParagraphStyle(
        "caption", parent=base["Normal"],
        fontSize=8, fontName="Helvetica", textColor=ICS_GREY,
        alignment=TA_CENTER, spaceAfter=2,
    )
    styles["kpi_value"] = ParagraphStyle(
        "kpi_value", parent=base["Normal"],
        fontSize=20, fontName="Helvetica-Bold", textColor=ICS_DARK,
        alignment=TA_CENTER, spaceAfter=2,
    )
    styles["kpi_label"] = ParagraphStyle(
        "kpi_label", parent=base["Normal"],
        fontSize=8.5, fontName="Helvetica", textColor=ICS_GREY,
        alignment=TA_CENTER, spaceAfter=0,
    )
    styles["insight"] = ParagraphStyle(
        "insight", parent=base["Normal"],
        fontSize=10, fontName="Helvetica", textColor=colors.HexColor("#1f2937"),
        spaceAfter=6, leading=16,
        leftIndent=12, borderPad=4,
    )
    styles["cover_sub"] = ParagraphStyle(
        "cover_sub", parent=base["Normal"],
        fontSize=11, fontName="Helvetica", textColor=ICS_GREY,
        spaceAfter=4, alignment=TA_CENTER,
    )
    return styles


# ─────────────────────────────────────────────────────────────────────────────
# PAGE TEMPLATE (header/footer on every page except cover)
# ─────────────────────────────────────────────────────────────────────────────

class _ReportDoc(SimpleDocTemplate):
    def __init__(self, buf, filename: str, **kw):
        super().__init__(buf, **kw)
        self._filename = filename
        self._page_num = 0

    def handle_pageEnd(self):
        self._page_num += 1
        canvas = self.canv
        if self._page_num == 1:
            super().handle_pageEnd()
            return
        # Header bar
        canvas.saveState()
        canvas.setFillColor(colors.HexColor("#f8f9fa"))
        canvas.rect(0, H - 38, W, 38, fill=1, stroke=0)
        canvas.setStrokeColor(colors.HexColor("#e5e7eb"))
        canvas.setLineWidth(0.5)
        canvas.line(0, H - 38, W, H - 38)
        canvas.setFont("Helvetica-Bold", 9)
        canvas.setFillColor(ICS_RED)
        canvas.drawString(28, H - 24, "ICS")
        canvas.setFillColor(ICS_DARK)
        canvas.drawString(48, H - 24, "AI Financial Analyzer")
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(ICS_GREY)
        canvas.drawRightString(W - 28, H - 24, f"Page {self._page_num - 1}")
        # Footer
        canvas.setFillColor(colors.HexColor("#f8f9fa"))
        canvas.rect(0, 0, W, 28, fill=1, stroke=0)
        canvas.setStrokeColor(colors.HexColor("#e5e7eb"))
        canvas.line(0, 28, W, 28)
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(ICS_GREY)
        canvas.drawCentredString(
            W / 2, 10,
            f"Report generated by AI Financial Analyzer — ICS  ·  {self._filename}  ·  "
            f"{datetime.datetime.now().strftime('%d %b %Y')}",
        )
        canvas.restoreState()
        super().handle_pageEnd()


# ─────────────────────────────────────────────────────────────────────────────
# SECTION BUILDERS
# ─────────────────────────────────────────────────────────────────────────────

def _cover_page(filename: str, parsed: dict, S: dict) -> list:
    sheet_count = len(parsed.get("sheets", []))
    total_rows  = sum(len(s.get("rows", [])) for s in parsed.get("sheets", []))
    date_str    = datetime.datetime.now().strftime("%d %B %Y")

    story = []
    story.append(Spacer(1, 60))

    # Logo
    logo_d = _ics_logo_drawing(width=160, height=48)
    story.append(_drawing_flowable(logo_d, 160, 48, align="center"))
    story.append(Spacer(1, 28))

    # Title
    story.append(Paragraph("Financial Analytics Report", S["h1"]))
    story.append(Spacer(1, 6))

    # File pill
    story.append(Paragraph(f"<b>{filename}</b>", ParagraphStyle(
        "fc", fontSize=13, fontName="Helvetica-Bold",
        textColor=ICS_RED, alignment=TA_CENTER, spaceAfter=4,
    )))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        f"{sheet_count} sheet(s)  ·  {total_rows:,} rows  ·  Generated {date_str}",
        S["cover_sub"],
    ))
    story.append(Spacer(1, 36))

    # Divider
    story.append(HRFlowable(width="60%", thickness=1.5, color=ICS_RED,
                             spaceBefore=0, spaceAfter=24, hAlign="CENTER"))

    story.append(Paragraph(
        "This report was automatically generated by the ICS AI Financial Analyzer. "
        "All metrics are computed deterministically from your uploaded data — "
        "no estimates, no hallucinations.",
        ParagraphStyle("cc", fontSize=10, fontName="Helvetica", textColor=ICS_GREY,
                       alignment=TA_CENTER, leading=17),
    ))
    story.append(Spacer(1, 12))

    # Contents list
    contents = [
        "KPI Summary", "Sales Breakdown", "Profitability Analysis",
        "Customer & Product Detail", "AI Insights",
    ]
    tbl_data = [[Paragraph(f"<b>{i+1}.</b>  {c}", S["body"])] for i, c in enumerate(contents)]
    tbl = Table(tbl_data, colWidths=[220])
    tbl.setStyle(TableStyle([
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING",   (0, 0), (-1, -1), 12),
    ]))
    story.append(_centred(tbl))

    story.append(PageBreak())
    return story


def _kpi_section(parsed: dict, S: dict) -> list:
    """Page 2 — big KPI cards."""
    story = []
    story.append(Paragraph("KPI Summary", S["h2"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#e5e7eb"),
                             spaceBefore=2, spaceAfter=10))

    # Compute KPIs
    kpis = []
    for q, label, is_pct in [
        ("What were the total sales?",           "Total Sales",     False),
        ("What was the total gross profit?",      "Gross Profit",    False),
        ("What was the overall gross profit margin?", "GP Margin",   True),
        ("What is the total quantity sold?",      "Total Quantity",  False),
    ]:
        try:
            r = _run_query(q, parsed)
            val = r.scalar_value
        except Exception:
            val = None
        kpis.append((label, val, is_pct))

    # Add row count
    total_rows = sum(len(s.get("rows", [])) for s in parsed.get("sheets", []))
    kpis.append(("Data Records", float(total_rows), False))

    def _kpi_cell(label: str, val: Optional[float], is_pct: bool, accent: str):
        val_str = _fmt(val, is_pct) if val is not None else "N/A"
        return Table(
            [[Paragraph(val_str, ParagraphStyle(
                "kv", fontSize=18, fontName="Helvetica-Bold",
                textColor=colors.HexColor(accent), alignment=TA_CENTER,
            ))],
             [Paragraph(label, S["kpi_label"])]],
            colWidths=[100],
        )

    accents = ["#C31D27", "#059669", "#2563eb", "#7c3aed", "#374151"]
    cards = [_kpi_cell(l, v, p, accents[i % len(accents)]) for i, (l, v, p) in enumerate(kpis)]

    # Layout: 3 + 2 or 5 in a row
    row1 = cards[:3]
    row2 = cards[3:]
    for row in [row1, row2]:
        tbl = Table([row], colWidths=[110] * len(row), hAlign="CENTER")
        tbl.setStyle(TableStyle([
            ("BOX",           (0, 0), (-1, -1), 0.5, colors.HexColor("#e5e7eb")),
            ("INNERGRID",     (0, 0), (-1, -1), 0.5, colors.HexColor("#e5e7eb")),
            ("BACKGROUND",    (0, 0), (-1, -1), colors.HexColor("#fafafa")),
            ("TOPPADDING",    (0, 0), (-1, -1), 14),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 14),
            ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
            ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
            ("ROUNDEDCORNERS", [6, 6, 6, 6]),
        ]))
        story.append(tbl)
        story.append(Spacer(1, 10))

    # Sheets table
    story.append(Spacer(1, 6))
    story.append(Paragraph("Workbook Structure", S["h3"]))
    sheets = parsed.get("sheets", [])
    tbl_data = [["Sheet Name", "Rows", "Table Type"]]
    for s in sheets:
        tbl_data.append([
            s.get("sheet_name", ""), str(len(s.get("rows", []))),
            s.get("table_type", "UNKNOWN"),
        ])
    tbl = Table(tbl_data, colWidths=[200, 70, 130])
    tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0),  ICS_DARK),
        ("TEXTCOLOR",     (0, 0), (-1, 0),  colors.white),
        ("FONTNAME",      (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, -1), 8.5),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, colors.HexColor("#f9fafb")]),
        ("GRID",          (0, 0), (-1, -1), 0.4, colors.HexColor("#e5e7eb")),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 8),
        ("ALIGN",         (0, 0), (-1, 0),  "CENTER"),
        ("ALIGN",         (1, 1), (2, -1),  "CENTER"),
    ]))
    story.append(tbl)
    story.append(PageBreak())
    return story


def _sales_breakdown(parsed: dict, S: dict) -> list:
    """Sales by region/division and by category, with chart descriptions."""
    story = []
    story.append(Paragraph("Sales Breakdown", S["h2"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#e5e7eb"),
                             spaceBefore=2, spaceAfter=10))

    # ── Sales by region / division ────────────────────────────────────────────
    region_result = None
    for q in ["What is the total sales for each region?",
              "What is total sales by division?",
              "What is total sales by department?"]:
        try:
            r = _run_query(q, parsed)
            if r.groups and len(r.groups) >= 2:
                region_result = r
                break
        except Exception:
            pass

    if region_result:
        labels  = [g.label for g in region_result.groups]
        values  = [g.value or 0 for g in region_result.groups]
        total_v = sum(values)
        top3_v  = sum(values[:3])
        top     = region_result.groups[0]
        bottom  = region_result.groups[-1]
        buf = _hbar_chart(labels, values, "Sales by Region / Division",
                          color="#2563eb", width_in=6.8,
                          height_in=max(3.0, len(labels) * 0.5))
        desc = (
            f"<b>{top.label}</b> leads regional sales at <b>{_fmt(top.value)}</b>, "
            f"contributing {(top.value or 0)/total_v*100:.1f}% of total revenue. "
            f"The top 3 regions collectively account for "
            f"{top3_v/total_v*100:.1f}% of all sales. "
            f"<b>{bottom.label}</b> is the lowest-contributing region "
            f"at <b>{_fmt(bottom.value)}</b>."
        )
        story.append(KeepTogether([_img_flowable(buf, W - 80), _chart_desc(desc, S)]))
        story.append(Spacer(1, 6))

    # ── Sales by product category ─────────────────────────────────────────────
    cat_result = None
    for q in ["What are total sales by product category?",
              "What are total sales by category?",
              "What are total sales by product?"]:
        try:
            r = _run_query(q, parsed)
            if r.groups and len(r.groups) >= 2:
                cat_result = r
                break
        except Exception:
            pass

    if cat_result:
        all_groups = sorted(cat_result.groups, key=lambda g: g.value or 0, reverse=True)
        top8       = all_groups[:8]
        labels     = [g.label for g in top8]
        values     = [g.value or 0 for g in top8]
        total_v    = sum(g.value or 0 for g in all_groups)
        top1_pct   = values[0] / total_v * 100 if total_v else 0
        top3_pct   = sum(values[:3]) / total_v * 100 if total_v else 0

        buf = _hbar_chart(labels, values, "Sales by Product Category (Top 8)",
                          color="#C31D27", width_in=6.8,
                          height_in=max(3.0, len(labels) * 0.5))
        desc = (
            f"<b>{labels[0]}</b> is the top-selling product with <b>{_fmt(values[0])}</b> "
            f"({top1_pct:.1f}% of total revenue). "
            f"The top 3 products — {labels[0]}, {labels[1]}, and {labels[2]} — "
            f"collectively account for {top3_pct:.1f}% of total sales. "
            f"The remaining {len(all_groups) - 3} products share the other "
            f"{100 - top3_pct:.1f}%."
        )
        story.append(KeepTogether([_img_flowable(buf, W - 80), _chart_desc(desc, S)]))

        # Pie chart
        if len(top8) <= 8:
            buf2 = _pie_chart(labels, values, "Category Share of Sales")
            top3_str = ", ".join(
                f"{labels[i]} ({values[i]/total_v*100:.1f}%)" for i in range(min(3, len(labels)))
            )
            pie_desc = (
                f"The three largest product segments by revenue are {top3_str}. "
                f"Product concentration is "
                f"{'high' if top1_pct > 25 else 'moderate'} — "
                f"the leading product alone captures {top1_pct:.1f}% of all revenue. "
                f"Diversifying focus toward mid-tier products could help reduce dependency "
                f"on a single product line."
            )
            story.append(Spacer(1, 8))
            story.append(KeepTogether([
                _img_flowable(buf2, 280, hAlign="CENTER", max_height=240),
                _chart_desc(pie_desc, S),
            ]))

    # ── Full ranked table ─────────────────────────────────────────────────────
    ranked_result = None
    for q in ["What are total sales by product?",
              "What are total sales by product category?"]:
        try:
            r = _run_query(q, parsed)
            if r.groups and len(r.groups) >= 2:
                ranked_result = r
                break
        except Exception:
            pass

    if ranked_result:
        story.append(Spacer(1, 10))
        story.append(Paragraph("Full Ranked Table — Sales by Product/Category", S["h3"]))
        tbl_data = [["Rank", "Name", "Sales", "% of Total"]]
        total = sum(g.value or 0 for g in ranked_result.groups)
        for i, g in enumerate(ranked_result.groups, 1):
            pct = f"{(g.value or 0) / total * 100:.1f}%" if total else "-"
            tbl_data.append([str(i), g.label, _fmt(g.value), pct])
        tbl = Table(tbl_data, colWidths=[35, 220, 100, 70], repeatRows=1)
        tbl.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, 0), ICS_DARK),
            ("TEXTCOLOR",     (0, 0), (-1, 0), colors.white),
            ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",      (0, 0), (-1, -1), 8.5),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1),
             [colors.white, colors.HexColor("#f9fafb")]),
            ("GRID",          (0, 0), (-1, -1), 0.4, colors.HexColor("#e5e7eb")),
            ("TOPPADDING",    (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING",   (0, 0), (-1, -1), 8),
            ("ALIGN",         (0, 0), (0, -1),  "CENTER"),
            ("ALIGN",         (2, 1), (-1, -1), "RIGHT"),
        ]))
        story.append(tbl)

    story.append(PageBreak())
    return story


def _profitability(parsed: dict, S: dict) -> list:
    """Profitability — GP% by category + Gross Profit by Salesperson."""
    story = []
    story.append(Paragraph("Profitability Analysis", S["h2"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#e5e7eb"),
                             spaceBefore=2, spaceAfter=10))

    # ── GP% by category ───────────────────────────────────────────────────────
    gp_result = None
    for q in ["What is the gross profit margin for each product category?",
              "What is the gross profit margin by category?"]:
        try:
            r = _run_query(q, parsed)
            if r.groups and len(r.groups) >= 2:
                gp_result = r
                break
        except Exception:
            pass

    if gp_result:
        groups  = sorted(gp_result.groups, key=lambda g: g.value or 0, reverse=True)
        labels  = [g.label for g in groups]
        values  = [g.value or 0 for g in groups]
        avg_gp  = sum(values) / len(values) if values else 0
        above   = sum(1 for v in values if v > avg_gp)
        buf = _hbar_chart(labels, values, "Gross Profit Margin % by Category",
                          color="#059669", fmt_pct=True,
                          width_in=6.8, height_in=max(3.0, len(labels) * 0.48))
        desc = (
            f"<b>{labels[0]}</b> achieves the highest GP margin at <b>{values[0]:.1f}%</b>, "
            f"while <b>{labels[-1]}</b> has the lowest at <b>{values[-1]:.1f}%</b>. "
            f"The average GP margin across all categories is {avg_gp:.1f}%, "
            f"with {above} of {len(labels)} categories performing above average. "
            f"Wide variation in margins indicates differences in pricing strategy "
            f"or cost structure across products."
        )
        story.append(KeepTogether([_img_flowable(buf, W - 80), _chart_desc(desc, S)]))
        story.append(Spacer(1, 8))

    # ── Gross Profit by Salesperson ───────────────────────────────────────────
    sp_result = None
    for q in ["What was the gross profit for each salesperson?",
              "What is gross profit by sales person?"]:
        try:
            r = _run_query(q, parsed)
            if r.groups and len(r.groups) >= 2:
                sp_result = r
                break
        except Exception:
            pass

    if sp_result:
        groups  = sorted(sp_result.groups, key=lambda g: g.value or 0, reverse=True)[:10]
        labels  = [g.label for g in groups]
        values  = [g.value or 0 for g in groups]
        total_v = sum(values)
        top2_v  = sum(values[:2])
        gap_pct = ((values[0] - values[1]) / values[1] * 100
                   if len(values) > 1 and values[1] else 0)

        buf = _hbar_chart(labels, values, "Gross Profit by Salesperson (Top 10)",
                          color="#C31D27",
                          width_in=6.8, height_in=max(3.0, len(labels) * 0.48))
        desc = (
            f"<b>{labels[0]}</b> leads the team with <b>{_fmt(values[0])}</b> in gross profit, "
            f"ahead of {labels[1]} by {gap_pct:.1f}%. "
            f"The top 2 performers together account for "
            f"{top2_v/total_v*100:.1f}% of total team gross profit. "
            f"A strong performance gap between the top tier and the rest of the team "
            f"suggests an opportunity to coach mid-tier performers."
        )
        story.append(KeepTogether([_img_flowable(buf, W - 80), _chart_desc(desc, S)]))

        # Leaderboard table — keep header + all rows together if short enough
        story.append(Spacer(1, 10))
        tbl_data = [["Rank", "Salesperson", "Gross Profit", "Rows"]]
        for i, g in enumerate(groups, 1):
            tbl_data.append([str(i), g.label, _fmt(g.value), str(g.row_count)])
        tbl = Table(tbl_data, colWidths=[35, 200, 120, 70])
        tbl.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, 0), ICS_DARK),
            ("TEXTCOLOR",     (0, 0), (-1, 0), colors.white),
            ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
            ("BACKGROUND",    (0, 1), (-1, 1), colors.HexColor("#fff7f7")),
            ("FONTSIZE",      (0, 0), (-1, -1), 8.5),
            ("ROWBACKGROUNDS", (0, 2), (-1, -1),
             [colors.white, colors.HexColor("#f9fafb")]),
            ("GRID",          (0, 0), (-1, -1), 0.4, colors.HexColor("#e5e7eb")),
            ("TOPPADDING",    (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING",   (0, 0), (-1, -1), 8),
            ("ALIGN",         (0, 0), (0, -1), "CENTER"),
            ("ALIGN",         (2, 1), (-1, -1), "RIGHT"),
        ]))
        story.append(KeepTogether([Paragraph("Salesperson Leaderboard", S["h3"]), tbl]))

    story.append(PageBreak())
    return story


def _customer_product(parsed: dict, S: dict) -> list:
    """Customer Type + Top/Bottom products with chart descriptions."""
    story = []
    story.append(Paragraph("Customer & Product Detail", S["h2"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#e5e7eb"),
                             spaceBefore=2, spaceAfter=10))

    # ── Sales by Customer Type ────────────────────────────────────────────────
    cust_result = None
    for q in ["What are total sales by customer type?",
              "Show me sales broken down by customer type"]:
        try:
            r = _run_query(q, parsed)
            if r.groups and len(r.groups) >= 2:
                cust_result = r
                break
        except Exception:
            pass

    if cust_result:
        labels  = [g.label for g in cust_result.groups]
        values  = [g.value or 0 for g in cust_result.groups]
        total_v = sum(values)
        top     = cust_result.groups[0]
        top_pct = (top.value or 0) / total_v * 100 if total_v else 0
        top2_pct = sum(values[:2]) / total_v * 100 if total_v else 0

        buf = _hbar_chart(labels, values, "Sales by Customer Type",
                          color="#ea580c",
                          width_in=6.8, height_in=max(2.5, len(labels) * 0.52))
        desc = (
            f"<b>{labels[0]}</b> is the dominant customer segment with "
            f"<b>{_fmt(top.value)}</b> in sales ({top_pct:.1f}% of total). "
            f"The top two segments — {labels[0]} and {labels[1]} — together represent "
            f"{top2_pct:.1f}% of total revenue. "
            f"Prioritising growth within the leading segments can deliver the "
            f"highest revenue impact."
        )
        story.append(KeepTogether([_img_flowable(buf, W - 80), _chart_desc(desc, S)]))
        story.append(Spacer(1, 8))

    # ── Top / Bottom products ─────────────────────────────────────────────────
    prod_result = None
    for q in ["What are total sales by product?",
              "What are total sales by product category?"]:
        try:
            r = _run_query(q, parsed)
            if r.groups and len(r.groups) >= 2:
                prod_result = r
                break
        except Exception:
            pass

    if prod_result:
        top5    = sorted(prod_result.groups, key=lambda g: g.value or 0, reverse=True)[:5]
        bottom5 = sorted(prod_result.groups, key=lambda g: g.value or 0)[:5]

        story.append(KeepTogether([
            Paragraph("Top 5 Products by Sales", S["h3"]),
            _rank_table(top5, "#C31D27"),
        ]))
        story.append(Spacer(1, 12))
        story.append(KeepTogether([
            Paragraph("Bottom 5 Products by Sales", S["h3"]),
            _rank_table(bottom5, "#6b7280"),
        ]))

    # ── Sales by City / Region ────────────────────────────────────────────────
    city_result = None
    for q in ["What is the total sales for each region?",
              "What is total sales by city?"]:
        try:
            r = _run_query(q, parsed)
            if r.groups and len(r.groups) >= 2:
                city_result = r
                break
        except Exception:
            pass

    if city_result:
        tbl_data = [["City / Region", "Sales"]]
        for g in city_result.groups:
            tbl_data.append([g.label, _fmt(g.value)])
        tbl = Table(tbl_data, colWidths=[250, 120])
        tbl.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, 0), ICS_DARK),
            ("TEXTCOLOR",     (0, 0), (-1, 0), colors.white),
            ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE",      (0, 0), (-1, -1), 8.5),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1),
             [colors.white, colors.HexColor("#f9fafb")]),
            ("GRID",          (0, 0), (-1, -1), 0.4, colors.HexColor("#e5e7eb")),
            ("TOPPADDING",    (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING",   (0, 0), (-1, -1), 8),
            ("ALIGN",         (1, 1), (1, -1), "RIGHT"),
        ]))
        story.append(Spacer(1, 14))
        # KeepTogether so the heading never strands at the bottom of a page
        story.append(KeepTogether([
            Paragraph("Sales by City / Region", S["h3"]),
            tbl,
        ]))

    story.append(PageBreak())
    return story


def _rank_table(groups, accent_hex: str):
    total = sum(g.value or 0 for g in groups)
    tbl_data = [["#", "Name", "Value", "Share"]]
    for i, g in enumerate(groups, 1):
        pct = f"{(g.value or 0) / total * 100:.1f}%" if total else "-"
        tbl_data.append([str(i), g.label, _fmt(g.value), pct])
    tbl = Table(tbl_data, colWidths=[30, 220, 110, 65])
    tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), colors.HexColor(accent_hex)),
        ("TEXTCOLOR",     (0, 0), (-1, 0), colors.white),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, -1), 8.5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.HexColor("#f9fafb")]),
        ("GRID",          (0, 0), (-1, -1), 0.4, colors.HexColor("#e5e7eb")),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 8),
        ("ALIGN",         (0, 0), (0, -1), "CENTER"),
        ("ALIGN",         (2, 1), (-1, -1), "RIGHT"),
    ]))
    return tbl


def _chart_desc(text: str, S: dict) -> Paragraph:
    """Styled 2–3 line description block rendered below each chart."""
    st = ParagraphStyle(
        "chart_desc",
        parent=S["body"],
        fontSize=9,
        textColor=colors.HexColor("#374151"),
        leading=15,
        spaceBefore=6,
        spaceAfter=12,
        leftIndent=10,
        rightIndent=10,
        borderPad=8,
        backColor=colors.HexColor("#f9fafb"),
    )
    return Paragraph(text, st)


def _ai_insights(parsed: dict, S: dict, api_key: str) -> list:
    """Page 6 — LLM-generated key insights."""
    story = []
    story.append(Paragraph("AI Insights", S["h2"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#e5e7eb"),
                             spaceBefore=2, spaceAfter=10))

    # Gather pre-computed facts for the LLM
    facts = []
    insight_queries = [
        ("What were the total sales?",               "Total Sales"),
        ("What was the total gross profit?",          "Gross Profit"),
        ("What was the overall gross profit margin?", "Overall GP%"),
        ("Which product had the highest sales?",      "Top Product"),
        ("Which salesperson had the highest sales?",  "Top Salesperson"),
        ("Which region had the lowest gross profit margin?", "Lowest GP% Region"),
        ("What is the total sales for each region?",  "Regional Sales"),
    ]
    for q, label in insight_queries:
        try:
            r = _run_query(q, parsed)
            if r.groups:
                top = r.groups[0]
                facts.append(f"{label}: {top.label} = {_fmt(top.value, r.is_weighted_pct)}")
            elif r.scalar_value is not None:
                facts.append(f"{label}: {_fmt(r.scalar_value, r.is_weighted_pct)}")
        except Exception:
            pass

    if not api_key:
        story.append(Paragraph(
            "AI Insights require an OpenAI API key to be configured.",
            S["body"],
        ))
        story.append(PageBreak())
        return story

    facts_str = "\n".join(f"\u2022 {f}" for f in facts)
    prompt = (
        f"You are a senior financial analyst writing a concise executive summary "
        f"for a one-page PDF report. Based on the following pre-computed metrics "
        f"from the data, write 5-6 bullet-point insights. Each bullet should be "
        f"1-2 sentences. Focus on performance highlights, anomalies, and actionable "
        f"observations. Do NOT invent numbers not listed below.\n\n"
        f"COMPUTED METRICS:\n{facts_str}\n\n"
        f"Format: Return a JSON object with key 'insights' containing a list of strings."
    )

    try:
        from openai import OpenAI
        import json
        client = OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=600,
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content)
        insights = data.get("insights", [])
    except Exception as e:
        insights = [f"AI insights unavailable: {e}"]

    for i, insight in enumerate(insights):
        bullet_style = ParagraphStyle(
            f"b{i}", parent=S["body"],
            leftIndent=16, firstLineIndent=-14,
            spaceAfter=8, leading=16,
        )
        story.append(Paragraph(f"<b>\u25c6</b>  {insight}", bullet_style))

    story.append(Spacer(1, 20))

    # Disclaimer
    story.append(HRFlowable(width="100%", thickness=0.4,
                             color=colors.HexColor("#e5e7eb"),
                             spaceBefore=8, spaceAfter=8))
    story.append(Paragraph(
        "<i>All figures in this report are computed directly from the uploaded data. "
        "AI-generated insights are based solely on the pre-computed metrics shown above "
        "and should be validated against source data before business decisions are made.</i>",
        S["small"],
    ))
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        "<b>Report generated by AI Financial Analyzer — ICS</b>",
        ParagraphStyle("brand", fontSize=8.5, fontName="Helvetica-Bold",
                       textColor=ICS_RED, alignment=TA_CENTER),
    ))
    return story


# ─────────────────────────────────────────────────────────────────────────────
# LAYOUT HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _img_flowable(buf: io.BytesIO, max_width: float,
                  hAlign: str = "CENTER", max_height: float = 340) -> Image:
    img = Image(buf)
    scale = min(1.0, max_width / img.drawWidth)
    if img.drawHeight * scale > max_height:
        scale = max_height / img.drawHeight
    img.drawWidth  *= scale
    img.drawHeight *= scale
    img.hAlign = hAlign
    return img


def _drawing_flowable(d, w, h, align="CENTER"):
    from reportlab.platypus.flowables import Flowable

    class _DrawingFlowable(Flowable):
        def __init__(self, drawing, width, height, halign):
            super().__init__()
            self._d = drawing
            self.width = width
            self.height = height
            self._ha = halign

        def draw(self):
            x = (self.canv._pagesize[0] - self.width) / 2 if self._ha == "center" else 0
            renderPDF.draw(self._d, self.canv, 0, 0)

    return _DrawingFlowable(d, w, h, align)


def _centred(tbl):
    tbl.hAlign = "CENTER"
    return tbl


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def generate_report(parsed: dict, filename: str, api_key: str = "") -> bytes:
    """
    Generate a full PDF analytics report for the uploaded workbook.

    Parameters:
        parsed   -- the output of parse_workbook()
        filename -- original filename (shown in header/footer)
        api_key  -- OpenAI API key for AI insights page (optional)

    Returns:
        PDF bytes
    """
    buf = io.BytesIO()
    doc = _ReportDoc(
        buf,
        filename=filename,
        pagesize=A4,
        leftMargin=28 * mm,
        rightMargin=28 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
    )

    S = _styles()
    story = []

    story += _cover_page(filename, parsed, S)
    story += _kpi_section(parsed, S)
    story += _sales_breakdown(parsed, S)
    story += _profitability(parsed, S)
    story += _customer_product(parsed, S)
    story += _ai_insights(parsed, S, api_key)

    doc.build(story)
    buf.seek(0)
    return buf.read()
