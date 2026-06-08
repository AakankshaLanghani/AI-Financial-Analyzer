"""
Analytics Engine — Deterministic Financial Computation Layer
=============================================================
Executes QueryPlan objects against parsed workbook data using pandas.
ALL numeric computation happens here. The LLM receives only the final
verified result and writes explanation text — it never does arithmetic.

Financial safety rules enforced here:
  1. Subtotal rows ALWAYS excluded before any aggregation.
  2. Percentage metrics aggregated as SUM(num)/SUM(den), NEVER averaged.
  3. Entity-level aggregation BEFORE max/min — avoids row-level confusion.
  4. Zero/null denominators handled explicitly — never silently wrong.
  5. Group-by columns with null values dropped before grouping.
  6. All column resolutions logged in result for full auditability.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from kpi_engine import (
    compute_weighted_ratio,
    get_formula_string,
    is_ratio_metric,
    resolve_ratio_columns,
    safe_sum,
)
from query_planner import QueryPlan


# ─────────────────────────────────────────────────────────────────────────────
# ANALYTICS RESULT DATACLASS
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class GroupEntry:
    label: str
    value: Optional[float]
    num_sum: Optional[float] = None    # for weighted ratios: numerator total
    den_sum: Optional[float] = None    # for weighted ratios: denominator total
    row_count: int = 0


@dataclass
class TrendEntry:
    period: str
    value: Optional[float]


@dataclass
class AnalyticsResult:
    # Core result
    operation:  str
    metric:     str
    group_by:   Optional[str]
    sheet_name: str
    row_count:  int

    # Different result shapes (only one is populated per query)
    groups:       Optional[List[GroupEntry]] = None   # ranked / grouped
    scalar_value: Optional[float]           = None    # single numeric answer
    scalar_label: Optional[str]             = None    # entity the scalar belongs to
    trend_data:   Optional[List[TrendEntry]] = None   # time-series
    compare_data: Optional[List[GroupEntry]] = None   # period comparison

    # Audit trail
    is_weighted_pct:   bool = False
    formula:           str  = ""
    filters_applied:   Dict = field(default_factory=dict)
    columns_used:      Dict = field(default_factory=dict)  # {role: col_name}
    warnings:          List[str] = field(default_factory=list)
    error:             Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# SHEET SELECTION
# ─────────────────────────────────────────────────────────────────────────────

def _score_sheet(sheet: dict, plan: QueryPlan) -> float:
    """Score a parsed sheet for relevance to this query plan."""
    score = 0.0
    table_type  = sheet.get("table_type", "UNKNOWN")
    fingerprint = sheet.get("col_fingerprint", {})
    data_sig    = sheet.get("data_signals", {})

    # Table type match
    if table_type in plan.target_table_types:
        idx = plan.target_table_types.index(table_type)
        score += 40.0 / (idx + 1)
    else:
        score -= 8.0

    # Primary metric column present
    if plan.metric in fingerprint:
        score += 15.0

    # For weighted ratios: both constituent columns present
    if plan.requires_weighted_pct:
        num_col, den_col = resolve_ratio_columns(plan.metric, fingerprint)
        if num_col:
            score += 8.0
        if den_col:
            score += 8.0

    # Group-by column present
    if plan.group_by and plan.group_by in fingerprint:
        score += 10.0

    # Entity filter columns present
    for col_type in plan.entity_filters:
        if col_type in fingerprint:
            score += 5.0

    # Time filters matchable
    if plan.time_filters.get("quarters") or plan.time_filters.get("years"):
        for tf in ("quarter", "month", "year", "date"):
            if tf in fingerprint:
                score += 5.0
                break
        if data_sig.get("has_quarter_vals"):
            score += 3.0

    # Rows exist
    row_count = len(sheet.get("rows", []))
    if row_count > 0:
        score += 2.0

    return score


def _select_best_sheet(plan: QueryPlan, parsed: dict) -> Optional[dict]:
    """Return the best-matching sheet for this plan."""
    sheets = parsed.get("sheets", [])
    if not sheets:
        return None
    scored = sorted(sheets, key=lambda s: _score_sheet(s, plan), reverse=True)
    return scored[0] if scored else None


# ─────────────────────────────────────────────────────────────────────────────
# DATAFRAME BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def _build_dataframe(sheet: dict) -> Tuple[pd.DataFrame, Dict[str, str]]:
    """
    Convert parsed sheet rows into a pandas DataFrame.

    Returns:
        df       — DataFrame with normalized column names + _is_subtotal + _row_id
        col_map  — {col_type: first_matching_col_name_in_df}
    """
    rows = sheet.get("rows", [])
    if not rows:
        return pd.DataFrame(), {}

    records = []
    for row in rows:
        record = dict(row.get("data", {}))
        record["_is_subtotal"] = bool(row.get("is_subtotal", False))
        record["_row_number"]  = row.get("row_number")
        record["_row_id"]      = row.get("row_id", "")
        records.append(record)

    df = pd.DataFrame(records)

    # Build col_map: col_type → first column name in df that matches
    fingerprint: Dict[str, List[str]] = sheet.get("col_fingerprint", {})
    col_map: Dict[str, str] = {}
    for col_type, col_names in fingerprint.items():
        for col_name in col_names:
            if col_name in df.columns:
                col_map[col_type] = col_name
                break

    return df, col_map


# ─────────────────────────────────────────────────────────────────────────────
# FILTER APPLICATION
# ─────────────────────────────────────────────────────────────────────────────

def _apply_entity_filters(
    df: pd.DataFrame,
    entity_filters: Dict[str, List[str]],
    col_map: Dict[str, str],
    fingerprint: Optional[Dict[str, List[str]]] = None,
) -> pd.DataFrame:
    """
    Apply entity value filters (e.g. category="Antidiabetics").
    Each filter is case-insensitive substring/equality match.
    Multiple filters are ANDed together.

    When fingerprint is supplied, ALL columns of a col_type are searched
    (e.g. both 'Product Category' and 'Customer Type' when both map to 'category').
    This prevents filters like 'Distributors' from silently missing the right column.
    """
    for col_type, values in entity_filters.items():
        # Determine candidate columns — all columns of this col_type if fingerprint given
        if fingerprint and col_type in fingerprint:
            candidate_cols = [c for c in fingerprint[col_type] if c in df.columns]
        else:
            col = col_map.get(col_type)
            candidate_cols = [col] if col and col in df.columns else []

        if not candidate_cols:
            continue
        if df.empty:
            break

        # Build OR mask across ALL candidate columns and ALL values
        mask = pd.Series([False] * len(df), index=df.index)
        for col in candidate_cols:
            col_lower = df[col].astype(str).str.lower().str.strip()
            for val in values:
                val_l = val.lower().strip()
                mask |= col_lower.str.contains(re.escape(val_l), na=False)
        df = df[mask].copy()

    return df


def _apply_time_filters(
    df: pd.DataFrame,
    time_filters: Dict,
    col_map: Dict[str, str],
) -> pd.DataFrame:
    """
    Filter rows by quarter/year values found in any column.
    If no time filters specified, returns df unchanged.
    """
    quarters = [q.lower() for q in time_filters.get("quarters", [])]
    years    = [str(y) for y in time_filters.get("years", [])]
    if not quarters and not years:
        return df

    # Find time-related columns
    time_types = ["quarter", "month", "year", "date"]
    time_cols  = [col_map[t] for t in time_types if t in col_map and col_map[t] in df.columns]

    if not time_cols:
        # Fallback: scan all string columns
        time_cols = [c for c in df.columns
                     if c.startswith("_") is False
                     and df[c].dtype == object]

    candidates = quarters + years
    if not candidates:
        return df

    mask = pd.Series([False] * len(df), index=df.index)
    for col in time_cols:
        col_str = df[col].astype(str).str.lower().str.strip()
        for val in candidates:
            mask |= col_str.str.contains(re.escape(val.lower()), na=False)

    result = df[mask].copy()
    return result if not result.empty else df  # relax if nothing matches


# ─────────────────────────────────────────────────────────────────────────────
# METRIC COLUMN RESOLUTION
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_metric_columns(
    plan: QueryPlan,
    col_map: Dict[str, str],
    fingerprint: Dict[str, List[str]],
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Resolve (metric_col, numerator_col, denominator_col) for a plan.

    For ratio metrics: return (pct_col_if_any, num_col, den_col).
      - num_col / den_col are used for weighted computation.
      - pct_col is returned for single-row lookups where no aggregation needed.

    For additive metrics: return (metric_col, None, None).
    """
    metric_col = col_map.get(plan.metric)

    # Check secondary metrics as fallback
    if metric_col is None:
        for sm in plan.secondary_metrics:
            if sm in col_map:
                metric_col = col_map[sm]
                break

    if is_ratio_metric(plan.metric):
        num_col, den_col = resolve_ratio_columns(plan.metric, fingerprint)
        return metric_col, num_col, den_col

    return metric_col, None, None


# ─────────────────────────────────────────────────────────────────────────────
# EXECUTION OPERATIONS
# ─────────────────────────────────────────────────────────────────────────────

def _execute_grouped(
    df: pd.DataFrame,
    plan: QueryPlan,
    group_col: str,
    metric_col: Optional[str],
    num_col: Optional[str],
    den_col: Optional[str],
    sheet_name: str,
    col_map: Optional[Dict[str, str]] = None,
) -> AnalyticsResult:
    """
    Execute group-by aggregation.

    For ratio metrics with constituent columns: SUM(num)/SUM(den)×100 per group.
    For additive metrics: SUM(metric_col) per group.

    Then apply max/min/top_n/bottom_n on the aggregated values.
    """
    warnings: List[str] = []

    # Clean group column
    df = df[df[group_col].notna()].copy()
    df[group_col] = df[group_col].astype(str).str.strip()
    df = df[df[group_col] != ""]

    if df.empty:
        return AnalyticsResult(
            operation=plan.operation,
            metric=plan.metric,
            group_by=plan.group_by,
            sheet_name=sheet_name,
            row_count=0,
            error="No rows after cleaning group-by column",
        )

    # ── Weighted ratio aggregation ──────────────────────────────────────────
    if plan.requires_weighted_pct and num_col and den_col:
        agg_df = compute_weighted_ratio(df, num_col, den_col, group_col)
        agg_df = agg_df.rename(columns={group_col: "_group_key"})
        agg_df = agg_df.dropna(subset=["_ratio"])

        # Count detail rows per group
        row_counts = df.groupby(group_col).size().reset_index(name="_rc")
        row_counts.rename(columns={group_col: "_group_key"}, inplace=True)
        agg_df = agg_df.merge(row_counts, on="_group_key", how="left")

        # Sort
        ascending = (plan.operation == "min")
        agg_df.sort_values("_ratio", ascending=ascending, inplace=True)
        agg_df.reset_index(drop=True, inplace=True)

        # Apply ranking
        agg_df = _apply_ranking(agg_df, plan, "_ratio")

        groups = [
            GroupEntry(
                label     = str(row["_group_key"]),
                value     = round(float(row["_ratio"]), 4) if pd.notna(row["_ratio"]) else None,
                num_sum   = float(row["_num_sum"]) if pd.notna(row["_num_sum"]) else None,
                den_sum   = float(row["_den_sum"]) if pd.notna(row["_den_sum"]) else None,
                row_count = int(row.get("_rc", 0)),
            )
            for _, row in agg_df.iterrows()
        ]

        formula = get_formula_string(plan.metric, num_col, den_col)
        return AnalyticsResult(
            operation       = plan.operation,
            metric          = plan.metric,
            group_by        = plan.group_by,
            sheet_name      = sheet_name,
            row_count       = len(df),
            groups          = groups,
            is_weighted_pct = True,
            formula         = formula,
            columns_used    = {"group": group_col, "numerator": num_col,
                               "denominator": den_col},
            warnings        = warnings,
        )

    # ── Additive aggregation ────────────────────────────────────────────────
    if metric_col is None or metric_col not in df.columns:
        return AnalyticsResult(
            operation=plan.operation,
            metric=plan.metric,
            group_by=plan.group_by,
            sheet_name=sheet_name,
            row_count=len(df),
            error=f"Metric column for '{plan.metric}' not found",
        )

    # Guard: if metric column is the same as group column (e.g. metric=sales_person
    # when question is "who sold more"), fall back to a proper numeric metric.
    if metric_col is None or metric_col == group_col:
        _NUMERIC_FALLBACK_TYPES = ["sales", "revenue", "gross_profit", "profit", "cost", "quantity"]
        _resolved = False
        if col_map:
            for _fb in _NUMERIC_FALLBACK_TYPES:
                _fc = col_map.get(_fb)
                if _fc and _fc in df.columns and _fc != group_col:
                    metric_col = _fc
                    warnings.append(f"Metric '{plan.metric}' same as group; using '{_fb}' instead")
                    _resolved = True
                    break
        if not _resolved:
            # Last resort: first numeric-valued column that isn't the group col or internal
            for _c in df.columns:
                if _c != group_col and not str(_c).startswith("_"):
                    _test = pd.to_numeric(df[_c], errors="coerce").dropna()
                    if len(_test) > 0 and _test.max() > 1000:  # skip small-valued cols like serial_no
                        metric_col = _c
                        warnings.append(f"Fell back to column '{_c}' for metric")
                        _resolved = True
                        break

    if metric_col is None or metric_col == group_col or metric_col not in df.columns:
        return AnalyticsResult(
            operation=plan.operation, metric=plan.metric, group_by=plan.group_by,
            sheet_name=sheet_name, row_count=len(df),
            error=f"Could not find a valid metric column distinct from group column '{group_col}'",
        )

    df[metric_col] = pd.to_numeric(df[metric_col], errors="coerce")
    row_counts_s = df.groupby(group_col).size()

    # payment_days uses MEAN per group, not SUM
    _MEAN_METRICS = {"payment_days"}
    if plan.metric in _MEAN_METRICS:
        # Only include rows where payment days > 0
        df_mean = df[df[metric_col] > 0].copy()
        agg_df = df_mean.groupby(group_col, as_index=False)[metric_col].mean()
        formula_str = f"MEAN({metric_col}) grouped by {group_col}"
    else:
        agg_df = df.groupby(group_col, as_index=False)[metric_col].sum()
        formula_str = f"SUM({metric_col}) grouped by {group_col}"

    agg_df = agg_df.rename(columns={group_col: "_group_key", metric_col: "_metric_value"})

    ascending = (plan.operation == "min")
    agg_df.sort_values("_metric_value", ascending=ascending, inplace=True)

    agg_df["_rc"] = agg_df["_group_key"].map(row_counts_s).fillna(0).astype(int)
    agg_df = _apply_ranking(agg_df, plan, "_metric_value")

    groups = [
        GroupEntry(
            label     = str(row["_group_key"]),
            value     = float(row["_metric_value"]) if pd.notna(row["_metric_value"]) else None,
            row_count = int(row.get("_rc", 0)),
        )
        for _, row in agg_df.iterrows()
    ]

    return AnalyticsResult(
        operation    = plan.operation,
        metric       = plan.metric,
        group_by     = plan.group_by,
        sheet_name   = sheet_name,
        row_count    = len(df),
        groups       = groups,
        formula      = formula_str,
        columns_used = {"group": group_col, "metric": metric_col},
        warnings     = warnings,
    )


def _apply_ranking(
    agg_df: pd.DataFrame,
    plan: QueryPlan,
    value_col: str,
) -> pd.DataFrame:
    """Slice the aggregated DataFrame to honour top_n / bottom_n / max / min."""
    if plan.top_n:
        agg_df = agg_df.sort_values(value_col, ascending=False).head(plan.top_n)
    elif plan.bottom_n:
        agg_df = agg_df.sort_values(value_col, ascending=True).head(plan.bottom_n)
    elif plan.operation == "max":
        agg_df = agg_df.sort_values(value_col, ascending=False).head(1)
    elif plan.operation == "min":
        agg_df = agg_df.sort_values(value_col, ascending=True).head(1)
    return agg_df.reset_index(drop=True)


def _execute_aggregate(
    df: pd.DataFrame,
    plan: QueryPlan,
    metric_col: Optional[str],
    num_col: Optional[str],
    den_col: Optional[str],
    sheet_name: str,
) -> AnalyticsResult:
    """Sum across all (filtered) rows."""
    if plan.requires_weighted_pct and num_col and den_col:
        agg_df = compute_weighted_ratio(df, num_col, den_col)
        ratio  = agg_df.iloc[0]["_ratio"] if len(agg_df) > 0 else None
        return AnalyticsResult(
            operation       = plan.operation,
            metric          = plan.metric,
            group_by        = None,
            sheet_name      = sheet_name,
            row_count       = len(df),
            scalar_value    = float(ratio) if ratio is not None else None,
            is_weighted_pct = True,
            formula         = get_formula_string(plan.metric, num_col, den_col),
            columns_used    = {"numerator": num_col, "denominator": den_col},
        )

    if metric_col is None or metric_col not in df.columns:
        return AnalyticsResult(
            operation=plan.operation, metric=plan.metric, group_by=None,
            sheet_name=sheet_name, row_count=len(df),
            error=f"Metric column for '{plan.metric}' not found",
        )

    df[metric_col] = pd.to_numeric(df[metric_col], errors="coerce")

    # payment_days is a MEAN metric — summing it makes no sense
    _MEAN_METRICS = {"payment_days"}
    if plan.metric in _MEAN_METRICS:
        series = df[metric_col].dropna()
        series = series[series > 0]  # exclude zero/blank rows
        value  = float(series.mean()) if len(series) > 0 else None
        return AnalyticsResult(
            operation    = plan.operation,
            metric       = plan.metric,
            group_by     = None,
            sheet_name   = sheet_name,
            row_count    = len(series),
            scalar_value = value,
            formula      = f"MEAN({metric_col})",
            columns_used = {"metric": metric_col},
        )

    total = safe_sum(df[metric_col])
    return AnalyticsResult(
        operation    = plan.operation,
        metric       = plan.metric,
        group_by     = None,
        sheet_name   = sheet_name,
        row_count    = len(df),
        scalar_value = total,
        formula      = f"SUM({metric_col})",
        columns_used = {"metric": metric_col},
    )


def _execute_max_min(
    df: pd.DataFrame,
    plan: QueryPlan,
    metric_col: Optional[str],
    num_col: Optional[str],
    den_col: Optional[str],
    col_map: Dict[str, str],
    sheet_name: str,
) -> AnalyticsResult:
    """
    Find the entity with the highest or lowest metric value.

    IMPORTANT: For queries like "which product had highest GP%?" without an
    explicit group_by phrase, we still need entity-level aggregation to be
    correct. This function handles the case where group_by is None but the
    question implies finding the best entity.

    Strategy:
      1. If we can identify a natural grouping dimension from the data
         (product, category, region, etc.) — aggregate by it and find max/min.
      2. Otherwise — find the row with the highest/lowest value.
    """
    # Try to infer a natural grouping dimension from available columns
    natural_dimensions = ["product", "sales_person", "customer", "city",
                          "region", "category", "department", "status"]
    inferred_group_col: Optional[str] = None
    for dim in natural_dimensions:
        if dim in col_map and col_map[dim] in df.columns:
            # Only use if the dimension has multiple distinct values
            n_unique = df[col_map[dim]].nunique()
            if n_unique > 1:
                inferred_group_col = col_map[dim]
                inferred_group_type = dim
                break

    if inferred_group_col:
        # Aggregate to entity level, then find max/min
        modified_plan = QueryPlan(
            raw_question          = plan.raw_question,
            metric                = plan.metric,
            secondary_metrics     = plan.secondary_metrics,
            operation             = plan.operation,
            group_by              = inferred_group_type,
            entity_filters        = plan.entity_filters,
            time_filters          = plan.time_filters,
            top_n                 = 1 if plan.operation != "min" else None,
            bottom_n              = 1 if plan.operation == "min" else None,
            requires_weighted_pct = plan.requires_weighted_pct,
            numerator_col_type    = plan.numerator_col_type,
            denominator_col_type  = plan.denominator_col_type,
            target_table_types    = plan.target_table_types,
        )
        result = _execute_grouped(
            df, modified_plan, inferred_group_col,
            metric_col, num_col, den_col, sheet_name, col_map=col_map
        )
        if result.error is None and result.groups:
            result.warnings.append(
                f"group_by='{inferred_group_type}' inferred automatically for max/min query"
            )
        return result

    # Fallback: row-level max/min (no grouping possible)
    if metric_col is None or metric_col not in df.columns:
        return AnalyticsResult(
            operation=plan.operation, metric=plan.metric, group_by=None,
            sheet_name=sheet_name, row_count=len(df),
            error=f"Metric column for '{plan.metric}' not found",
        )

    df[metric_col] = pd.to_numeric(df[metric_col], errors="coerce")
    df_clean = df.dropna(subset=[metric_col])

    if df_clean.empty:
        return AnalyticsResult(
            operation=plan.operation, metric=plan.metric, group_by=None,
            sheet_name=sheet_name, row_count=0,
            error="No numeric values found for metric",
        )

    if plan.operation == "max":
        best_idx = df_clean[metric_col].idxmax()
    else:
        best_idx = df_clean[metric_col].idxmin()

    best_row = df_clean.loc[best_idx]
    best_val = float(best_row[metric_col])

    # Build a readable label from text columns in that row
    text_vals = [
        str(v).strip() for k, v in best_row.items()
        if not str(k).startswith("_") and isinstance(v, str) and str(v).strip()
    ]
    label = " | ".join(text_vals[:3]) or f"Row {best_row.get('_row_number', '?')}"

    return AnalyticsResult(
        operation    = plan.operation,
        metric       = plan.metric,
        group_by     = None,
        sheet_name   = sheet_name,
        row_count    = len(df),
        scalar_value = best_val,
        scalar_label = label,
        formula      = f"{'MAX' if plan.operation == 'max' else 'MIN'}({metric_col}) — row level",
        columns_used = {"metric": metric_col},
        warnings     = ["Row-level max/min used (no grouping column found — "
                        "result may reflect a single period/record, not an entity total)"],
    )


def _execute_trend(
    df: pd.DataFrame,
    plan: QueryPlan,
    col_map: Dict[str, str],
    metric_col: Optional[str],
    num_col: Optional[str],
    den_col: Optional[str],
    sheet_name: str,
) -> AnalyticsResult:
    """Produce a time-ordered series of metric values for trend analysis."""
    # Find time dimension column
    time_col: Optional[str] = None
    for tt in ("quarter", "month", "year", "date"):
        if tt in col_map and col_map[tt] in df.columns:
            time_col = col_map[tt]
            break

    if time_col is None:
        # No explicit time column — fall back to aggregate
        return _execute_aggregate(df, plan, metric_col, num_col, den_col, sheet_name)

    if plan.requires_weighted_pct and num_col and den_col:
        agg_df = compute_weighted_ratio(df, num_col, den_col, group_col=time_col)
        agg_df.rename(columns={time_col: "_period", "_ratio": "_value"}, inplace=True)
        value_col = "_value"
    else:
        if metric_col is None or metric_col not in df.columns:
            return AnalyticsResult(
                operation=plan.operation, metric=plan.metric, group_by=None,
                sheet_name=sheet_name, row_count=len(df),
                error=f"Metric column for '{plan.metric}' not found for trend",
            )
        df[metric_col] = pd.to_numeric(df[metric_col], errors="coerce")
        agg_df = df.groupby(time_col)[metric_col].sum().reset_index()
        agg_df.columns = ["_period", "_value"]
        value_col = "_value"

    trend = [
        TrendEntry(period=str(row["_period"]), value=float(row[value_col])
                   if pd.notna(row[value_col]) else None)
        for _, row in agg_df.iterrows()
    ]

    return AnalyticsResult(
        operation    = plan.operation,
        metric       = plan.metric,
        group_by     = None,
        sheet_name   = sheet_name,
        row_count    = len(df),
        trend_data   = trend,
        formula      = (get_formula_string(plan.metric, num_col, den_col)
                        if plan.requires_weighted_pct else f"SUM({metric_col}) by {time_col}"),
        columns_used = {"time": time_col,
                        "metric": metric_col or "",
                        "numerator": num_col or "",
                        "denominator": den_col or ""},
    )


def _execute_compare(
    df: pd.DataFrame,
    plan: QueryPlan,
    col_map: Dict[str, str],
    metric_col: Optional[str],
    num_col: Optional[str],
    den_col: Optional[str],
    sheet_name: str,
) -> AnalyticsResult:
    """Compare two or more time periods side by side."""
    periods = plan.comparison_periods
    if not periods:
        # Fall back to trend
        return _execute_trend(df, plan, col_map, metric_col, num_col, den_col, sheet_name)

    time_col: Optional[str] = None
    for tt in ("quarter", "month", "year", "date"):
        if tt in col_map and col_map[tt] in df.columns:
            time_col = col_map[tt]
            break

    if time_col is None:
        return _execute_aggregate(df, plan, metric_col, num_col, den_col, sheet_name)

    compare_entries: List[GroupEntry] = []
    for period in periods:
        mask   = df[time_col].astype(str).str.lower().str.strip() == period.lower()
        period_df = df[mask]

        if plan.requires_weighted_pct and num_col and den_col:
            agg = compute_weighted_ratio(period_df, num_col, den_col)
            val = float(agg.iloc[0]["_ratio"]) if len(agg) and pd.notna(agg.iloc[0]["_ratio"]) else None
            n   = float(agg.iloc[0]["_num_sum"]) if len(agg) else None
            d   = float(agg.iloc[0]["_den_sum"]) if len(agg) else None
            compare_entries.append(GroupEntry(label=period, value=val, num_sum=n, den_sum=d,
                                              row_count=len(period_df)))
        else:
            if metric_col and metric_col in period_df.columns:
                period_df[metric_col] = pd.to_numeric(period_df[metric_col], errors="coerce")
                val = float(period_df[metric_col].sum())
                compare_entries.append(GroupEntry(label=period, value=val,
                                                  row_count=len(period_df)))

    return AnalyticsResult(
        operation    = plan.operation,
        metric       = plan.metric,
        group_by     = None,
        sheet_name   = sheet_name,
        row_count    = len(df),
        compare_data = compare_entries,
        formula      = (get_formula_string(plan.metric, num_col, den_col)
                        if plan.requires_weighted_pct else f"SUM({metric_col}) per period"),
        columns_used = {"time": time_col or "", "metric": metric_col or ""},
    )


def _execute_lookup(
    df: pd.DataFrame,
    plan: QueryPlan,
    metric_col: Optional[str],
    num_col: Optional[str],
    den_col: Optional[str],
    sheet_name: str,
) -> AnalyticsResult:
    """
    Return a single value for a specific entity (after entity filter has been applied).
    If multiple rows match, aggregate them correctly.
    """
    n_rows = len(df)
    if n_rows == 0:
        return AnalyticsResult(
            operation=plan.operation, metric=plan.metric, group_by=None,
            sheet_name=sheet_name, row_count=0,
            error="No rows match the specified filters",
        )

    if n_rows == 1:
        # Single row — read value directly
        row = df.iloc[0]
        if metric_col and metric_col in df.columns:
            val = row.get(metric_col)
            if pd.notna(val):
                return AnalyticsResult(
                    operation    = plan.operation,
                    metric       = plan.metric,
                    group_by     = None,
                    sheet_name   = sheet_name,
                    row_count    = 1,
                    scalar_value = float(val),
                    formula      = f"Direct read: {metric_col}",
                    columns_used = {"metric": metric_col},
                )

    # Multiple rows — aggregate
    return _execute_aggregate(df, plan, metric_col, num_col, den_col, sheet_name)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN PUBLIC ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def execute_plan(plan: QueryPlan, parsed: dict) -> AnalyticsResult:
    """
    Execute a QueryPlan deterministically against parsed workbook data.

    Pipeline:
      1. Select best sheet.
      2. Build DataFrame.
      3. Exclude subtotal rows.
      4. Apply entity + time filters.
      5. Resolve metric columns (with weighted-ratio support).
      6. Dispatch to the appropriate operation executor.
      7. Return AnalyticsResult — all numbers fully computed.
    """
    # Step 1 — select sheet
    sheet = _select_best_sheet(plan, parsed)
    if sheet is None:
        return AnalyticsResult(
            operation=plan.operation, metric=plan.metric, group_by=plan.group_by,
            sheet_name="N/A", row_count=0,
            error="No matching sheet found in workbook",
        )

    sheet_name = sheet.get("sheet_name", "Unknown")
    fingerprint = sheet.get("col_fingerprint", {})

    # Step 2 — build DataFrame
    df, col_map = _build_dataframe(sheet)
    if df.empty:
        return AnalyticsResult(
            operation=plan.operation, metric=plan.metric, group_by=plan.group_by,
            sheet_name=sheet_name, row_count=0,
            error="Sheet contains no data rows",
        )

    # Step 3 — exclude subtotals (CRITICAL: prevents double-counting)
    df = df[~df["_is_subtotal"]].copy()

    # Step 4a — apply entity filters
    df = _apply_entity_filters(df, plan.entity_filters, col_map, fingerprint)

    # Step 4b — apply time filters
    if plan.operation != "trend":  # trend queries need all periods
        if plan.time_filters.get("quarters") or plan.time_filters.get("years"):
            df = _apply_time_filters(df, plan.time_filters, col_map)

    if df.empty:
        return AnalyticsResult(
            operation=plan.operation, metric=plan.metric, group_by=plan.group_by,
            sheet_name=sheet_name, row_count=0,
            error=(
                "No rows matched the applied filters. "
                f"Entity filters: {plan.entity_filters}. "
                f"Time filters: {plan.time_filters}."
            ),
        )

    # Step 5 — resolve metric columns
    metric_col, num_col, den_col = _resolve_metric_columns(plan, col_map, fingerprint)

    # Step 6 — resolve group_by actual column
    group_col: Optional[str] = None
    if plan.group_by:
        group_col = col_map.get(plan.group_by)

    # Step 7 — dispatch
    if group_col:
        return _execute_grouped(df, plan, group_col, metric_col, num_col, den_col, sheet_name, col_map=col_map)

    elif plan.operation == "sum":
        return _execute_aggregate(df, plan, metric_col, num_col, den_col, sheet_name)

    elif plan.operation in ("max", "min"):
        return _execute_max_min(df, plan, metric_col, num_col, den_col, col_map, sheet_name)

    elif plan.operation == "trend":
        return _execute_trend(df, plan, col_map, metric_col, num_col, den_col, sheet_name)

    elif plan.operation == "compare":
        return _execute_compare(df, plan, col_map, metric_col, num_col, den_col, sheet_name)

    else:
        return _execute_lookup(df, plan, metric_col, num_col, den_col, sheet_name)
