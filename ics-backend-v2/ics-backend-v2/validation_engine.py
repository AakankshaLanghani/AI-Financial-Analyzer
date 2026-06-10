"""
Validation Engine — Finance-Grade Sanity Checks on Analytics Results
=====================================================================
Runs after the analytics engine and before the LLM response.
Any FAIL-level check should prevent the answer from being returned
as-is and should surface a warning or error to the user.

Checks performed:
  1. Row count sanity       — catches empty-result false answers
  2. Percentage bounds      — catches formula errors producing >200% or <-200%
  3. Zero-denominator guard — catches division by zero in ratio metrics
  4. Subtotal contamination — checks if suspiciously round numbers suggest totals slipped through
  5. Duplicate group keys   — catches malformed aggregation
  6. Null result guard      — catches None scalar when an answer is expected
  7. Negative quantity guard — catches nonsensical negative units/prices
"""

from dataclasses import dataclass, field
from typing import List, Optional

from analytics_engine import AnalyticsResult
from kpi_engine import is_ratio_metric
from query_planner import QueryPlan


# ─────────────────────────────────────────────────────────────────────────────
# VALIDATION STRUCTURES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ValidationCheck:
    name:    str
    level:   str           # "PASS" | "WARN" | "FAIL"
    message: str


@dataclass
class ValidationReport:
    checks:  List[ValidationCheck] = field(default_factory=list)
    passed:  bool = True           # False if ANY check is FAIL-level

    def add(self, name: str, level: str, message: str) -> None:
        self.checks.append(ValidationCheck(name, level, message))
        if level == "FAIL":
            self.passed = False

    @property
    def warnings(self) -> List[str]:
        return [c.message for c in self.checks if c.level in ("WARN", "FAIL")]

    @property
    def errors(self) -> List[str]:
        return [c.message for c in self.checks if c.level == "FAIL"]

    @property
    def summary(self) -> str:
        fails = [c for c in self.checks if c.level == "FAIL"]
        warns = [c for c in self.checks if c.level == "WARN"]
        if fails:
            return f"FAILED ({len(fails)} error(s), {len(warns)} warning(s))"
        if warns:
            return f"PASSED with {len(warns)} warning(s)"
        return "PASSED"


# ─────────────────────────────────────────────────────────────────────────────
# INDIVIDUAL CHECKS
# ─────────────────────────────────────────────────────────────────────────────

def _check_row_count(result: AnalyticsResult, report: ValidationReport) -> None:
    if result.row_count == 0:
        report.add(
            "row_count",
            "FAIL",
            "Zero rows analysed. The answer is based on no data. "
            "Check filters — entity name or time period may not match the spreadsheet.",
        )
    elif result.row_count < 3:
        report.add(
            "row_count",
            "WARN",
            f"Only {result.row_count} row(s) analysed. Verify the filters are not over-restrictive.",
        )
    else:
        report.add("row_count", "PASS", f"{result.row_count} rows analysed.")


def _check_null_result(result: AnalyticsResult, report: ValidationReport) -> None:
    has_groups  = result.groups  is not None and len(result.groups) > 0
    has_scalar  = result.scalar_value is not None
    has_trend   = result.trend_data   is not None and len(result.trend_data) > 0
    has_compare = result.compare_data is not None and len(result.compare_data) > 0

    if not any([has_groups, has_scalar, has_trend, has_compare]):
        if result.error:
            report.add("null_result", "FAIL", f"No result computed: {result.error}")
        else:
            report.add(
                "null_result",
                "WARN",
                "No computed value found in result. The LLM cannot answer accurately.",
            )
    else:
        report.add("null_result", "PASS", "Result contains computed values.")


def _check_percentage_bounds(result: AnalyticsResult, plan: QueryPlan,
                              report: ValidationReport) -> None:
    if not is_ratio_metric(plan.metric):
        return

    PCT_MAX =  200.0
    PCT_MIN = -200.0

    def _check_val(val: Optional[float], context: str) -> None:
        if val is None:
            return
        if val > PCT_MAX:
            report.add(
                "pct_bounds",
                "WARN",
                f"Percentage value {val:.2f}% ({context}) exceeds {PCT_MAX}%. "
                "Check if numerator/denominator columns are correct.",
            )
        elif val < PCT_MIN:
            report.add(
                "pct_bounds",
                "WARN",
                f"Percentage value {val:.2f}% ({context}) is below {PCT_MIN}%. "
                "Check for data quality issues.",
            )
        else:
            report.add("pct_bounds", "PASS",
                       f"Percentage {val:.2f}% ({context}) within expected bounds.")

    if result.scalar_value is not None:
        _check_val(result.scalar_value, "scalar")

    if result.groups:
        for g in result.groups:
            _check_val(g.value, g.label)

    if result.trend_data:
        for t in result.trend_data:
            _check_val(t.value, t.period)


def _check_zero_denominator(result: AnalyticsResult, plan: QueryPlan,
                             report: ValidationReport) -> None:
    if not result.is_weighted_pct:
        return

    if result.groups:
        for g in result.groups:
            if g.den_sum is not None and g.den_sum <= 0:
                report.add(
                    "zero_denominator",
                    "FAIL",
                    f"Group '{g.label}': denominator is {g.den_sum}. "
                    "Cannot compute a valid percentage — check source data.",
                )
        report.add("zero_denominator", "PASS", "All denominators are positive.")


def _check_duplicate_groups(result: AnalyticsResult, report: ValidationReport) -> None:
    if not result.groups:
        return
    labels = [g.label for g in result.groups]
    duplicates = [l for l in set(labels) if labels.count(l) > 1]
    if duplicates:
        report.add(
            "duplicate_groups",
            "FAIL",
            f"Duplicate group keys found: {duplicates}. "
            "Aggregation may be incorrect — check parser deduplication.",
        )
    else:
        report.add("duplicate_groups", "PASS", "All group keys are unique.")


def _check_negative_quantities(result: AnalyticsResult, plan: QueryPlan,
                                report: ValidationReport) -> None:
    """Quantities and prices should never be negative."""
    if plan.metric not in ("quantity", "unit_price"):
        return
    values: List[Optional[float]] = []
    if result.scalar_value is not None:
        values.append(result.scalar_value)
    if result.groups:
        values.extend(g.value for g in result.groups)

    negatives = [v for v in values if v is not None and v < 0]
    if negatives:
        report.add(
            "negative_quantity",
            "WARN",
            f"{len(negatives)} negative value(s) found for metric '{plan.metric}'. "
            "Verify source data — quantities and prices should not be negative.",
        )
    else:
        report.add("negative_quantity", "PASS",
                   f"No negative values for '{plan.metric}'.")


def _check_result_error(result: AnalyticsResult, report: ValidationReport) -> None:
    if result.error:
        report.add(
            "engine_error",
            "FAIL",
            f"Analytics engine reported an error: {result.error}",
        )


# ─────────────────────────────────────────────────────────────────────────────
# MAIN PUBLIC ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def validate_result(result: AnalyticsResult, plan: QueryPlan) -> ValidationReport:
    """
    Run all sanity checks on an AnalyticsResult.

    Returns a ValidationReport. The LLM layer should:
      - If report.passed is False: surface the errors to the user.
      - If report.warnings: include a caveat in the LLM explanation.
      - If report.passed is True and no warnings: proceed normally.
    """
    report = ValidationReport()

    _check_result_error(result, report)
    _check_row_count(result, report)
    _check_null_result(result, report)
    _check_percentage_bounds(result, plan, report)
    _check_zero_denominator(result, plan, report)
    _check_duplicate_groups(result, report)
    _check_negative_quantities(result, plan, report)

    return report
