"""
KPI Engine — Financial Formula Registry & Weighted Ratio Computations
======================================================================
CARDINAL RULE:  Percentage / ratio metrics MUST be computed as
                SUM(numerator) / SUM(denominator).
                NEVER average a percentage column.
                NEVER sum a percentage column.

Why averages are wrong:
  Product A: GP=100, Sales=1000 → GP%=10%
  Product B: GP=400, Sales=500  → GP%=80%
  AVG(GP%) = 45%   ← WRONG
  SUM(GP)/SUM(Sales) = 500/1500 = 33.3%  ← CORRECT
"""

from typing import Dict, Tuple, Optional, List
import pandas as pd


# ─────────────────────────────────────────────────────────────────────────────
# WEIGHTED RATIO REGISTRY
# Maps metric semantic type → (numerator_col_types, denominator_col_types)
# Each list is priority-ordered: first col_type found in the sheet is used.
# ─────────────────────────────────────────────────────────────────────────────

WEIGHTED_RATIO_REGISTRY: Dict[str, Tuple[List[str], List[str]]] = {
    "margin_pct": (
        ["gross_profit", "profit"],          # numerator candidates (priority order)
        ["sales", "revenue"],                # denominator candidates (priority order)
    ),
    "net_margin": (
        ["net_profit"],
        ["revenue", "sales"],
    ),
    "operating_margin": (
        ["ebit", "operating_exp"],
        ["revenue", "sales"],
    ),
    "cogs_pct": (
        ["cogs"],
        ["revenue", "sales"],
    ),
    "variance_pct": (
        ["variance"],
        ["budget"],
    ),
    "return_on_equity": (
        ["net_profit"],
        ["equity"],
    ),
    "debt_to_equity": (
        ["debt"],
        ["equity"],
    ),
    "current_ratio": (
        ["current_assets"],
        ["current_liab"],
    ),
    "asset_turnover": (
        ["revenue", "sales"],
        ["total_assets"],
    ),
}

# Metrics that are percentage/ratio types and MUST NOT be directly summed
RATIO_METRICS: frozenset = frozenset(WEIGHTED_RATIO_REGISTRY.keys()) | frozenset({
    "margin_pct", "variance_pct", "net_margin", "cogs_pct",
    "operating_margin", "return_on_equity",
})

# Additive metrics — safe to SUM directly across rows
ADDITIVE_METRICS: frozenset = frozenset({
    "sales", "revenue", "gross_profit", "net_profit", "profit", "cost",
    "cogs", "ebit", "ebitda", "operating_exp", "tax_exp", "depreciation",
    "interest_exp", "quantity", "budget", "actual", "variance",
    "total_assets", "current_assets", "fixed_assets", "total_liab",
    "current_liab", "long_term_liab", "inventory", "receivables",
    "payables", "cash_equiv", "debt", "equity", "retained_earn",
    "share_capital", "bs_amount", "cf_net", "cf_operating",
    "cf_investing", "cf_financing", "unit_price",
})


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────

def is_ratio_metric(metric: str) -> bool:
    """Return True if this metric requires weighted (ratio) computation."""
    return metric in RATIO_METRICS


def resolve_ratio_columns(
    metric: str,
    fingerprint: Dict[str, List[str]],
) -> Tuple[Optional[str], Optional[str]]:
    """
    Given a metric name and the sheet's column fingerprint, return the
    actual column names for (numerator, denominator).

    Returns (None, None) if the metric is not a ratio or if required
    columns are absent from the sheet.

    Example:
        metric="margin_pct", fingerprint has "gross_profit"→["gross_profit"],
        "sales"→["sales"]
        → returns ("gross_profit", "sales")
    """
    if metric not in WEIGHTED_RATIO_REGISTRY:
        return None, None

    num_types, den_types = WEIGHTED_RATIO_REGISTRY[metric]

    num_col: Optional[str] = None
    for nt in num_types:
        cols = fingerprint.get(nt, [])
        if cols:
            num_col = cols[0]
            break

    den_col: Optional[str] = None
    for dt in den_types:
        cols = fingerprint.get(dt, [])
        if cols:
            den_col = cols[0]
            break

    return num_col, den_col


def compute_weighted_ratio(
    df: pd.DataFrame,
    num_col: str,
    den_col: str,
    group_col: Optional[str] = None,
    scale: float = 100.0,
) -> pd.DataFrame:
    """
    Compute SUM(num_col) / SUM(den_col) × scale, optionally grouped.

    Returns a DataFrame with columns:
        [group_col (if provided), "_num_sum", "_den_sum", "_ratio"]

    Rows where denominator ≤ 0 get _ratio = None.

    FINANCIAL RULE:
        GP% per product = SUM(Gross Profit) / SUM(Net Sales) × 100
        NOT: AVG(GP%)  NOT: SUM(GP%)
    """
    keep_cols = [c for c in [group_col, num_col, den_col] if c]
    work = df[keep_cols].copy()
    work[num_col] = pd.to_numeric(work[num_col], errors="coerce")
    work[den_col] = pd.to_numeric(work[den_col], errors="coerce")

    if group_col:
        agg = work.groupby(group_col, as_index=False, dropna=True).agg(
            _num_sum=(num_col, "sum"),
            _den_sum=(den_col, "sum"),
        )
    else:
        agg = pd.DataFrame(
            {"_num_sum": [work[num_col].sum()], "_den_sum": [work[den_col].sum()]}
        )

    agg["_ratio"] = None
    mask = agg["_den_sum"] > 0
    agg.loc[mask, "_ratio"] = (
        agg.loc[mask, "_num_sum"] / agg.loc[mask, "_den_sum"] * scale
    )

    return agg


def get_formula_string(
    metric: str,
    num_col: Optional[str],
    den_col: Optional[str],
) -> str:
    """Return a human-readable formula for audit trail and LLM context."""
    if num_col and den_col:
        return f"SUM({num_col}) ÷ SUM({den_col}) × 100"
    return f"SUM({metric})"


def safe_sum(series: pd.Series) -> float:
    """Sum a series, treating NaN as 0."""
    return float(pd.to_numeric(series, errors="coerce").fillna(0).sum())


def safe_weighted_pct(numerator_sum: float, denominator_sum: float) -> Optional[float]:
    """
    Safely compute numerator/denominator × 100.
    Returns None if denominator is zero or negative to prevent division errors.
    """
    if denominator_sum <= 0:
        return None
    return (numerator_sum / denominator_sum) * 100.0
