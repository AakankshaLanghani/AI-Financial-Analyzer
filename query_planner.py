"""
Query Planner — Natural Language → Deterministic Execution Plan
================================================================
Converts a free-text financial question into a structured QueryPlan that
the analytics engine executes deterministically.

Critical improvements over the previous retriever.py approach:
  1. "which [entity_type]" ALWAYS implies group_by=entity_type — no need for
     an explicit "by X" phrase in the question.
  2. Entity values found in data (e.g. "Antidiabetics") become FILTERS on their
     column, not group_by dimensions. group_by is always a DIMENSION TYPE.
  3. Weighted-ratio flag is set whenever the metric is a percentage and
     group_by or ranking is active — this tells the engine to use
     SUM(num)/SUM(den) instead of SUM(pct_col).
  4. Top-N / Bottom-N extracted as integers — never delegated to the LLM.
  5. Comparison periods extracted explicitly for Q1 vs Q2 style queries.
  6. Output is a plain dataclass (no hidden state, fully inspectable).
"""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from kpi_engine import is_ratio_metric, WEIGHTED_RATIO_REGISTRY


# ─────────────────────────────────────────────────────────────────────────────
# QUERY PLAN DATACLASS
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class QueryPlan:
    raw_question: str

    # ── What to measure ──────────────────────────────────────────────────────
    metric: str                          # primary semantic metric type
    secondary_metrics: List[str] = field(default_factory=list)

    # ── How to measure ───────────────────────────────────────────────────────
    operation: str = "lookup"            # sum | max | min | trend | compare | list | lookup

    # ── Grouping dimension ───────────────────────────────────────────────────
    # col_type (e.g. "product", "sales_person", "region") — NOT a column value
    group_by: Optional[str] = None

    # ── Filters ──────────────────────────────────────────────────────────────
    # col_type → list of data values to match (case-insensitive)
    entity_filters: Dict[str, List[str]] = field(default_factory=dict)
    time_filters: Dict = field(default_factory=lambda: {"quarters": [], "years": []})

    # ── Ranking ──────────────────────────────────────────────────────────────
    top_n: Optional[int] = None
    bottom_n: Optional[int] = None

    # ── Weighted ratio ───────────────────────────────────────────────────────
    requires_weighted_pct: bool = False
    numerator_col_type: Optional[str] = None
    denominator_col_type: Optional[str] = None

    # ── Routing ──────────────────────────────────────────────────────────────
    target_table_types: List[str] = field(default_factory=list)

    # ── Comparison ───────────────────────────────────────────────────────────
    comparison_periods: List[str] = field(default_factory=list)
    comparison_entities: List[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# ENTITY BLACKLIST  (values that look like data but are actually vocabulary)
# ─────────────────────────────────────────────────────────────────────────────

ENTITY_BLACKLIST: Set[str] = {
    "total", "subtotal", "grand total", "net total", "sum",
    "total revenue", "total sales", "total income", "total cost",
    "total profit", "total assets", "total liabilities", "total equity",
    "total expenses", "total budget", "total actual", "total variance",
    "revenue", "sales", "profit", "cost", "expense", "income",
    "net profit", "gross profit", "ebit", "ebitda", "cogs",
    "net income", "gross income", "operating profit", "operating income",
    "operating expense", "opex", "sg&a",
    "budget", "actual", "actuals", "variance", "margin",
    "assets", "liabilities", "equity", "cash flow",
    "depreciation", "amortization", "interest", "tax",
    "net sales", "gross sales", "sales revenue", "sales amount",
    "turnover", "profit amount",
    "q1", "q2", "q3", "q4", "quarter",
    "jan", "feb", "mar", "apr", "may", "jun",
    "jul", "aug", "sep", "oct", "nov", "dec",
    "january", "february", "march", "april", "june", "july",
    "august", "september", "october", "november", "december",
    "annual", "yearly", "monthly", "quarterly", "period",
    "fy", "fiscal year", "financial year",
    "highest", "lowest", "maximum", "minimum", "average",
    "best", "worst", "top", "bottom", "most", "least",
    "all", "each", "every", "which", "what",
    "on track", "off track", "favorable", "unfavorable",
    "over budget", "under budget", "above", "below",
    "yes", "no", "amount", "value", "number", "item", "name",
    "data", "report", "sheet", "table",
}


# ─────────────────────────────────────────────────────────────────────────────
# METRIC DETECTION VOCABULARY (ordered most-specific → most-generic)
# ─────────────────────────────────────────────────────────────────────────────

_METRIC_PATTERNS: Dict[str, List[str]] = {
    "net_profit":    ["net profit", "net income", "profit after tax", "pat",
                      "bottom line", "net earnings", "net loss",
                      "profit/(loss)", "net profit/(loss)"],
    "gross_profit":  ["gross profit", "gross income", "gross margin amount"],
    "ebit":          ["ebit", "operating profit", "operating income"],
    "ebitda":        ["ebitda"],
    "cogs":          ["cogs", "cost of goods", "cost of sales", "direct cost"],
    "operating_exp": ["operating expense", "opex", "sg&a", "operating cost"],
    "tax_exp":       ["income tax", "taxation"],
    "depreciation":  ["depreciation", "amortization"],
    "interest_exp":  ["interest expense", "finance cost", "finance charge"],
    "revenue":       ["revenue", "turnover", "total revenue", "total sales",
                      "net sales", "gross sales", "top line", "sales revenue",
                      "income from operations", "sales amount", "sales amt"],
    "margin_pct":    ["gross profit %", "gross profit percent",
                      "gross profit percentage", "gp%", "gp %",
                      "profit margin %", "profit margin%", "profit %",
                      "net margin", "gross margin", "profit margin",
                      "margin %", "margin%", "margin percentage", "margin",
                      "gross profit margin"],
    "profit":        ["profit"],
    "sales":         ["sales"],
    "cost":          ["cost", "expense", "expenditure", "spend", "overhead"],
    "quantity":      ["quantity", "qty", "units", "volume", "units sold"],
    "unit_price":    ["price", "unit price", "selling price", "rate"],
    "budget":        ["budget", "plan", "planned", "target", "forecast"],
    "actual":        ["actual", "actuals", "realized", "achieved"],
    "variance":      ["variance", "difference", "delta", "deviation",
                      "over budget", "under budget"],
    "total_assets":  ["total assets"],
    "current_assets":["current assets"],
    "fixed_assets":  ["fixed assets", "non-current assets", "ppe"],
    "inventory":     ["inventory", "stock", "inventories"],
    "receivables":   ["accounts receivable", "receivable", "debtors"],
    "payables":      ["accounts payable", "payable", "creditors"],
    "cash_equiv":    ["cash and equivalents", "cash balance"],
    "debt":          ["debt", "borrowings", "loans"],
    "equity":        ["equity", "shareholders equity", "net worth"],
    "retained_earn": ["retained earnings", "retained profit"],
    "current_liab":  ["current liabilities"],
    "total_liab":    ["total liabilities"],
    "cf_net":        ["cash flow", "net cash", "free cash flow",
                      "operating cash", "investing cash", "financing cash"],
    "sales_person":  ["sales person", "salesperson", "sales rep",
                      "sales representative", "sales executive",
                      "who sold", "which salesperson", "which rep",
                      "by salesperson", "per salesperson"],
}

# Order matters: more specific metrics checked before generic ones
_METRIC_CHECK_ORDER = [
    "net_profit", "gross_profit", "ebit", "ebitda", "cogs",
    "operating_exp", "tax_exp", "depreciation", "interest_exp",
    "total_assets", "total_liab", "current_assets", "fixed_assets",
    "current_liab", "inventory", "receivables", "payables",
    "cash_equiv", "debt", "retained_earn", "equity",
    "margin_pct",
    "variance", "actual", "budget",
    "revenue", "sales", "profit", "cost",
    "quantity", "unit_price", "cf_net",
    "sales_person",
]


# ─────────────────────────────────────────────────────────────────────────────
# OPERATION KEYWORDS
# ─────────────────────────────────────────────────────────────────────────────

_OP_KEYWORDS = {
    "sum":     ["total", "sum", "combined", "overall", "aggregate",
                "how much total", "add up", "cumulative", "how much",
                "how many", "what is the total", "what was the total"],
    "max":     ["highest", "most", "maximum", "max", "best", "largest",
                "greatest", "peak", "most profitable", "best performing",
                "outperformed", "leading", "which had the highest",
                "achieved the highest", "top performing"],
    "min":     ["lowest", "least", "minimum", "min", "worst", "smallest",
                "weakest", "poorest", "underperformed",
                "which had the lowest", "least profitable",
                "most over budget"],
    "compare": ["compare", "versus", " vs ", "difference between",
                "better than", "worse than", "compared to", "against",
                "relative to", "q1 vs", "q2 vs", "q3 vs", "q4 vs",
                "q1 and q2", "q2 and q3", "q3 and q4"],
    "trend":   ["trend", "over time", "progression", "all quarters",
                "every quarter", "each quarter", "quarter by quarter",
                "qoq", "quarter on quarter", "growth over",
                "across quarters", "over all periods", "month by month",
                "over the months", "across all"],
    "list":    ["list", "show all", "which departments", "which products",
                "which regions", "all departments", "all products",
                "show me all", "give me all", "enumerate"],
    "negative":["loss", "losses", "deficit", "negative", "below zero",
                "in the red", "made a loss", "exceeded budget"],
    "under_budget": ["under budget", "below budget", "surplus", "favorable",
                     "favourable", "saved", "underspent"],
}

# metric → preferred table types (routing hints)
_METRIC_ROUTES: Dict[str, List[str]] = {
    "net_profit":    ["INCOME_STATEMENT"],
    "gross_profit":  ["INCOME_STATEMENT", "PRODUCT_SALES"],
    "ebit":          ["INCOME_STATEMENT"],
    "ebitda":        ["INCOME_STATEMENT"],
    "cogs":          ["INCOME_STATEMENT"],
    "operating_exp": ["INCOME_STATEMENT", "EXPENSE_BREAKDOWN"],
    "tax_exp":       ["INCOME_STATEMENT"],
    "depreciation":  ["INCOME_STATEMENT"],
    "interest_exp":  ["INCOME_STATEMENT"],
    "revenue":       ["INCOME_STATEMENT", "PRODUCT_SALES"],
    "profit":        ["PRODUCT_SALES", "INCOME_STATEMENT"],
    "sales":         ["PRODUCT_SALES", "INCOME_STATEMENT"],
    "cost":          ["PRODUCT_SALES", "INCOME_STATEMENT", "EXPENSE_BREAKDOWN", "BUDGET_VARIANCE"],
    "margin_pct":    ["PRODUCT_SALES"],
    "quantity":      ["PRODUCT_SALES"],
    "unit_price":    ["PRODUCT_SALES"],
    "sales_person":  ["PRODUCT_SALES"],
    "budget":        ["BUDGET_VARIANCE"],
    "actual":        ["BUDGET_VARIANCE"],
    "variance":      ["BUDGET_VARIANCE"],
    "total_assets":  ["BALANCE_SHEET"],
    "current_assets":["BALANCE_SHEET"],
    "fixed_assets":  ["BALANCE_SHEET"],
    "inventory":     ["BALANCE_SHEET"],
    "receivables":   ["BALANCE_SHEET"],
    "payables":      ["BALANCE_SHEET"],
    "cash_equiv":    ["BALANCE_SHEET"],
    "debt":          ["BALANCE_SHEET"],
    "equity":        ["BALANCE_SHEET"],
    "retained_earn": ["BALANCE_SHEET"],
    "current_liab":  ["BALANCE_SHEET"],
    "total_liab":    ["BALANCE_SHEET"],
    "cf_net":        ["CASH_FLOW"],
}


# ─────────────────────────────────────────────────────────────────────────────
# GROUP-BY DETECTION VOCABULARIES
# ─────────────────────────────────────────────────────────────────────────────

# Pass 1: explicit "by X / per X / for each X" phrases → col_type
_EXPLICIT_GROUPBY: Dict[str, str] = {
    "by product":          "product",
    "per product":         "product",
    "for each product":    "product",
    "product wise":        "product",
    "product-wise":        "product",
    "across products":     "product",
    "by item":             "product",
    "per item":            "product",
    "for each item":       "product",
    "by category":         "category",
    "per category":        "category",
    "for each category":   "category",
    "category wise":       "category",
    "by segment":          "category",
    "per segment":         "category",
    "by brand":            "category",
    "by product line":     "category",
    "by region":           "region",
    "per region":          "region",
    "for each region":     "region",
    "region wise":         "region",
    "region-wise":         "region",
    "by city":             "region",
    "per city":            "region",
    "by zone":             "region",
    "by territory":        "region",
    "by location":         "region",
    "across regions":      "region",
    "by department":       "department",
    "per department":      "department",
    "for each department": "department",
    "department wise":     "department",
    "by division":         "department",
    "per division":        "department",
    "by branch":           "department",
    "per branch":          "department",
    "by team":             "department",
    "across departments":  "department",
    "by salesperson":      "sales_person",
    "by sales person":     "sales_person",
    "per salesperson":     "sales_person",
    "per sales person":    "sales_person",
    "by rep":              "sales_person",
    "per rep":             "sales_person",
    "by sales rep":        "sales_person",
    "by agent":            "sales_person",
    "rep wise":            "sales_person",
    "by customer type":    "category",
    "per customer type":   "category",
    "customer type wise":  "category",
    "by customer":         "customer",
    "per customer":        "customer",
    "for each customer":   "customer",
    "customer wise":       "customer",
    "by client":           "customer",
    "per client":          "customer",
    "by quarter":          "quarter",
    "per quarter":         "quarter",
    "for each quarter":    "quarter",
    "quarter by quarter":  "quarter",
    "by month":            "month",
    "per month":           "month",
    "for each month":      "month",
    "month by month":      "month",
    "by year":             "year",
    "per year":            "year",
    "year by year":        "year",
    "year on year":        "year",
}

# Pass 2: "which/what [adjective?] [entity_type]" → implicit group_by
# These patterns fire when the question asks "which X" without "by X"
_WHICH_ENTITY_PATTERNS: List[Tuple[re.Pattern, str]] = [
    # product / item
    (re.compile(r'\bwhich\s+(?:\w+\s+){0,3}products?\b', re.I),      "product"),
    (re.compile(r'\bwhat\s+(?:\w+\s+){0,3}products?\b', re.I),       "product"),
    (re.compile(r'\bwhich\s+(?:\w+\s+){0,3}items?\b', re.I),         "product"),
    (re.compile(r'\bwhat\s+(?:\w+\s+){0,3}items?\b', re.I),          "product"),
    # salesperson / rep / agent
    (re.compile(r'\bwhich\s+(?:\w+\s+){0,3}salesperson\b', re.I),    "sales_person"),
    (re.compile(r'\bwhat\s+(?:\w+\s+){0,3}salesperson\b', re.I),     "sales_person"),
    (re.compile(r'\bwhich\s+(?:\w+\s+){0,3}sales\s*(?:person|rep|executive|representative)\b', re.I), "sales_person"),
    (re.compile(r'\bwhich\s+(?:\w+\s+){0,3}rep\b', re.I),            "sales_person"),
    (re.compile(r'\bwhich\s+(?:\w+\s+){0,3}agent\b', re.I),          "sales_person"),
    # region / city / zone / territory
    (re.compile(r'\bwhich\s+(?:\w+\s+){0,3}regions?\b', re.I),       "region"),
    (re.compile(r'\bwhat\s+(?:\w+\s+){0,3}regions?\b', re.I),        "region"),
    (re.compile(r'\bwhich\s+(?:\w+\s+){0,3}cit(?:y|ies)\b', re.I),  "region"),
    (re.compile(r'\bwhich\s+(?:\w+\s+){0,3}zones?\b', re.I),         "region"),
    (re.compile(r'\bwhich\s+(?:\w+\s+){0,3}territor(?:y|ies)\b', re.I), "region"),
    # category / segment
    (re.compile(r'\bwhich\s+(?:\w+\s+){0,3}categor(?:y|ies)\b', re.I), "category"),
    (re.compile(r'\bwhat\s+(?:\w+\s+){0,3}categor(?:y|ies)\b', re.I),  "category"),
    (re.compile(r'\bwhich\s+(?:\w+\s+){0,3}segments?\b', re.I),      "category"),
    # department / division / branch / team
    (re.compile(r'\bwhich\s+(?:\w+\s+){0,3}departments?\b', re.I),   "department"),
    (re.compile(r'\bwhich\s+(?:\w+\s+){0,3}divisions?\b', re.I),     "department"),
    (re.compile(r'\bwhich\s+(?:\w+\s+){0,3}branches?\b', re.I),      "department"),
    (re.compile(r'\bwhich\s+(?:\w+\s+){0,3}teams?\b', re.I),         "department"),
    # customer / client
    (re.compile(r'\bwhich\s+(?:\w+\s+){0,3}customers?\b', re.I),     "customer"),
    (re.compile(r'\bwhich\s+(?:\w+\s+){0,3}clients?\b', re.I),       "customer"),
]

# Pass 3: "top/bottom N [entity_type]" → implied group_by
_TOPN_GROUPBY: List[Tuple[re.Pattern, str]] = [
    (re.compile(r'\btop\s+\d+\s+products?\b',      re.I), "product"),
    (re.compile(r'\bbottom\s+\d+\s+products?\b',   re.I), "product"),
    (re.compile(r'\btop\s+\d+\s+items?\b',         re.I), "product"),
    (re.compile(r'\bbottom\s+\d+\s+items?\b',      re.I), "product"),
    (re.compile(r'\btop\s+\d+\s+(?:sales\s*)?reps?\b',    re.I), "sales_person"),
    (re.compile(r'\bbottom\s+\d+\s+(?:sales\s*)?reps?\b', re.I), "sales_person"),
    (re.compile(r'\btop\s+\d+\s+salespersons?\b',  re.I), "sales_person"),
    (re.compile(r'\bbottom\s+\d+\s+salespersons?\b', re.I), "sales_person"),
    (re.compile(r'\btop\s+\d+\s+agents?\b',        re.I), "sales_person"),
    (re.compile(r'\btop\s+\d+\s+regions?\b',       re.I), "region"),
    (re.compile(r'\bbottom\s+\d+\s+regions?\b',    re.I), "region"),
    (re.compile(r'\btop\s+\d+\s+cit(?:y|ies)\b',  re.I), "region"),
    (re.compile(r'\btop\s+\d+\s+zones?\b',         re.I), "region"),
    (re.compile(r'\btop\s+\d+\s+territories\b',    re.I), "region"),
    (re.compile(r'\btop\s+\d+\s+categories\b',     re.I), "category"),
    (re.compile(r'\bbottom\s+\d+\s+categories\b',  re.I), "category"),
    (re.compile(r'\btop\s+\d+\s+segments?\b',      re.I), "category"),
    (re.compile(r'\btop\s+\d+\s+(?:divisions?|departments?|branches|teams?)\b', re.I), "department"),
    (re.compile(r'\bbottom\s+\d+\s+(?:divisions?|departments?|branches|teams?)\b', re.I), "department"),
    (re.compile(r'\btop\s+\d+\s+customers?\b',     re.I), "customer"),
    (re.compile(r'\bbottom\s+\d+\s+customers?\b',  re.I), "customer"),
]


# ─────────────────────────────────────────────────────────────────────────────
# INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _detect_metric(q: str) -> str:
    """Return the primary metric semantic type for this question."""
    # Percentage + margin combination → margin_pct wins over gross_profit
    pct_triggers = ["%", "percent", "percentage"]
    margin_terms = ["gross profit", "profit margin", "gp", "margin", "profit %"]
    if any(t in q for t in pct_triggers) and any(t in q for t in margin_terms):
        return "margin_pct"
    # "gross profit margin" or "profit margin" without explicit % also means margin_pct
    if re.search(r"\bgross\s+profit\s+margin\b", q) or re.search(r"\bprofit\s+margin\b", q):
        return "margin_pct"

    for metric in _METRIC_CHECK_ORDER:
        for pat in sorted(_METRIC_PATTERNS.get(metric, []), key=len, reverse=True):
            if re.search(r"\b" + re.escape(pat) + r"\b", q):
                return metric

    return "sales"  # safe fallback


def _detect_secondary_metrics(q: str, primary: str) -> List[str]:
    """Return additional metrics mentioned alongside the primary."""
    found = []
    for metric in _METRIC_CHECK_ORDER:
        if metric == primary:
            continue
        for pat in sorted(_METRIC_PATTERNS.get(metric, []), key=len, reverse=True):
            if re.search(r"\b" + re.escape(pat) + r"\b", q):
                found.append(metric)
                break
    return found[:3]


def _detect_operation(q: str) -> str:
    """Determine the analytical operation from the question."""
    for op, keywords in _OP_KEYWORDS.items():
        for kw in sorted(keywords, key=len, reverse=True):
            if kw in q:
                if op == "negative":
                    return "min"
                if op == "under_budget":
                    return "list"
                return op
    # Default heuristic: specific entity without aggregation keyword = lookup
    return "lookup"


def _detect_time_filters(q: str) -> Dict:
    quarters = re.findall(r"\bq[1-4]\b", q)
    years    = re.findall(r"\b(20\d{2}|19\d{2}|fy\s*\d{2,4})\b", q)
    return {"quarters": quarters, "years": years}


def _detect_group_by(q: str, parsed: dict) -> Optional[str]:
    """
    Three-pass group_by detection.

    Pass 1 — explicit "by X" / "per X" phrases (vocabulary lookup).
    Pass 2 — "which/what [entity_type]" implicit group_by.
    Pass 3 — "top/bottom N [entity_type]" implied group_by.

    Returns the col_type string (e.g. "product") or None.
    Only returns a col_type that actually exists in the workbook.
    """
    def col_type_exists(col_type: str) -> bool:
        for sheet in parsed.get("sheets", []):
            if col_type in sheet.get("col_fingerprint", {}):
                return True
        return False

    # Pass 1
    for phrase in sorted(_EXPLICIT_GROUPBY.keys(), key=len, reverse=True):
        if phrase in q:
            ct = _EXPLICIT_GROUPBY[phrase]
            if col_type_exists(ct):
                return ct

    # Pass 2
    for pattern, col_type in _WHICH_ENTITY_PATTERNS:
        if pattern.search(q):
            if col_type_exists(col_type):
                return col_type

    # Pass 2b — "who / whose" implicitly refers to a person dimension
    if re.search(r"\bwho\b|\bwhose\b", q):
        for person_type in ("sales_person", "customer"):
            if col_type_exists(person_type):
                return person_type

    # Pass 2c — plural entity noun without "which/top" (e.g. "rank all salespersons")
    _PLURAL_ENTITY: List[Tuple[re.Pattern, str]] = [
        (re.compile(r'\b(?:all|rank|each|every|list)\s+(?:the\s+)?salespersons?\b', re.I), "sales_person"),
        (re.compile(r'\b(?:all|rank|each|every|list)\s+(?:the\s+)?(?:sales\s+)?reps?\b', re.I), "sales_person"),
        (re.compile(r'\b(?:all|rank|each|every|list)\s+(?:the\s+)?products?\b', re.I), "product"),
        (re.compile(r'\b(?:all|rank|each|every|list)\s+(?:the\s+)?categor(?:y|ies)\b', re.I), "category"),
        (re.compile(r'\b(?:all|rank|each|every|list)\s+(?:the\s+)?regions?\b', re.I), "region"),
        (re.compile(r'\b(?:all|rank|each|every|list)\s+(?:the\s+)?departments?\b', re.I), "department"),
        (re.compile(r'\b(?:all|rank|each|every|list)\s+(?:the\s+)?customers?\b', re.I), "customer"),
        (re.compile(r'\bsalespersons?\s+(?:by|ranked|sorted)\b', re.I), "sales_person"),
        (re.compile(r'\bcustomer\s+type\b', re.I), "category"),
        (re.compile(r'\bcust(?:omer)?\s+type\b', re.I), "category"),
    ]
    for _pattern, _col_type in _PLURAL_ENTITY:
        if _pattern.search(q):
            if col_type_exists(_col_type):
                return _col_type

    # Pass 3
    for pattern, col_type in _TOPN_GROUPBY:
        if pattern.search(q):
            if col_type_exists(col_type):
                return col_type

    return None


def _detect_top_n(q: str) -> Tuple[Optional[int], Optional[int]]:
    """Extract top_n and bottom_n integers from the question."""
    top_match    = re.search(r"\btop\s+(\d+)\b", q, re.I)
    bottom_match = re.search(r"\bbottom\s+(\d+)\b", q, re.I)
    top_n    = int(top_match.group(1))    if top_match    else None
    bottom_n = int(bottom_match.group(1)) if bottom_match else None
    return top_n, bottom_n


def _detect_entity_filters(q: str, parsed: dict) -> Dict[str, List[str]]:
    """
    Scan actual data values against the question to detect entity filters.
    Returns {col_type: [matched_values]}.

    Values in ENTITY_BLACKLIST and period tokens are excluded.
    Values that match the detected group_by type are NOT added as filters
    (they are the grouping dimension itself, not a filter).
    """
    period_tokens: Set[str] = set(
        re.findall(r"\bq[1-4]\b", q) + re.findall(r"\b(?:20|19)\d{2}\b", q)
    )
    found: Dict[str, List[str]] = {}
    seen: Set[str] = set()

    for sheet in parsed.get("sheets", []):
        fingerprint = sheet.get("col_fingerprint", {})
        for row in sheet.get("rows", []):
            if row.get("is_subtotal"):
                continue
            for col, val in row.get("data", {}).items():
                if val is None or not isinstance(val, str):
                    continue
                vl = val.strip().lower()
                if (not vl
                        or len(vl) < 2
                        or vl in ENTITY_BLACKLIST
                        or vl in period_tokens
                        or vl in seen):
                    continue
                # Check if this value appears in the question.
                # Pass 1 — exact word-boundary match  ("antidiabetics" in question)
                exact_match = bool(re.search(r"\b" + re.escape(vl) + r"\b", q))
                # Pass 2 — prefix/suffix match for plural/singular variants:
                #   question has "antidiabetic", data has "antidiabetics" → vl.startswith(tok)
                #   question has "antidiabetics", data has "antidiabetic" → tok.startswith(vl)
                prefix_match = False
                if not exact_match and len(vl) >= 4:
                    for tok in re.findall(r"\b[a-z]{4,}\b", q):
                        if vl.startswith(tok) or tok.startswith(vl):
                            prefix_match = True
                            break
                if not exact_match and not prefix_match:
                    continue
                # Find which col_type this column belongs to
                for col_type, cols in fingerprint.items():
                    if col in cols:
                        found.setdefault(col_type, [])
                        if vl not in found[col_type]:
                            found[col_type].append(vl)
                        seen.add(vl)
                        break

    return found


def _detect_weighted_ratio(
    metric: str,
    group_by: Optional[str],
    operation: str,
    top_n: Optional[int],
    bottom_n: Optional[int],
    parsed: dict,
) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Determine whether this plan requires a weighted ratio computation.

    A weighted ratio is required when:
      - The metric is a ratio type (margin_pct, variance_pct, etc.) AND
      - We are aggregating across multiple rows for an entity
        (i.e., group_by is set, or top_n/bottom_n ranking is requested)

    Returns (requires_weighted, numerator_col_type, denominator_col_type).
    """
    if not is_ratio_metric(metric):
        return False, None, None

    needs_agg = (group_by is not None or top_n is not None or bottom_n is not None
                 or operation in ("max", "min", "sum", "trend", "list"))

    if not needs_agg:
        return False, None, None

    # Check that the constituent columns actually exist in some sheet
    if metric in WEIGHTED_RATIO_REGISTRY:
        num_types, den_types = WEIGHTED_RATIO_REGISTRY[metric]
        for sheet in parsed.get("sheets", []):
            fp = sheet.get("col_fingerprint", {})
            has_num = any(nt in fp for nt in num_types)
            has_den = any(dt in fp for dt in den_types)
            if has_num and has_den:
                return True, num_types[0], den_types[0]

    # Constituent columns not found — flag it but don't crash
    return False, None, None


def _resolve_target_tables(metric: str, entity_filters: Dict) -> List[str]:
    """Resolve the preferred table types for routing, biased by entity filter types."""
    preferred = list(_METRIC_ROUTES.get(metric, []))

    row_entity_types = {"product", "sku", "category", "region", "sales_person", "customer"}
    dept_entity_types = {"department"}

    has_row_entity  = any(t in entity_filters for t in row_entity_types)
    has_dept_entity = any(t in entity_filters for t in dept_entity_types)

    if has_row_entity and "PRODUCT_SALES" in preferred:
        preferred = ["PRODUCT_SALES"] + [p for p in preferred if p != "PRODUCT_SALES"]
    if has_dept_entity and "BUDGET_VARIANCE" in preferred:
        preferred = ["BUDGET_VARIANCE"] + [p for p in preferred if p != "BUDGET_VARIANCE"]

    return preferred if preferred else ["PRODUCT_SALES", "INCOME_STATEMENT"]


def _detect_comparison_periods(q: str, time_filters: Dict) -> List[str]:
    """Extract periods being compared for 'Q1 vs Q2' style queries."""
    quarters = time_filters.get("quarters", [])
    if len(quarters) >= 2:
        return quarters
    return []


# ─────────────────────────────────────────────────────────────────────────────
# MAIN PUBLIC ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def build_query_plan(question: str, parsed: dict) -> QueryPlan:
    """
    Convert a natural language financial question into a deterministic
    QueryPlan that the analytics engine can execute without guessing.

    Steps:
      1. Detect primary metric (what is being measured)
      2. Detect operation (how to measure: max, sum, trend, etc.)
      3. Detect time filters (quarters, years)
      4. Detect group_by dimension (product, region, salesperson, etc.)
         — includes "which X" implicit inference
      5. Detect entity filters (specific data values like "Antidiabetics")
         — entities matching the group_by type are EXCLUDED from filters
      6. Extract top_n / bottom_n
      7. Determine if weighted ratio computation is needed
      8. Resolve target table types for sheet selection
    """
    q = question.lower().strip()

    metric           = _detect_metric(q)
    secondary        = _detect_secondary_metrics(q, metric)
    operation        = _detect_operation(q)
    time_filters     = _detect_time_filters(q)
    group_by         = _detect_group_by(q, parsed)
    top_n, bottom_n  = _detect_top_n(q)
    entity_filters   = _detect_entity_filters(q, parsed)

    # Force weighted ratio computation when metric is a ratio and we're looking
    # up a specific filtered subset (e.g. "What is GP% for Antidiabetics?")
    # Without this, operation stays "lookup" and requires_weighted_pct stays False,
    # causing the engine to read a single GP% cell value instead of recomputing
    # SUM(GP)/SUM(Sales)*100 over the filtered rows.
    if is_ratio_metric(metric) and entity_filters and operation == "lookup":
        operation = "sum"

    # Prevent the group_by dimension from also being treated as a filter
    # e.g. "which product" → group_by=product; any product name in question
    # should be a value filter unless it IS the group entity
    if group_by and group_by in entity_filters:
        del entity_filters[group_by]

    # Auto-infer group_by from entity comparison patterns.
    # When the question mentions 2+ specific values belonging to the SAME dimension
    # (e.g. "James or Paul", "Antidiabetics vs Respiratory") and no group_by has
    # been detected yet, treat that dimension as the group_by so we get one row
    # per entity rather than a merged single-value lookup.
    # Auto-infer group_by when 2+ entities share the same column type.
    # e.g. "James or Paul" → entity_filters={sales_person:[james,paul]} → group_by=sales_person
    if group_by is None:
        for col_type, values in entity_filters.items():
            if len(values) >= 2:
                group_by = col_type
                break

    # Upgrade operation for ranking queries that didn't trigger max/min directly
    if top_n and operation not in ("max", "sum", "trend", "compare", "list"):
        operation = "max"
    if bottom_n and operation not in ("min", "sum", "trend", "compare", "list"):
        operation = "min"

    # If group_by is set and no explicit operation, sum/rank per group
    if group_by and operation == "lookup":
        operation = "sum"

    requires_w, num_type, den_type = _detect_weighted_ratio(
        metric, group_by, operation, top_n, bottom_n, parsed
    )

    target_tables      = _resolve_target_tables(metric, entity_filters)
    comparison_periods = _detect_comparison_periods(q, time_filters)

    return QueryPlan(
        raw_question            = question,
        metric                  = metric,
        secondary_metrics       = secondary,
        operation               = operation,
        group_by                = group_by,
        entity_filters          = entity_filters,
        time_filters            = time_filters,
        top_n                   = top_n,
        bottom_n                = bottom_n,
        requires_weighted_pct   = requires_w,
        numerator_col_type      = num_type,
        denominator_col_type    = den_type,
        target_table_types      = target_tables,
        comparison_periods      = comparison_periods,
        comparison_entities     = [],
    )
