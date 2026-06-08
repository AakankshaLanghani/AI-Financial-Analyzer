"""
Overview Engine — General Data Summary
=======================================
Computes a comprehensive overview of all data in the workbook.
Called only for open-ended / general questions.
Zero LLM involvement — all numbers are deterministic pandas/Python.
"""

from collections import defaultdict
from typing import Dict, List, Optional, Tuple


def _sum_col_type(rows: list, fp: dict, col_type: str) -> Optional[float]:
    """Sum all values for a given col_type across all rows (excluding subtotals)."""
    cols = fp.get(col_type, [])
    if not cols:
        return None
    total = 0.0
    count = 0
    for row in rows:
        if row.get("is_subtotal"):
            continue
        for col in cols:
            val = row.get("data", {}).get(col)
            if isinstance(val, (int, float)):
                total += val
                count += 1
    return total if count > 0 else None


def _rank_by_dimension(
    rows: list,
    fp: dict,
    dim_type: str,
    value_type: str,
    top_n: int = 3,
) -> List[Tuple[str, float]]:
    """Return top_n (label, value) pairs for a dimension ranked by a value metric."""
    dim_cols   = fp.get(dim_type, [])
    value_cols = fp.get(value_type, [])
    if not dim_cols or not value_cols:
        return []

    dim_totals: Dict[str, float] = defaultdict(float)
    for row in rows:
        if row.get("is_subtotal"):
            continue
        label = None
        for dc in dim_cols:
            v = row.get("data", {}).get(dc)
            if v and isinstance(v, str) and v.strip():
                label = v.strip()
                break
        if not label:
            continue
        for vc in value_cols:
            num = row.get("data", {}).get(vc)
            if isinstance(num, (int, float)):
                dim_totals[label] += num

    return sorted(dim_totals.items(), key=lambda x: -x[1])[:top_n]


def _gp_margin_by_dimension(
    rows: list,
    fp: dict,
    dim_type: str,
) -> List[Tuple[str, float]]:
    """Return GP margin % per dimension entity, sorted highest first."""
    dim_cols = fp.get(dim_type, [])
    gp_cols  = fp.get("gross_profit", [])
    sal_cols = fp.get("sales", fp.get("revenue", []))
    if not dim_cols or not gp_cols or not sal_cols:
        return []

    gp_map:  Dict[str, float] = defaultdict(float)
    sal_map: Dict[str, float] = defaultdict(float)

    for row in rows:
        if row.get("is_subtotal"):
            continue
        label = None
        for dc in dim_cols:
            v = row.get("data", {}).get(dc)
            if v and isinstance(v, str) and v.strip():
                label = v.strip()
                break
        if not label:
            continue
        for gc in gp_cols:
            val = row.get("data", {}).get(gc)
            if isinstance(val, (int, float)):
                gp_map[label] += val
        for sc in sal_cols:
            val = row.get("data", {}).get(sc)
            if isinstance(val, (int, float)):
                sal_map[label] += val

    result = []
    for label in gp_map:
        if sal_map.get(label, 0) > 0:
            result.append((label, gp_map[label] / sal_map[label] * 100))
    return sorted(result, key=lambda x: -x[1])


def compute_overview(parsed: dict) -> dict:
    """
    Compute a comprehensive overview of all data in the workbook.
    Returns a structured dict ready to be passed to the LLM for narration.
    """
    overview = {"sheets": []}

    for sheet in parsed.get("sheets", []):
        rows       = sheet.get("rows", [])
        fp         = sheet.get("col_fingerprint", {})
        table_type = sheet.get("table_type", "UNKNOWN")

        if not rows:
            continue

        data_rows = [r for r in rows if not r.get("is_subtotal")]
        row_count = len(data_rows)

        # ── Key totals ────────────────────────────────────────────────────────
        totals: Dict[str, float] = {}
        for col_type in ["sales", "revenue", "gross_profit", "net_profit",
                         "quantity", "cost", "budget", "actual", "variance"]:
            val = _sum_col_type(data_rows, fp, col_type)
            if val is not None:
                totals[col_type] = val

        # Derive primary revenue key
        rev_key = "sales" if "sales" in totals else ("revenue" if "revenue" in totals else None)

        # GP margin
        if "gross_profit" in totals and rev_key and totals[rev_key] > 0:
            totals["gp_margin_pct"] = totals["gross_profit"] / totals[rev_key] * 100

        # Unique dimension counts
        dim_counts: Dict[str, int] = {}
        for dim in ["product", "category", "region", "sales_person", "customer", "department"]:
            dim_cols = fp.get(dim, [])
            if not dim_cols:
                continue
            unique_vals = set()
            for row in data_rows:
                for dc in dim_cols:
                    v = row.get("data", {}).get(dc)
                    if v and isinstance(v, str) and v.strip():
                        unique_vals.add(v.strip().lower())
            if unique_vals:
                dim_counts[dim] = len(unique_vals)

        # ── Rankings ──────────────────────────────────────────────────────────
        rankings: Dict[str, dict] = {}
        primary_value = rev_key or ("gross_profit" if "gross_profit" in fp else None)

        if primary_value:
            for dim in ["product", "category", "region", "sales_person", "customer", "department"]:
                top = _rank_by_dimension(data_rows, fp, dim, primary_value, top_n=3)
                if top:
                    rankings[dim] = {
                        "metric": primary_value,
                        "top":    top,
                        "total":  dim_counts.get(dim, len(top)),
                    }

        # GP margin rankings (if GP data available)
        gp_rankings: Dict[str, list] = {}
        if "gross_profit" in fp and rev_key:
            for dim in ["product", "region", "sales_person", "category"]:
                margin_data = _gp_margin_by_dimension(data_rows, fp, dim)
                if len(margin_data) >= 2:
                    gp_rankings[dim] = {
                        "highest": margin_data[0],
                        "lowest":  margin_data[-1],
                    }

        overview["sheets"].append({
            "name":        sheet.get("sheet_name", "Sheet"),
            "table_type":  table_type,
            "row_count":   row_count,
            "columns":     sheet.get("original_columns", []),
            "totals":      totals,
            "dim_counts":  dim_counts,
            "rankings":    rankings,
            "gp_rankings": gp_rankings,
        })

    return overview
