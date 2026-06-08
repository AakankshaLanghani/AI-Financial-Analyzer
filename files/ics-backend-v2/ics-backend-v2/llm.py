"""
LLM Layer — Explanation Generation Only
=========================================
The LLM receives fully computed, validated analytics results.
Its ONLY job is to write clear, concise business explanations.

The LLM MUST NOT:
  - Recalculate any number
  - Re-rank any result
  - Infer any winner/loser
  - Average or sum any values
  - Determine trends numerically

All numbers in the prompt are labelled "PYTHON-COMPUTED" to prevent
the model from second-guessing them.

Token efficiency:
  - No raw rows are sent to the LLM (they stay in the analytics layer).
  - Only the final structured result is sent.
  - Max context ~300 tokens for normal queries, ~600 for ranked lists.
  - Temperature = 0.0 (deterministic output).
"""

import json
from typing import Optional

from openai import OpenAI

from analytics_engine import AnalyticsResult, GroupEntry, TrendEntry
from query_planner import QueryPlan
from validation_engine import ValidationReport


# ─────────────────────────────────────────────────────────────────────────────
# NUMBER FORMATTER
# ─────────────────────────────────────────────────────────────────────────────

def _fmt(val: Optional[float], is_pct: bool = False) -> str:
    if val is None:
        return "N/A"
    if is_pct:
        return f"{val:.1f}%"
    if abs(val) >= 1e9:
        return f"{val:,.0f} ({val / 1e9:.2f}B)"
    if abs(val) >= 1e6:
        return f"{val:,.0f} ({val / 1e6:.2f}M)"
    if abs(val) >= 1e3:
        return f"{val:,.0f}"
    try:
        if val == int(val) and abs(val) < 1e15:
            return f"{int(val):,}"
    except (OverflowError, ValueError):
        pass
    return f"{val:.2f}"


# ─────────────────────────────────────────────────────────────────────────────
# CONTEXT BUILDER — Minimal, structured, LLM-safe
# ─────────────────────────────────────────────────────────────────────────────

def _build_context(
    plan: QueryPlan,
    result: AnalyticsResult,
    validation: ValidationReport,
) -> str:
    """
    Produce a compact, structured context block for the LLM.

    All computed values are labelled PYTHON-COMPUTED to make it
    explicit the LLM must use them verbatim.
    """
    is_pct  = result.is_weighted_pct
    lines   = []

    lines.append("=" * 54)
    lines.append("  PYTHON-COMPUTED RESULT  (use verbatim — do not recalculate)")
    lines.append("=" * 54)

    # ── Grouped / ranked result ─────────────────────────────────────────────
    if result.groups:
        op_label = {
            "max":  "HIGHEST",
            "min":  "LOWEST",
            "sum":  "RANKED by",
            "list": "RANKED by",
        }.get(result.operation, "RANKED")

        lines.append(
            f"  {op_label} {plan.metric.upper().replace('_', ' ')}"
            + (f" by {plan.group_by}" if plan.group_by else "")
        )
        if is_pct and result.formula:
            lines.append(f"  Formula: {result.formula}")
        lines.append("")

        for i, g in enumerate(result.groups, 1):
            val_str = _fmt(g.value, is_pct)
            extra   = ""
            if is_pct and g.num_sum is not None and g.den_sum is not None:
                extra = f"  [GP={_fmt(g.num_sum)} / Sales={_fmt(g.den_sum)}]"
            lines.append(f"  {i}. {g.label}: {val_str}{extra}")

        lines.append("")
        lines.append(f"  Groups returned:  {len(result.groups)}")
        lines.append(f"  Rows analysed:    {result.row_count}")

    # ── Scalar result ───────────────────────────────────────────────────────
    elif result.scalar_value is not None:
        val_str = _fmt(result.scalar_value, is_pct)
        label   = f" ({result.scalar_label})" if result.scalar_label else ""
        lines.append(
            f"  RESULT: {plan.metric.upper().replace('_', ' ')} = {val_str}{label}"
        )
        if result.formula:
            lines.append(f"  Formula: {result.formula}")
        lines.append(f"  Rows analysed: {result.row_count}")

    # ── Trend result ─────────────────────────────────────────────────────────
    elif result.trend_data:
        lines.append(f"  TREND: {plan.metric.upper().replace('_', ' ')} over time")
        for t in result.trend_data:
            lines.append(f"  {t.period}: {_fmt(t.value, is_pct)}")
        lines.append(f"  Rows analysed: {result.row_count}")

    # ── Comparison result ────────────────────────────────────────────────────
    elif result.compare_data:
        lines.append(f"  COMPARISON: {plan.metric.upper().replace('_', ' ')}")
        for c in result.compare_data:
            lines.append(f"  {c.label}: {_fmt(c.value, is_pct)}")
        lines.append(f"  Rows analysed: {result.row_count}")

    # ── No result ────────────────────────────────────────────────────────────
    else:
        lines.append("  NO COMPUTED RESULT — see validation warnings below.")

    # ── Filter context ───────────────────────────────────────────────────────
    lines.append("")
    lines.append(f"  Sheet:         {result.sheet_name}")
    if plan.entity_filters:
        lines.append(f"  Filters:       {plan.entity_filters}")
    if plan.time_filters.get('quarters') or plan.time_filters.get('years'):
        lines.append(f"  Time period:   {plan.time_filters}")

    # ── Validation warnings ──────────────────────────────────────────────────
    if validation.warnings:
        lines.append("")
        lines.append("  VALIDATION WARNINGS:")
        for w in validation.warnings:
            lines.append(f"  - {w}")

    # ── Engine warnings ──────────────────────────────────────────────────────
    if result.warnings:
        lines.append("")
        lines.append("  ENGINE NOTES:")
        for w in result.warnings:
            lines.append(f"  - {w}")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# SYSTEM PROMPT  (minimal, finance-safe)
# ─────────────────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = (
    "You are a concise senior financial analyst who writes explanations, "
    "not a calculator.\n\n"
    "RULES — read before every response:\n"
    "1. All numbers come from the PYTHON-COMPUTED RESULT section. "
    "Use them exactly. NEVER recalculate, re-rank, or re-sum.\n"
    "2. If no computed result is present, say: "
    "\"This cannot be determined from the uploaded data.\"\n"
    "3. Do not fabricate, estimate, or use external knowledge.\n"
    "4. Do not assume a currency unless one appears in the data.\n"
    "5. Lead with the direct answer (number first).\n"
    "6. Add 1–2 sentences of business insight only. "
    "Do not restate the number in the insight.\n"
    "7. Ranked lists: output as a numbered list using the exact values provided.\n"
    "8. If VALIDATION WARNINGS are present, note them briefly after your answer.\n"
    "9. For 'how many' questions: the answer is the 'Rows analysed' count, NOT the computed value. "
    "State the count directly (e.g. '11 customers are overdue').\n\n"
    "RESPONSE FORMAT — strict JSON, no extra keys:\n"
    "{\n"
    "  \"answer\": \"direct value or numbered list\",\n"
    "  \"explanation\": \"1–2 sentences of business insight\",\n"
    "  \"caveats\": \"validation warnings if any, else empty string\"\n"
    "}"
)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN PUBLIC ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def generate_explanation(
    question: str,
    plan: QueryPlan,
    result: AnalyticsResult,
    validation: ValidationReport,
    api_key: str,
) -> dict:
    """
    Call the LLM to generate a business explanation for an already-computed result.

    The LLM context contains only the structured result — no raw rows,
    no intermediate data, no opportunity to re-compute.

    Returns a dict with keys: answer, explanation, caveats,
    row_count, formula, validation_passed.
    """
    # Hard-fail path: engine returned an error
    if result.error and not any([result.groups, result.scalar_value is not None,
                                  result.trend_data, result.compare_data]):
        return {
            "answer":           "This cannot be determined from the uploaded data.",
            "explanation":      result.error,
            "caveats":          " | ".join(validation.errors),
            "row_count":        result.row_count,
            "formula":          result.formula,
            "validation_passed": False,
        }

    context  = _build_context(plan, result, validation)
    user_msg = (
        f"QUESTION: {question}\n\n"
        f"{context}\n\n"
        "Use the PYTHON-COMPUTED values above. "
        "Respond in strict JSON only."
    )

    client   = OpenAI(api_key=api_key)
    response = None

    try:
        response = client.chat.completions.create(
            model           = "gpt-4o-mini",
            messages        = [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user",   "content": user_msg},
            ],
            temperature     = 0.0,
            max_tokens      = 500,
            response_format = {"type": "json_object"},
        )

        raw    = response.choices[0].message.content
        parsed = json.loads(raw)

        return {
            "answer":            parsed.get("answer",      "Cannot be determined."),
            "explanation":       parsed.get("explanation", ""),
            "caveats":           parsed.get("caveats",     ""),
            "row_count":         result.row_count,
            "formula":           result.formula,
            "validation_passed": validation.passed,
        }

    except json.JSONDecodeError:
        raw_text = response.choices[0].message.content if response else "No response."
        return {
            "answer":            raw_text,
            "explanation":       "",
            "caveats":           "",
            "row_count":         result.row_count,
            "formula":           result.formula,
            "validation_passed": validation.passed,
        }

    except Exception as e:
        raise Exception(f"LLM error: {e}") from e


# ─────────────────────────────────────────────────────────────────────────────
# OVERVIEW EXPLANATION  — for general / open-ended questions
# ─────────────────────────────────────────────────────────────────────────────

_OVERVIEW_SYSTEM_PROMPT = (
    "You are a senior financial analyst giving a plain-English overview of a dataset.\n\n"
    "RULES:\n"
    "1. Use ONLY the PYTHON-COMPUTED values provided. Never recalculate, estimate, or fabricate.\n"
    "2. Write a clear, business-friendly overview: what the dataset covers, key totals, "
    "top performers, and 1–2 notable insights.\n"
    "3. Do not assume a currency symbol unless it appears in the data.\n"
    "4. Keep it concise and natural — no bullet lists, no headers, flowing sentences.\n"
    "5. Cover the most important dimensions (product, region, salesperson, category) if present.\n\n"
    "RESPONSE FORMAT — strict JSON, no extra keys:\n"
    "{\n"
    "  \"answer\": \"One sentence: what the dataset is and its headline number\",\n"
    "  \"explanation\": \"3–5 sentences covering key totals, top performers across dimensions, and a standout insight\",\n"
    "  \"caveats\": \"\"\n"
    "}"
)


def _build_overview_context(overview: dict) -> str:
    lines = ["=" * 54,
             "  PYTHON-COMPUTED DATA OVERVIEW  (use verbatim — do not recalculate)",
             "=" * 54]

    for sheet in overview.get("sheets", []):
        lines.append(f"\nSheet: {sheet['name']}  |  Type: {sheet['table_type']}  |  Rows: {sheet['row_count']}")

        totals = sheet.get("totals", {})
        if totals:
            lines.append("  Key Totals (PYTHON-COMPUTED):")
            for k, v in totals.items():
                if k == "gp_margin_pct":
                    lines.append(f"    GP Margin: {v:.1f}%")
                elif k == "quantity":
                    lines.append(f"    Total Quantity: {v:,.0f}")
                else:
                    label = k.replace("_", " ").title()
                    lines.append(f"    Total {label}: {v:,.0f}  ({v/1e6:.2f}M)")

        dim_counts = sheet.get("dim_counts", {})
        if dim_counts:
            parts = [f"{v} {k.replace('_',' ')}s" for k, v in dim_counts.items()]
            lines.append(f"  Dimensions: {', '.join(parts)}")

        rankings = sheet.get("rankings", {})
        for dim, info in rankings.items():
            top = info["top"]
            metric = info["metric"].replace("_", " ").title()
            dim_label = dim.replace("_", " ").title()
            lines.append(f"  Top {dim_label}s by {metric}:")
            for label, val in top:
                lines.append(f"    - {label}: {val:,.0f}  ({val/1e6:.2f}M)")

        gp_rankings = sheet.get("gp_rankings", {})
        for dim, info in gp_rankings.items():
            dim_label = dim.replace("_", " ").title()
            h_label, h_val = info["highest"]
            l_label, l_val = info["lowest"]
            lines.append(f"  GP Margin by {dim_label}: highest = {h_label} ({h_val:.1f}%), lowest = {l_label} ({l_val:.1f}%)")

    return "\n".join(lines)


def generate_overview_explanation(question: str, overview: dict, api_key: str) -> dict:
    """
    Generate a natural-language business overview for general/open-ended questions.
    All numbers come from the pre-computed overview dict — LLM only narrates.
    """
    context  = _build_overview_context(overview)
    user_msg = (
        f"QUESTION: {question}\n\n"
        f"{context}\n\n"
        "Use the PYTHON-COMPUTED values above exactly. Respond in strict JSON only."
    )

    total_rows = sum(s.get("row_count", 0) for s in overview.get("sheets", []))
    client     = OpenAI(api_key=api_key)
    response   = None

    try:
        response = client.chat.completions.create(
            model           = "gpt-4o-mini",
            messages        = [
                {"role": "system", "content": _OVERVIEW_SYSTEM_PROMPT},
                {"role": "user",   "content": user_msg},
            ],
            temperature     = 0.0,
            max_tokens      = 600,
            response_format = {"type": "json_object"},
        )

        raw    = response.choices[0].message.content
        parsed = json.loads(raw)

        return {
            "answer":            parsed.get("answer",      "Cannot be determined."),
            "explanation":       parsed.get("explanation", ""),
            "caveats":           parsed.get("caveats",     ""),
            "row_count":         total_rows,
            "formula":           "",
            "validation_passed": True,
        }

    except json.JSONDecodeError:
        raw_text = response.choices[0].message.content if response else "No response."
        return {
            "answer":            raw_text,
            "explanation":       "",
            "caveats":           "",
            "row_count":         total_rows,
            "formula":           "",
            "validation_passed": True,
        }

    except Exception as e:
        raise Exception(f"LLM overview error: {e}") from e
