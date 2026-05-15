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
    "8. If VALIDATION WARNINGS are present, note them briefly after your answer.\n\n"
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
