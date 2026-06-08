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
import matplotlib.ticker as mticker
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

import re
import pandas as pd

from analytics_engine import execute_plan, AnalyticsResult
from query_planner import build_query_plan, QueryPlan
from kpi_engine import is_ratio_metric


# ─────────────────────────────────────────────────────────────────────────────
# DIRECT DATA ACCESS  (bypasses NLP pipeline for 100% accuracy in reports)
# ─────────────────────────────────────────────────────────────────────────────

def _get_raw_df(parsed: dict) -> pd.DataFrame:
    """Return DataFrame with original Excel column names, subtotals excluded."""
    sheets = parsed.get("sheets", [])
    if not sheets:
        return pd.DataFrame()
    sheet = sheets[0]
    rows = [r for r in sheet.get("rows", []) if not r.get("is_subtotal", False)]
    if not rows:
        return pd.DataFrame()
    records = [row.get("original_data", row.get("data", {})) for row in rows]
    return pd.DataFrame(records)


def _detect_cols(df: pd.DataFrame) -> dict:
    """
    Detect which Excel column maps to which role.
    Normalises away spaces, hyphens, underscores and case for robust matching.
    """
    def _norm(s, strip_pct=True):
        t = str(s).lower()
        for ch in (' ', '-', '_'):
            t = t.replace(ch, '')
        if strip_pct:
            t = t.replace('%', '')
        return t

    # Two maps: one that strips %, one that keeps it (for pct vs amount disambiguation)
    col_map      = {_norm(c, True):  c for c in reversed(list(df.columns))}
    col_map_pct  = {_norm(c, False): c for c in df.columns}

    def find(*candidates):
        for c in candidates:
            k = _norm(c, True)
            if k in col_map:
                return col_map[k]
        return None

    def find_pct(*candidates):
        """Find percentage columns — keeps % in key so GP% != GP."""
        for c in candidates:
            k = _norm(c, False)
            if k in col_map_pct:
                return col_map_pct[k]
        return None

    def find_exact(name):
        """Exact case-insensitive column name match."""
        name_l = name.lower()
        for c in df.columns:
            if c.lower() == name_l:
                return c
        return None

    return {
        'sale':           find_exact('Sale') or find('Sales', 'NetSales', 'TotalSales'),
        'gp':             find_exact('GP') or find('GrossProfit', 'GrossProfitAmount'),
        'gp_pct':         find_exact('GP%') or find_pct('GP%', 'GPPercent', 'GrossProfit%', 'Margin%'),
        'city':           find('City'),
        'region':         find('Region', 'Area', 'Zone'),
        'business_type':  find('Business-Type', 'BusinessType', 'BizType'),
        'customer':       find('CustomerName', 'Customer', 'ClientName', 'Client'),
        'total_received': find('TotalReceived', 'TotalReceipt', 'AmountReceived'),
        'before_due':     find('Received_Before Due Date', 'Received_BeforeDueDate', 'ReceivedBeforeDueDate'),
        'before_due_pct': find('Received_BeforeDueDate%', 'ReceivedBeforeDueDatePercent'),
        'after_due':      find('Received_After Due Date', 'Received_AfterDueDate', 'ReceivedAfterDueDate'),
        'after_due_pct':  find('Received_AfterDueDate%', 'ReceivedAfterDueDatePercent'),
        'payment_status': find('PaymentStatus', 'Status'),
        'avg_days':       find('AvgPaymentDays', 'AveragePaymentDays', 'PaymentDays'),
        'credit_limit':   find('CreditLimit', 'Limit'),
        'credit_term':    find('CreditTerm', 'Terms', 'PaymentTerms'),
    }


def _num(df: pd.DataFrame, col: Optional[str]) -> pd.Series:
    """Return a numeric Series for a column, or zeros if column missing."""
    if col is None or col not in df.columns:
        return pd.Series([0.0] * len(df), index=df.index)
    return pd.to_numeric(df[col], errors="coerce").fillna(0)


def _group_sale_gp(df: pd.DataFrame, by_col: str,
                   sale_col: str, gp_col: str) -> pd.DataFrame:
    """
    Group by a dimension and compute SUM(Sale), SUM(GP), and GP%=SUM(GP)/SUM(Sale).
    Returns DataFrame sorted by Sale descending.
    """
    df2 = df[[by_col, sale_col, gp_col]].copy()
    df2[sale_col] = pd.to_numeric(df2[sale_col], errors="coerce").fillna(0)
    df2[gp_col]   = pd.to_numeric(df2[gp_col],   errors="coerce").fillna(0)
    agg = df2.groupby(by_col, as_index=False).agg(
        Sale=(sale_col, 'sum'),
        GP=(gp_col, 'sum'),
    )
    agg['GP_Pct'] = agg.apply(
        lambda r: r['GP'] / r['Sale'] * 100 if r['Sale'] > 0 else 0, axis=1
    )
    return agg.sort_values('Sale', ascending=False).reset_index(drop=True)

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
    """All monetary figures are expressed in millions (e.g. 1,200,000 → 1.20M)."""
    if val is None:
        return "N/A"
    if is_pct:
        return f"{val:.1f}%"
    # Always show in millions regardless of magnitude
    return f"{val / 1e6:.2f}M"


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
    color = "#C31D27",        # str OR list of str (one per bar)
    fmt_pct: bool = False,
    width_in: float = 6.5,
    height_in: float = 3.2,
    legend_items: List[Tuple[str, str]] = None,  # [(label, hex_color), ...]
) -> io.BytesIO:
    """Horizontal bar chart — returns PNG bytes. color may be a single hex or a list."""
    n = len(labels)
    fig, ax = plt.subplots(figsize=(width_in, max(height_in, n * 0.45 + 0.6)))
    fig.patch.set_facecolor("white")

    y = np.arange(n)
    bar_colors = color if isinstance(color, list) else [color] * n
    bars = ax.barh(y, values, color=bar_colors, height=0.6,
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
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x/1e6:.0f}M"))
    if legend_items:
        patches = [mpatches.Patch(color=c, label=l) for l, c in legend_items]
        ax.legend(handles=patches, loc="lower right", fontsize=8, frameon=False)
    plt.tight_layout(pad=0.6)
    return _chart_bytes(fig)


def _hbar_gp_chart(
    labels: List[str],
    values: List[float],
    title: str,
    avg: float,
    max_items: int = 12,
    width_in: float = 6.8,
) -> io.BytesIO:
    """
    Horizontal bar chart for GP% by category — capped at max_items rows so
    labels never crowd. Green = above average, red = below. Dashed avg line.
    """
    # Cap: show top half + bottom half if there are more than max_items
    if len(labels) > max_items:
        half = max_items // 2
        keep_idx = list(range(half)) + list(range(len(labels) - half, len(labels)))
        labels = [labels[i] for i in keep_idx]
        values = [values[i] for i in keep_idx]
        truncated = True
    else:
        truncated = False

    n = len(labels)
    height_in = max(3.2, n * 0.38)
    fig, ax = plt.subplots(figsize=(width_in, height_in))
    fig.patch.set_facecolor("white")

    y = np.arange(n)
    bar_colors = ["#059669" if v >= avg else "#C31D27" for v in values]
    bars = ax.barh(y, values, color=bar_colors, height=0.55,
                   edgecolor="none", zorder=3)

    # Value labels beside each bar
    max_v = max(values) if values else 100
    for bar, val in zip(bars, values):
        ax.text(val + max_v * 0.012, bar.get_y() + bar.get_height() / 2,
                f"{val:.1f}%", va="center", ha="left",
                fontsize=8, color="#374151", fontweight="600")

    # Average reference line
    ax.axvline(avg, color="#6b7280", linewidth=1.2, linestyle="--", zorder=4, alpha=0.85)
    ax.text(avg + max_v * 0.01, n - 0.4, f"Avg {avg:.1f}%",
            va="top", ha="left", fontsize=7.5,
            color="#6b7280", fontstyle="italic")

    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=9, color="#1a1f2e")
    ax.invert_yaxis()
    suffix = f" (top & bottom {max_items // 2} shown)" if truncated else ""
    ax.set_title(title + suffix, fontsize=11, fontweight="700",
                 color="#1a1f2e", pad=10, loc="left")
    ax.set_xlim(0, max_v * 1.22)
    ax.grid(axis="x", color="#f3f4f6", linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#e5e7eb")
    ax.spines["bottom"].set_color("#e5e7eb")
    ax.tick_params(axis="x", labelsize=8, colors="#9ca3af")
    ax.tick_params(axis="y", length=0)

    above_patch = mpatches.Patch(color="#059669", label="Above average")
    below_patch = mpatches.Patch(color="#C31D27", label="Below average")
    ax.legend(handles=[above_patch, below_patch], loc="lower right",
              fontsize=8, frameon=False)

    plt.tight_layout(pad=0.7)
    return _chart_bytes(fig)


def _pie_chart(
    labels: List[str],
    values: List[float],
    title: str,
    grand_total: Optional[float] = None,
    width_in: float = 4.0,
    height_in: float = 3.5,
) -> io.BytesIO:
    """
    grand_total: if provided, slice labels show % of grand_total (all products),
    not % of the slice sum. This keeps the chart honest when only top-N are shown.
    """
    fig, ax = plt.subplots(figsize=(width_in, height_in))
    fig.patch.set_facecolor("white")
    colors_list = CHART_PALETTE[:len(labels)]

    denom = grand_total if (grand_total and grand_total > 0) else sum(values)

    def _autopct(pct_of_slice_sum):
        # matplotlib passes % relative to the pie's own total; convert back to absolute
        actual = pct_of_slice_sum / 100.0 * sum(values)
        return f"{actual / denom * 100:.1f}%"

    wedges, texts, autotexts = ax.pie(
        values, labels=None, colors=colors_list,
        autopct=_autopct, pctdistance=0.82,
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

    def handle_pageBegin(self):
        """Draw cover background BEFORE any flowables are rendered on page 1."""
        super().handle_pageBegin()
        if self._page_num == 0:
            _draw_cover_background(self.canv, self)

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
        canvas.setFillColor(ICS_DARK)
        canvas.drawString(28, H - 24, "AI Financial Analyzer")
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
            f"Report generated by AI Financial Analyzer  ·  {self._filename}  ·  "
            f"{datetime.datetime.now().strftime('%d %b %Y')}",
        )
        canvas.restoreState()
        super().handle_pageEnd()


# ─────────────────────────────────────────────────────────────────────────────
# SECTION BUILDERS
# ─────────────────────────────────────────────────────────────────────────────

def _draw_cover_background(canvas, doc):
    """
    Canvas callback — drawn BEFORE flowables so everything sits on top.
    Adds a professional dark header band, red accent strip, and footer bar.
    """
    canvas.saveState()

    # ── Dark header band ──────────────────────────────────────────────────────
    canvas.setFillColor(ICS_DARK)
    canvas.rect(0, H - 110, W, 110, fill=1, stroke=0)

    # Red accent strip at the bottom of the dark band
    canvas.setFillColor(ICS_RED)
    canvas.rect(0, H - 116, W, 6, fill=1, stroke=0)

    # Wordmark inside the band
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 16)
    canvas.drawCentredString(W / 2, H - 68, "AI Financial Analyzer")
    canvas.setFillColor(colors.HexColor("#6b7280"))
    canvas.setFont("Helvetica", 8.5)
    canvas.drawCentredString(W / 2, H - 100, "Intelligent. Accurate. Instant.")

    # ── Light footer bar ──────────────────────────────────────────────────────
    canvas.setFillColor(colors.HexColor("#f8f9fa"))
    canvas.rect(0, 0, W, 36, fill=1, stroke=0)
    canvas.setStrokeColor(colors.HexColor("#e5e7eb"))
    canvas.setLineWidth(0.5)
    canvas.line(0, 36, W, 36)
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(colors.HexColor("#9ca3af"))
    canvas.drawCentredString(
        W / 2, 14,
        f"Confidential  ·  Generated by AI Financial Analyzer  ·  "
        f"{datetime.datetime.now().strftime('%d %B %Y')}",
    )

    canvas.restoreState()


def _cover_page(filename: str, parsed: dict, S: dict) -> list:
    sheet_count = len(parsed.get("sheets", []))
    total_rows  = sum(len(s.get("rows", [])) for s in parsed.get("sheets", []))
    date_str    = datetime.datetime.now().strftime("%d %B %Y")

    story = []

    # Push content below the 116 pt dark header band
    story.append(Spacer(1, 130))

    # ── Title block ───────────────────────────────────────────────────────────
    story.append(Paragraph(
        "Financial Analytics Report",
        ParagraphStyle(
            "cover_h1", fontSize=27, fontName="Helvetica-Bold",
            textColor=ICS_DARK, alignment=TA_CENTER,
            spaceAfter=6, letterSpacing=-0.5,
        ),
    ))

    story.append(Spacer(1, 20))

    # ── File info ─────────────────────────────────────────────────────────────
    story.append(Paragraph(
        f"<b>{filename}</b>",
        ParagraphStyle(
            "fc", fontSize=12, fontName="Helvetica-Bold",
            textColor=ICS_RED, alignment=TA_CENTER, spaceAfter=6,
        ),
    ))
    story.append(Paragraph(
        f"{sheet_count} sheet(s)  ·  {total_rows:,} rows  ·  Generated {date_str}",
        ParagraphStyle(
            "cover_meta", fontSize=9.5, fontName="Helvetica",
            textColor=ICS_GREY, alignment=TA_CENTER, spaceAfter=0,
        ),
    ))
    story.append(Spacer(1, 32))

    # ── Description blurb ─────────────────────────────────────────────────────
    story.append(Paragraph(
        "This report was automatically generated by the AI Financial Analyzer. "
        "All metrics are computed deterministically from your uploaded data — "
        "no estimates, no hallucinations.",
        ParagraphStyle(
            "cc", fontSize=9.5, fontName="Helvetica", textColor=ICS_GREY,
            alignment=TA_CENTER, leading=16, spaceAfter=0,
        ),
    ))
    story.append(Spacer(1, 36))

    # ── Table of contents ─────────────────────────────────────────────────────
    contents = [
        ("1", "KPI Summary"),
        ("2", "Sales Breakdown"),
        ("3", "Top & Bottom Customers by Sales"),
        ("4", "Top & Bottom Customers by GP"),
        ("5", "Profitability Analysis"),
        ("6", "Receivables & Collections"),
        ("7", "Customer Detail"),
        ("8", "AI Insights"),
    ]
    tbl_data = []
    for num, label in contents:
        tbl_data.append([
            Paragraph(f"<b>{num}</b>", ParagraphStyle(
                f"cn{num}", fontSize=10, fontName="Helvetica-Bold",
                textColor=ICS_RED, alignment=TA_CENTER,
            )),
            Paragraph(label, ParagraphStyle(
                f"cl{num}", fontSize=10, fontName="Helvetica",
                textColor=ICS_DARK,
            )),
        ])
    tbl = Table(tbl_data, colWidths=[32, 200])
    tbl.setStyle(TableStyle([
        ("TOPPADDING",    (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING",   (0, 0), (-1, -1), 12),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 12),
        ("LINEBELOW",     (0, 0), (-1, -2), 0.4, colors.HexColor("#e5e7eb")),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND",    (0, 0), (-1, -1), colors.HexColor("#fafafa")),
        ("BOX",           (0, 0), (-1, -1), 0.4, colors.HexColor("#e5e7eb")),
    ]))
    story.append(_centred(tbl))

    story.append(PageBreak())
    return story


def _kpi_section(parsed: dict, S: dict) -> list:
    """Page 2 — KPI Summary cards computed directly from master data."""
    story = []
    story.append(Paragraph("KPI Summary", S["h2"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#e5e7eb"),
                             spaceBefore=2, spaceAfter=10))

    df   = _get_raw_df(parsed)
    cols = _detect_cols(df)

    sale_s    = _num(df, cols['sale'])
    gp_s      = _num(df, cols['gp'])
    recv_s    = _num(df, cols['total_received'])
    days_s    = _num(df, cols['avg_days'])

    total_sale   = sale_s.sum()
    total_gp     = gp_s.sum()
    gp_pct       = (total_gp / total_sale * 100) if total_sale > 0 else 0
    total_recv   = recv_s.sum()
    coll_rate    = (total_recv / total_sale * 100) if total_sale > 0 else 0
    avg_days_val = days_s.mean() if len(days_s) > 0 else 0
    total_rows   = len(df)

    # fmt_type: "money" → millions, "pct" → 1 decimal %, "count" → integer, "days" → X days
    kpis = [
        ("Total Sales",        total_sale,   "money",  "#C31D27"),
        ("Gross Profit",       total_gp,     "money",  "#059669"),
        ("GP Margin",          gp_pct,       "pct",    "#2563eb"),
        ("Total Received",     total_recv,   "money",  "#7c3aed"),
        ("Collection Rate",    coll_rate,    "pct",    "#ea580c"),
        ("Avg Payment Days",   avg_days_val, "days",   "#374151"),
    ]

    def _fmt_kpi(val: Optional[float], fmt_type: str) -> str:
        if val is None:
            return "N/A"
        if fmt_type == "pct":
            return f"{val:.1f}%"
        if fmt_type == "count":
            return f"{int(val):,}"
        if fmt_type == "days":
            return f"{val:.1f}d"
        return _fmt(val)

    def _kpi_cell(label: str, val: Optional[float], fmt_type: str, accent: str):
        val_str = _fmt_kpi(val, fmt_type)
        return Table(
            [[Paragraph(val_str, ParagraphStyle(
                "kv", fontSize=18, fontName="Helvetica-Bold",
                textColor=colors.HexColor(accent), alignment=TA_CENTER,
            ))],
             [Paragraph(label, S["kpi_label"])]],
            colWidths=[100],
        )

    cards = [_kpi_cell(l, v, t, a) for l, v, t, a in kpis]

    # Layout: 3 + 3
    for row in [cards[:3], cards[3:]]:
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

    story.append(PageBreak())
    return story


def _make_customer_table(df_rows, col_widths, hdr_color="#1a1f2e", status_col_idx=None, font_size=7.5):
    STATUS_COLOR = {"on time":"#059669","delayed":"#ea580c","partial":"#2563eb","overdue":"#C31D27"}
    tbl = Table(df_rows, colWidths=col_widths, repeatRows=1)
    cmds = [
        ("BACKGROUND",     (0,0),(-1,0), colors.HexColor(hdr_color)),
        ("TEXTCOLOR",      (0,0),(-1,0), colors.white),
        ("FONTNAME",       (0,0),(-1,0), "Helvetica-Bold"),
        ("FONTSIZE",       (0,0),(-1,-1), font_size),
        ("ROWBACKGROUNDS", (0,1),(-1,-1), [colors.white, colors.HexColor("#f9fafb")]),
        ("GRID",           (0,0),(-1,-1), 0.4, colors.HexColor("#e5e7eb")),
        ("TOPPADDING",     (0,0),(-1,-1), 3),
        ("BOTTOMPADDING",  (0,0),(-1,-1), 3),
        ("LEFTPADDING",    (0,0),(-1,-1), 5),
        ("RIGHTPADDING",   (0,0),(-1,-1), 5),
        ("FONTNAME",       (0,1),(-1,-1), "Helvetica"),
        ("VALIGN",         (0,0),(-1,-1), "MIDDLE"),
    ]
    if status_col_idx is not None:
        for ri, row in enumerate(df_rows[1:], 1):
            c = STATUS_COLOR.get(str(row[status_col_idx]).lower(), "#374151")
            cmds += [("TEXTCOLOR",(status_col_idx,ri),(status_col_idx,ri),colors.HexColor(c)),
                     ("FONTNAME", (status_col_idx,ri),(status_col_idx,ri),"Helvetica-Bold")]
    tbl.setStyle(TableStyle(cmds))
    return tbl


# ── CHART HELPERS (new / updated) ────────────────────────────────────────────

def _hbar_value_chart(labels, values, title, color="#2563eb",
                      width_in=6.8, height_in=None, fmt_fn=None):
    """Clean horizontal bar chart — shows actual M-values on bars."""
    n = len(labels)
    hi = height_in or max(3.0, n * 0.48 + 0.6)
    fig, ax = plt.subplots(figsize=(width_in, hi))
    fig.patch.set_facecolor("white")
    y = np.arange(n)
    bar_colors = color if isinstance(color, list) else [color]*n
    bars = ax.barh(y, values, color=bar_colors, height=0.58, edgecolor="none", zorder=3)
    max_v = max(abs(v) for v in values) if values else 1
    for bar, val in zip(bars, values):
        lbl = fmt_fn(val) if fmt_fn else f"{val/1e6:.2f}M"
        ax.text(bar.get_width() + max_v*0.012, bar.get_y()+bar.get_height()/2,
                lbl, va="center", ha="left", fontsize=8.5, color="#1a1f2e", fontweight="600")
    ax.set_yticks(y); ax.set_yticklabels(labels, fontsize=9.5, color="#1a1f2e")
    ax.invert_yaxis()
    ax.set_title(title, fontsize=11, fontweight="700", color="#1a1f2e", pad=10, loc="left")
    ax.set_xlim(0, max_v*1.28)
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x,_: f"{x/1e6:.1f}M"))
    ax.grid(axis="x", color="#f3f4f6", linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    for sp in ["top","right"]: ax.spines[sp].set_visible(False)
    ax.spines["left"].set_color("#e5e7eb"); ax.spines["bottom"].set_color("#e5e7eb")
    ax.tick_params(axis="x", labelsize=8, colors="#9ca3af")
    ax.tick_params(axis="y", length=0)
    plt.tight_layout(pad=0.6)
    return _chart_bytes(fig)


def _hbar_gp_dual_chart(labels, pct_values, gp_values, title, avg_pct, width_in=6.8):
    """GP% horizontal bars — label shows 'XX.X%  |  X.XXM' for each bar."""
    n = len(labels)
    hi = max(3.2, n*0.46 + 0.6)
    fig, ax = plt.subplots(figsize=(width_in, hi))
    fig.patch.set_facecolor("white")
    y = np.arange(n)
    bar_colors = ["#059669" if v >= avg_pct else "#C31D27" for v in pct_values]
    bars = ax.barh(y, pct_values, color=bar_colors, height=0.55, edgecolor="none", zorder=3)
    max_v = max(pct_values) if pct_values else 100
    for bar, pct, gp in zip(bars, pct_values, gp_values):
        lbl = f"{pct:.1f}%  |  {gp/1e6:.2f}M"
        ax.text(bar.get_width()+max_v*0.013, bar.get_y()+bar.get_height()/2,
                lbl, va="center", ha="left", fontsize=8.2, color="#374151", fontweight="600")
    ax.axvline(avg_pct, color="#6b7280", linewidth=1.2, linestyle="--", zorder=4, alpha=0.85)
    ax.text(avg_pct+max_v*0.01, n-0.4, f"Avg {avg_pct:.1f}%",
            va="top", ha="left", fontsize=7.5, color="#6b7280", fontstyle="italic")
    ax.set_yticks(y); ax.set_yticklabels(labels, fontsize=9.5, color="#1a1f2e")
    ax.invert_yaxis()
    ax.set_title(title, fontsize=11, fontweight="700", color="#1a1f2e", pad=10, loc="left")
    ax.set_xlim(0, max_v*1.45)
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x,_: f"{x:.0f}%"))
    ax.grid(axis="x", color="#f3f4f6", linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    for sp in ["top","right"]: ax.spines[sp].set_visible(False)
    ax.spines["left"].set_color("#e5e7eb"); ax.spines["bottom"].set_color("#e5e7eb")
    ax.tick_params(axis="x", labelsize=8, colors="#9ca3af")
    ax.tick_params(axis="y", length=0)
    ap = mpatches.Patch(color="#059669", label="Above average")
    bp = mpatches.Patch(color="#C31D27", label="Below average")
    ax.legend(handles=[ap,bp], loc="lower right", fontsize=8, frameon=False)
    plt.tight_layout(pad=0.7)
    return _chart_bytes(fig)


def _stacked_hbar_chart(labels, vals_a, vals_b, title, lab_a, lab_b,
                        col_a="#059669", col_b="#C31D27", width_in=6.8):
    """Stacked horizontal bar — used for before/after due date all customers."""
    n = len(labels)
    hi = max(5.0, n*0.27+0.8)
    fig, ax = plt.subplots(figsize=(width_in, hi))
    fig.patch.set_facecolor("white")
    y = np.arange(n)
    ax.barh(y, vals_a, color=col_a, height=0.6, edgecolor="none", zorder=3, label=lab_a)
    ax.barh(y, vals_b, left=vals_a, color=col_b, height=0.6, edgecolor="none", zorder=3, label=lab_b)
    totals = [a+b for a,b in zip(vals_a, vals_b)]
    max_t  = max(totals) if totals else 1
    for i,(a,b,t) in enumerate(zip(vals_a, vals_b, totals)):
        if t > 0:
            ax.text(t+max_t*0.01, i, f"{t/1e6:.2f}M", va="center", ha="left",
                    fontsize=6.5, color="#374151", fontweight="500")
    ax.set_yticks(y); ax.set_yticklabels(labels, fontsize=6.8, color="#1a1f2e")
    ax.invert_yaxis()
    ax.set_title(title, fontsize=11, fontweight="700", color="#1a1f2e", pad=10, loc="left")
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x,_: f"{x/1e6:.1f}M"))
    ax.grid(axis="x", color="#f3f4f6", linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    for sp in ["top","right"]: ax.spines[sp].set_visible(False)
    ax.spines["left"].set_color("#e5e7eb"); ax.spines["bottom"].set_color("#e5e7eb")
    ax.tick_params(axis="x", labelsize=8, colors="#9ca3af")
    ax.tick_params(axis="y", length=0)
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, -0.12),
              ncol=2, fontsize=8, frameon=True,
              facecolor="white", edgecolor="#e5e7eb", framealpha=0.9)
    ax.set_xlim(0, max_t*1.14)
    plt.tight_layout(pad=0.6)
    return _chart_bytes(fig)


# ── MAIN SECTIONS ─────────────────────────────────────────────────────────────

def _sales_breakdown(parsed: dict, S: dict) -> list:
    """Sales by City, Region, Business Type — accurate charts with actual figures."""
    story = []
    story.append(Paragraph("Sales Breakdown", S["h2"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#e5e7eb"),
                             spaceBefore=2, spaceAfter=6))
    df   = _get_raw_df(parsed)
    cols = _detect_cols(df)
    sale_col = cols['sale']; gp_col = cols['gp']
    if sale_col is None:
        story.append(Paragraph("Sales column not detected.", S["body"]))
        story.append(PageBreak()); return story
    total_sale = _num(df, sale_col).sum()
    story.append(Paragraph(
        f"Total sales of <b>{total_sale/1e6:.2f}M</b> distributed across cities, "
        f"regions, and business types. Charts show actual sales figures.",
        ParagraphStyle("sec_intro", parent=S["body"], textColor=colors.HexColor("#6b7280"),
                       fontSize=9, spaceAfter=10)))

    for dim, col_key, title, color in [
        ("City",          'city',          "Sales by City",          "#2563eb"),
        ("Region",        'region',        "Sales by Region",        "#7c3aed"),
        ("Business Type", 'business_type', "Sales by Business Type", "#ea580c"),
    ]:
        dcol = cols.get(col_key)
        if not dcol: continue
        agg = _group_sale_gp(df, dcol, sale_col, gp_col or sale_col)
        labels = list(agg[dcol]); values = list(agg['Sale'])
        buf = _hbar_value_chart(labels, values, title, color=color,
                                height_in=max(3.0, len(labels)*0.52))
        top3_v = sum(values[:3])
        desc = (f"<b>{labels[0]}</b> leads with <b>{values[0]/1e6:.2f}M</b> "
                f"({values[0]/total_sale*100:.1f}% of total). "
                f"Top 3 account for {top3_v/total_sale*100:.1f}%. "
                f"<b>{labels[-1]}</b> is lowest at <b>{values[-1]/1e6:.2f}M</b> "
                f"({values[-1]/total_sale*100:.1f}%).")
        story.append(KeepTogether([_img_flowable(buf, W-80), _chart_desc(desc, S)]))
        story.append(Spacer(1, 10))

    story.append(PageBreak()); return story


def _top_bottom_customers_sales(parsed: dict, S: dict) -> list:
    """Top & Bottom 10 customers by Sales — bar charts."""
    story = []
    story.append(Paragraph("Top & Bottom 10 Customers by Sales", S["h2"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#e5e7eb"),
                             spaceBefore=2, spaceAfter=10))
    df   = _get_raw_df(parsed)
    cols = _detect_cols(df)
    sale_col = cols['sale']; cust_col = cols['customer']
    if not sale_col or not cust_col:
        story.append(PageBreak()); return story
    df2 = df.copy()
    df2['_sale'] = pd.to_numeric(df2[sale_col], errors='coerce').fillna(0)
    df2['_gp']   = pd.to_numeric(df2[cols['gp']], errors='coerce').fillna(0) if cols['gp'] else 0
    total_sale   = df2['_sale'].sum()

    def _sale_block(df_sub, lbl, col, asc):
        s2 = df_sub.sort_values('_sale', ascending=asc).reset_index(drop=True)
        nm = [str(r[cust_col]) for _, r in s2.iterrows()]
        vv = list(s2['_sale'])
        b  = _hbar_value_chart(nm, vv, f"{lbl} Customers by Sales", color=col,
                               height_in=max(3.8, 10*0.52), width_in=7.2)
        _ps = ParagraphStyle("sb",fontSize=7.2,fontName="Helvetica",
                             textColor=colors.HexColor("#1f2937"),leading=9)
        _ph = ParagraphStyle("sh",fontSize=7.2,fontName="Helvetica-Bold",
                             textColor=colors.white,alignment=TA_CENTER,leading=9)
        _pn = ParagraphStyle("sn",fontSize=7.2,fontName="Helvetica",
                             textColor=colors.HexColor("#1f2937"),alignment=TA_CENTER,leading=9)
        rt = [[Paragraph(h,_ph) for h in ["#","Customer","City","Sale","GP","GP%"]]]
        for rk, (_, row) in enumerate(s2.iterrows(), 1):
            sv=row['_sale']; gv=row['_gp']
            cy = str(row[cols['city']]) if cols['city'] and pd.notna(row.get(cols['city'])) else "-"
            rt.append([Paragraph(str(rk),_pn), Paragraph(str(row[cust_col]),_ps),
                       Paragraph(cy,_ps), Paragraph(f"{sv/1e6:.2f}M",_pn),
                       Paragraph(f"{gv/1e6:.2f}M",_pn),
                       Paragraph(f"{gv/sv*100:.1f}%" if sv>0 else "-",_pn)])
        t = Table(rt, colWidths=[20,172,58,48,48,40], repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(-1,0),colors.HexColor(col)),
            ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,colors.HexColor("#f9fafb")]),
            ("GRID",(0,0),(-1,-1),0.4,colors.HexColor("#e5e7eb")),
            ("TOPPADDING",(0,0),(-1,-1),3),("BOTTOMPADDING",(0,0),(-1,-1),3),
            ("LEFTPADDING",(0,0),(-1,-1),4),("RIGHTPADDING",(0,0),(-1,-1),4),
            ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ]))
        return [Paragraph(f"{lbl} Customers by Sales", S["h3"]),
                Spacer(1,4), _img_flowable(b, W-60), Spacer(1,4), t, Spacer(1,8)]

    story.extend(_sale_block(df2.nlargest(10,'_sale'),  "Top 10",    "#2563eb", False))
    story.append(PageBreak())
    story.extend(_sale_block(df2.nsmallest(10,'_sale'), "Bottom 10", "#C31D27", True))
    story.append(PageBreak()); return story


def _top_bottom_customers_gp(parsed: dict, S: dict) -> list:
    """Top & Bottom 10 customers by Gross Profit — bar charts."""
    story = []
    story.append(Paragraph("Top & Bottom 10 Customers by Gross Profit", S["h2"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#e5e7eb"),
                             spaceBefore=2, spaceAfter=10))
    df   = _get_raw_df(parsed)
    cols = _detect_cols(df)
    gp_col = cols['gp']; cust_col = cols['customer']
    if not gp_col or not cust_col:
        story.append(PageBreak()); return story
    df2 = df.copy()
    df2['_sale'] = pd.to_numeric(df2[cols['sale']], errors='coerce').fillna(0) if cols['sale'] else 0
    df2['_gp']   = pd.to_numeric(df2[gp_col], errors='coerce').fillna(0)

    def _gp_block(df_sub, lbl, col, asc):
        s2 = df_sub.sort_values('_gp', ascending=asc).reset_index(drop=True)
        nm = [str(r[cust_col]) for _, r in s2.iterrows()]
        vv = list(s2['_gp'])
        b  = _hbar_value_chart(nm, vv, f"{lbl} Customers by GP", color=col,
                               height_in=max(3.8, 10*0.52), width_in=7.2)
        _ps = ParagraphStyle("gb",fontSize=7.2,fontName="Helvetica",
                             textColor=colors.HexColor("#1f2937"),leading=9)
        _ph = ParagraphStyle("gh",fontSize=7.2,fontName="Helvetica-Bold",
                             textColor=colors.white,alignment=TA_CENTER,leading=9)
        _pn = ParagraphStyle("gn",fontSize=7.2,fontName="Helvetica",
                             textColor=colors.HexColor("#1f2937"),alignment=TA_CENTER,leading=9)
        rt = [[Paragraph(h,_ph) for h in ["#","Customer","City","Sale","GP","GP%"]]]
        for rk, (_, row) in enumerate(s2.iterrows(), 1):
            sv=row['_sale']; gv=row['_gp']
            cy = str(row[cols['city']]) if cols['city'] and pd.notna(row.get(cols['city'])) else "-"
            rt.append([Paragraph(str(rk),_pn), Paragraph(str(row[cust_col]),_ps),
                       Paragraph(cy,_ps), Paragraph(f"{sv/1e6:.2f}M",_pn),
                       Paragraph(f"{gv/1e6:.2f}M",_pn),
                       Paragraph(f"{gv/sv*100:.1f}%" if sv>0 else "-",_pn)])
        t = Table(rt, colWidths=[20,172,58,48,48,40], repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(-1,0),colors.HexColor(col)),
            ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,colors.HexColor("#f9fafb")]),
            ("GRID",(0,0),(-1,-1),0.4,colors.HexColor("#e5e7eb")),
            ("TOPPADDING",(0,0),(-1,-1),3),("BOTTOMPADDING",(0,0),(-1,-1),3),
            ("LEFTPADDING",(0,0),(-1,-1),4),("RIGHTPADDING",(0,0),(-1,-1),4),
            ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ]))
        return [Paragraph(f"{lbl} Customers by GP", S["h3"]),
                Spacer(1,4), _img_flowable(b, W-60), Spacer(1,4), t, Spacer(1,8)]

    story.extend(_gp_block(df2.nlargest(10,'_gp'),  "Top 10",    "#059669", False))
    story.append(PageBreak())
    story.extend(_gp_block(df2.nsmallest(10,'_gp'), "Bottom 10", "#ea580c", True))
    story.append(PageBreak()); return story


def _profitability(parsed: dict, S: dict) -> list:
    """GP% by City, Region, Business Type — dual-label charts + data tables."""
    story = []
    story.append(Paragraph("Profitability Analysis", S["h2"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#e5e7eb"),
                             spaceBefore=2, spaceAfter=10))
    df   = _get_raw_df(parsed)
    cols = _detect_cols(df)
    sale_col = cols['sale']; gp_col = cols['gp']
    if not sale_col or not gp_col:
        story.append(PageBreak()); return story
    total_gp   = _num(df, gp_col).sum()
    total_sale = _num(df, sale_col).sum()
    avg_pct    = (total_gp/total_sale*100) if total_sale > 0 else 0

    for col_key, title in [('city','GP% by City'),('region','GP% by Region'),
                            ('business_type','GP% by Business Type')]:
        dcol = cols.get(col_key)
        if not dcol: continue
        agg   = _group_sale_gp(df, dcol, sale_col, gp_col)
        agg_s = agg.sort_values('GP_Pct', ascending=False).reset_index(drop=True)
        labels   = list(agg_s[dcol])
        pct_vals = list(agg_s['GP_Pct'])
        gp_vals  = list(agg_s['GP'])
        sale_vals= list(agg_s['Sale'])
        above    = sum(1 for v in pct_vals if v >= avg_pct)
        buf = _hbar_gp_dual_chart(labels, pct_vals, gp_vals, title, avg_pct)
        desc = (f"<b>{labels[0]}</b> achieves the highest GP margin at "
                f"<b>{pct_vals[0]:.1f}%</b> (GP: <b>{gp_vals[0]/1e6:.2f}M</b>), "
                f"while <b>{labels[-1]}</b> has the lowest at "
                f"<b>{pct_vals[-1]:.1f}%</b> (GP: <b>{gp_vals[-1]/1e6:.2f}M</b>). "
                f"Overall average: <b>{avg_pct:.1f}%</b>. "
                f"{above} of {len(labels)} perform above average.")
        # Summary table
        tbl_rows = [["", "GP Amount", "GP%", "Sales"]]
        for lbl, gp, pct, sale in zip(labels, gp_vals, pct_vals, sale_vals):
            tbl_rows.append([lbl, f"{gp/1e6:.2f}M", f"{pct:.1f}%", f"{sale/1e6:.2f}M"])
        tbl = Table(tbl_rows, colWidths=[90,72,55,72])
        tbl.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#1a1f2e")),
            ("TEXTCOLOR",(0,0),(-1,0),colors.white),
            ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
            ("FONTSIZE",(0,0),(-1,-1),8),
            ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,colors.HexColor("#f9fafb")]),
            ("GRID",(0,0),(-1,-1),0.4,colors.HexColor("#e5e7eb")),
            ("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4),
            ("LEFTPADDING",(0,0),(-1,-1),6),
            ("ALIGN",(1,1),(-1,-1),"RIGHT"),
            ("FONTNAME",(0,1),(-1,-1),"Helvetica"),
        ]))
        story.append(_img_flowable(buf, W-80))
        story.append(KeepTogether([_chart_desc(desc, S), Spacer(1,4), _centred(tbl), Spacer(1,14)]))

    story.append(PageBreak()); return story


def _receivables_section(parsed: dict, S: dict) -> list:
    """Receivables — per-status customer charts, before/after due stacked chart,
       top 10 by avg payment days chart."""
    story = []
    story.append(Paragraph("Receivables & Collections", S["h2"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#e5e7eb"),
                             spaceBefore=2, spaceAfter=10))
    df   = _get_raw_df(parsed)
    cols = _detect_cols(df)
    sale_col   = cols['sale'];  gp_col    = cols['gp']
    recv_col   = cols['total_received']
    before_col = cols['before_due'];  after_col = cols['after_due']
    status_col = cols['payment_status']
    days_col   = cols['avg_days']
    cust_col   = cols['customer'];    city_col  = cols['city']
    STATUS_COLORS = {"on time":"#059669","delayed":"#ea580c","partial":"#2563eb","overdue":"#C31D27"}
    total_recv = _num(df, recv_col).sum() if recv_col else 0

    # ── Overview bar (count by status) ───────────────────────────────────────
    if status_col:
        vc = df[status_col].value_counts()
        buf = _hbar_value_chart(
            list(vc.index), list(vc.values.astype(float)),
            "Customer Count by Payment Status",
            color=[STATUS_COLORS.get(l.lower(),"#6b7280") for l in vc.index],
            fmt_fn=lambda v: str(int(v)))
        at_risk = sum(v for l,v in zip(vc.index, vc.values) if l.lower() in ("overdue","delayed"))
        desc = (f"Out of {int(vc.sum())} customers, <b>{int(at_risk)}</b> are overdue or delayed "
                f"({at_risk/vc.sum()*100:.1f}%). On-time payers: <b>{int(vc.get('On Time',0))}</b>.")
        story.append(KeepTogether([_img_flowable(buf, W-80), _chart_desc(desc, S)]))
        story.append(Spacer(1, 10))

        # Per-status top-10 amount charts
        df2 = df.copy()
        df2['_sale'] = _num(df2, sale_col)
        df2['_gp']   = _num(df2, gp_col)
        for status in ["Overdue","Delayed","Partial","On Time"]:
            grp = df2[df2[status_col].str.strip() == status]
            if grp.empty: continue
            grp_top = grp.nlargest(10, '_sale').sort_values('_sale', ascending=True).reset_index(drop=True)
            names  = [str(r[cust_col]) if cust_col else f"Cust {i+1}"
                      for i, (_, r) in enumerate(grp_top.iterrows())]
            values = list(grp_top['_sale'])
            hx = STATUS_COLORS.get(status.lower(), "#374151")
            buf = _hbar_value_chart(names, values,
                                    f"Top 10 {status} Customers — Sale Amount",
                                    color=hx, height_in=max(2.8, 10*0.33))
            story.append(_img_flowable(buf, W-80))
            story.append(Spacer(1, 6))

    story.append(PageBreak())

    # ── Before vs After Due Date — Top 10 each ─────────────────────────────
    story.append(Paragraph("Collections: Before vs After Due Date", S["h3"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#e5e7eb"),
                             spaceBefore=2, spaceAfter=8))
    if before_col and after_col:
        df2 = df.copy()
        df2['_before'] = _num(df2, before_col)
        df2['_after']  = _num(df2, after_col)
        total_b = df2['_before'].sum(); total_a = df2['_after'].sum()
        total_recv_all = total_b + total_a
        desc = (f"Total collected <b>before</b> due date: <b>{total_b/1e6:.2f}M</b> "
                f"({total_b/total_recv_all*100:.1f}%). "
                f"Total collected <b>after</b> due date: <b>{total_a/1e6:.2f}M</b> "
                f"({total_a/total_recv_all*100:.1f}%).")
        story.append(_chart_desc(desc, S))
        story.append(Spacer(1, 8))

        # Top 10 by Before Due Date
        top_before = df2.nlargest(10, '_before').sort_values('_before', ascending=True).reset_index(drop=True)
        names_b = [str(r[cust_col]) if cust_col else f"C{i+1}" for i,(_, r) in enumerate(top_before.iterrows())]
        buf_b = _stacked_hbar_chart(
            names_b, list(top_before['_before']), list(top_before['_after']),
            "Top 10 Customers — Received Before Due Date",
            "Before Due Date", "After Due Date",
            col_a="#059669", col_b="#ea580c")
        story.append(_img_flowable(buf_b, W-80, max_height=320))
        story.append(Spacer(1, 12))

        # Top 10 by After Due Date
        top_after = df2.nlargest(10, '_after').sort_values('_after', ascending=True).reset_index(drop=True)
        names_a = [str(r[cust_col]) if cust_col else f"C{i+1}" for i,(_, r) in enumerate(top_after.iterrows())]
        buf_a = _stacked_hbar_chart(
            names_a, list(top_after['_before']), list(top_after['_after']),
            "Top 10 Customers — Received After Due Date",
            "Before Due Date", "After Due Date",
            col_a="#059669", col_b="#ea580c")
        story.append(_img_flowable(buf_a, W-80, max_height=320))
        story.append(Spacer(1, 16))

    # ── Top 10 customers by Avg Payment Days — chart ─────────────────────────
    if days_col and cust_col:
        story.append(Spacer(1, 8))
        df2 = df.copy()
        df2['_days'] = _num(df2, days_col)
        slow = df2.nlargest(10, '_days').sort_values('_days', ascending=True).reset_index(drop=True)
        names  = [str(r[cust_col]) for _, r in slow.iterrows()]
        values = list(slow['_days'])
        overall_avg = float(df2['_days'].mean())
        # Custom chart with avg line
        fig, ax = plt.subplots(figsize=(6.8, max(3.5, 10*0.48)))
        fig.patch.set_facecolor("white")
        y = np.arange(len(names))
        ax.barh(y, values, color="#7c3aed", height=0.55, edgecolor="none", zorder=3)
        for i, v in enumerate(values):
            ax.text(v+overall_avg*0.01, i, f"{v:.0f}d", va="center", ha="left",
                    fontsize=8.5, color="#1a1f2e", fontweight="600")
        ax.axvline(overall_avg, color="#6b7280", linewidth=1.2, linestyle="--", zorder=4)
        ax.text(overall_avg+overall_avg*0.01, len(names)-0.5,
                f"Avg {overall_avg:.1f}d", va="top", ha="left", fontsize=7.5,
                color="#6b7280", fontstyle="italic")
        ax.set_yticks(y); ax.set_yticklabels(names, fontsize=9, color="#1a1f2e")
        ax.invert_yaxis()
        ax.set_title("Top 10 Slowest Payers — Avg Payment Days", fontsize=11,
                     fontweight="700", color="#1a1f2e", pad=10, loc="left")
        ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x,_: f"{x:.0f}d"))
        ax.grid(axis="x", color="#f3f4f6", linewidth=0.8, zorder=0)
        ax.set_axisbelow(True)
        for sp in ["top","right"]: ax.spines[sp].set_visible(False)
        ax.spines["left"].set_color("#e5e7eb"); ax.spines["bottom"].set_color("#e5e7eb")
        ax.tick_params(axis="x", labelsize=8, colors="#9ca3af")
        ax.tick_params(axis="y", length=0)
        plt.tight_layout(pad=0.6)
        buf = _chart_bytes(fig)
        desc = (f"<b>{slow.iloc[-1][cust_col]}</b> takes the longest at "
                f"<b>{slow.iloc[-1]['_days']:.0f} days</b>. "
                f"Overall average: <b>{overall_avg:.1f} days</b>.")
        story.append(KeepTogether([_img_flowable(buf, W-80), _chart_desc(desc, S)]))

    story.append(PageBreak()); return story


def _customer_detail_section(parsed: dict, S: dict) -> list:
    """Customer Detail — Top 10 by Sales with full columns."""
    story = []
    story.append(Paragraph("Customer Detail", S["h2"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#e5e7eb"),
                             spaceBefore=2, spaceAfter=10))
    df   = _get_raw_df(parsed)
    cols = _detect_cols(df)
    sale_col  = cols['sale'];  gp_col    = cols['gp']
    cust_col  = cols['customer']; city_col = cols['city']
    status_col= cols['payment_status']; days_col = cols['avg_days']
    term_col  = cols['credit_term'];  limit_col = cols['credit_limit']
    if not sale_col or not cust_col:
        story.append(PageBreak()); return story

    df2 = df.copy()
    df2['_sale'] = _num(df2, sale_col); df2['_gp'] = _num(df2, gp_col)
    if days_col: df2['_days'] = _num(df2, days_col)
    total_sale = df2['_sale'].sum()
    top_df = df2.nlargest(10, '_sale').reset_index(drop=True)

    story.append(Paragraph("Top 10 Customers by Sales", S["h3"]))

    # Column widths — total must fit within page margins (~436 pts)
    # Use Paragraph for wrapping cells so long names never get cut off
    COL_W = [22, 106, 44, 52, 38, 36, 34, 30, 44, 30]  # sum = 436
    STATUS_COLOR = {"on time":"#059669","delayed":"#ea580c","partial":"#2563eb","overdue":"#C31D27"}

    def _cell(text, bold=False, align="LEFT", color="#1f2937", size=7):
        st = ParagraphStyle(
            "tc", fontSize=size, fontName="Helvetica-Bold" if bold else "Helvetica",
            textColor=colors.HexColor(color), alignment=TA_CENTER if align=="CENTER" else TA_LEFT,
            leading=9, spaceAfter=0, spaceBefore=0,
        )
        return Paragraph(str(text), st)

    # Header row — white text on dark background
    hdr_style = ParagraphStyle("th", fontSize=7, fontName="Helvetica-Bold",
                               textColor=colors.white, alignment=TA_CENTER, leading=9)
    hdr_row = [Paragraph(h, hdr_style) for h in
               ["#","Customer","City","Credit Term","Cr.Limit","Sale","GP","GP%","Status","Days"]]

    tbl_rows = [hdr_row]
    for i, row in top_df.iterrows():
        s=row['_sale']; g=row['_gp']
        cust = str(row[cust_col]) if cust_col else "-"
        city = str(row[city_col]) if city_col and pd.notna(row.get(city_col)) else "-"
        term = str(row[term_col]) if term_col and pd.notna(row.get(term_col)) else "-"
        lim  = f"{float(row[limit_col])/1e6:.2f}M" if limit_col and pd.notna(row.get(limit_col)) else "-"
        st   = str(row[status_col]) if status_col and pd.notna(row.get(status_col)) else "-"
        st_c = STATUS_COLOR.get(st.lower(), "#1f2937")
        tbl_rows.append([
            _cell(str(i+1), align="CENTER"),
            _cell(cust),                           # wraps automatically
            _cell(city),
            _cell(term),                           # wraps automatically
            _cell(lim, align="CENTER"),
            _cell(f"{s/1e6:.2f}M", align="CENTER"),
            _cell(f"{g/1e6:.2f}M", align="CENTER"),
            _cell(f"{g/s*100:.1f}%" if s>0 else "-", align="CENTER"),
            _cell(st, bold=True, align="CENTER", color=st_c),
            _cell(f"{row['_days']:.0f}d" if days_col else "-", align="CENTER"),
        ])

    tbl = Table(tbl_rows, colWidths=COL_W, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND",     (0,0),(-1,0),  colors.HexColor("#1a1f2e")),
        ("ROWBACKGROUNDS", (0,1),(-1,-1), [colors.white, colors.HexColor("#f9fafb")]),
        ("GRID",           (0,0),(-1,-1), 0.4, colors.HexColor("#e5e7eb")),
        ("TOPPADDING",     (0,0),(-1,-1), 4),
        ("BOTTOMPADDING",  (0,0),(-1,-1), 4),
        ("LEFTPADDING",    (0,0),(-1,-1), 4),
        ("RIGHTPADDING",   (0,0),(-1,-1), 4),
        ("VALIGN",         (0,0),(-1,-1), "MIDDLE"),
    ]))
    story.append(tbl); story.append(Spacer(1,6))
    top10_sale = top_df['_sale'].sum()
    story.append(_chart_desc(
        f"The top 10 customers account for <b>{top10_sale/1e6:.2f}M</b> "
        f"({top10_sale/total_sale*100:.1f}% of total sales). "
        f"Customers with Overdue or Delayed status require priority collections attention.", S))
    story.append(PageBreak()); return story

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


def _salesperson_table(groups, grand_total: float):
    """Ranked table of all salespeople by sales value."""
    tbl_data = [["#", "Sales Person", "Sales", "Share"]]
    for i, g in enumerate(groups, 1):
        pct = f"{(g.value or 0) / grand_total * 100:.1f}%" if grand_total else "-"
        tbl_data.append([str(i), g.label, _fmt(g.value), pct])
    tbl = Table(tbl_data, colWidths=[30, 220, 110, 65])
    tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), colors.HexColor("#2563eb")),
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

    # Gather pre-computed facts directly from data for the LLM
    df   = _get_raw_df(parsed)
    cols = _detect_cols(df)
    facts = []

    sale_col   = cols['sale']
    gp_col     = cols['gp']
    recv_col   = cols['total_received']
    status_col = cols['payment_status']
    days_col   = cols['avg_days']
    city_col   = cols['city']
    region_col = cols['region']
    btype_col  = cols['business_type']
    cust_col   = cols['customer']

    if sale_col:
        sale_s = _num(df, sale_col)
        gp_s   = _num(df, gp_col)
        total_sale = sale_s.sum()
        total_gp   = gp_s.sum()
        gp_pct     = total_gp / total_sale * 100 if total_sale > 0 else 0
        facts.append(f"Total Sales: {_fmt(total_sale)}")
        facts.append(f"Total Gross Profit: {_fmt(total_gp)}")
        facts.append(f"Overall GP Margin: {gp_pct:.1f}%")

        if recv_col:
            total_recv = _num(df, recv_col).sum()
            coll_rate  = total_recv / total_sale * 100 if total_sale > 0 else 0
            facts.append(f"Total Received: {_fmt(total_recv)} (Collection Rate: {coll_rate:.1f}%)")

        if status_col:
            vc = df[status_col].value_counts()
            facts.append(f"Payment Status: {dict(vc)}")

        if days_col:
            avg_days = _num(df, days_col).mean()
            facts.append(f"Average Payment Days: {avg_days:.1f}")

        if city_col:
            agg = _group_sale_gp(df, city_col, sale_col, gp_col or sale_col)
            top_city = agg.iloc[0]
            low_gp   = agg.sort_values('GP_Pct').iloc[0]
            facts.append(f"Top City by Sales: {top_city[city_col]} = {_fmt(top_city['Sale'])}")
            facts.append(f"Lowest GP% City: {low_gp[city_col]} = {low_gp['GP_Pct']:.1f}%")

        if region_col:
            agg = _group_sale_gp(df, region_col, sale_col, gp_col or sale_col)
            top_region = agg.iloc[0]
            facts.append(f"Top Region: {top_region[region_col]} = {_fmt(top_region['Sale'])}")

        if btype_col:
            agg = _group_sale_gp(df, btype_col, sale_col, gp_col or sale_col)
            top_btype = agg.iloc[0]
            facts.append(f"Top Business Type: {top_btype[btype_col]} = {_fmt(top_btype['Sale'])}")

        if cust_col:
            df2 = df.copy()
            df2['_sale'] = pd.to_numeric(df2[sale_col], errors="coerce").fillna(0)
            top_cust = df2.nlargest(1, '_sale').iloc[0]
            facts.append(f"Top Customer: {top_cust[cust_col]} = {_fmt(top_cust['_sale'])}")

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
        story.append(Paragraph(f"&#8226;  {insight}", bullet_style))

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
        "<b>Report generated by AI Financial Analyzer</b>",
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
    story += _top_bottom_customers_sales(parsed, S)
    story += _top_bottom_customers_gp(parsed, S)
    story += _profitability(parsed, S)
    story += _receivables_section(parsed, S)
    story += _customer_detail_section(parsed, S)
    story += _ai_insights(parsed, S, api_key)

    doc.build(story)
    buf.seek(0)
    return buf.read()
