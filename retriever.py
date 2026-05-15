"""
AI Financial Analyzer -- Semantic Retrieval Engine v5
======================================================
Routes by TABLE TYPE (classifier output from parser), never by sheet name.
All routing uses semantic field types from COL_VOCAB. Zero hardcoded labels.

v5 audit fixes:
  1. Subtotal rows ALWAYS excluded in filter_rows (removed `not is_aggregate`
     exception that caused double-counting in aggregate queries).
  2. ENTITY_BLACKLIST prevents financial vocabulary words being mis-detected as
     named entities (previously caused "What is total sales?" to only match the
     subtotal row).
  3. passes_under_budget no longer hardcodes variance sign convention; infers
     it from status column or falls back to permissive (all non-zero variance).
  4. GROUP_BY_VOCAB expanded: by branch/rep/location/zone/territory/agent/team.
  5. Implied group-by detection for "top/bottom N [dimension]" patterns.
"""

import re
from typing import List, Dict, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# ENTITY BLACKLIST
# ---------------------------------------------------------------------------
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
    "favourable", "unfavourable",
    "over budget", "under budget", "above", "below",
    "positive", "negative", "surplus", "deficit",
    "yes", "no", "true", "false",
    "amount", "value", "number", "item", "name",
    "data", "report", "sheet", "table",
}


# ---------------------------------------------------------------------------
# METRIC DETECTION VOCABULARY
# ---------------------------------------------------------------------------

METRIC_PATTERNS: Dict[str, List[str]] = {
    "net_profit":    ["net profit", "net income", "profit after tax", "pat",
                      "bottom line", "net earnings", "net loss",
                      "profit/(loss)", "net profit/(loss)"],
    "gross_profit":  ["gross profit", "gross income"],
    "ebit":          ["ebit", "operating profit", "operating income"],
    "ebitda":        ["ebitda"],
    "cogs":          ["cogs", "cost of goods", "cost of sales", "direct cost"],
    "operating_exp": ["operating expense", "opex", "sg&a", "operating cost"],
    "tax_exp":       ["tax", "income tax", "taxation"],
    "depreciation":  ["depreciation", "amortization"],
    "interest_exp":  ["interest expense", "finance cost", "finance charge"],
    "revenue":       ["revenue", "turnover", "total revenue", "total sales",
                      "net sales", "gross sales", "top line", "sales revenue",
                      "income from operations", "sales amount", "sales amt"],
    "profit":        ["profit"],
    "sales":         ["sales"],
    "cost":          ["cost", "expense", "expenditure", "spend", "overhead"],
    "margin_pct":    ["gross profit %", "gross profit percent",
                      "gross profit percentage", "gp%", "gp %",
                      "profit margin %", "profit margin%", "profit %",
                      "net margin", "gross margin", "profit margin",
                      "margin %", "margin%", "margin percentage", "margin"],
    "quantity":      ["quantity", "qty", "units", "volume", "units sold"],
    "unit_price":    ["price", "unit price", "selling price", "rate"],
    "sales_person":  ["sales person", "salesperson", "sales rep",
                      "sales representative", "sales executive",
                      "who sold", "which salesperson", "which sales person",
                      "which rep", "by salesperson", "by sales person",
                      "per salesperson", "per sales person"],
    "budget":        ["budget", "plan", "planned", "target", "forecast"],
    "actual":        ["actual", "actuals", "realized", "achieved"],
    "variance":      ["variance", "difference", "delta", "deviation",
                      "over budget", "under budget"],
    "status":        ["status", "flag", "on track", "off track"],
    "total_assets":  ["total assets"],
    "current_assets":["current assets"],
    "fixed_assets":  ["fixed assets", "non-current assets", "ppe"],
    "inventory":     ["inventory", "stock", "inventories"],
    "receivables":   ["accounts receivable", "receivable", "debtors"],
    "payables":      ["accounts payable", "payable", "creditors"],
    "cash_equiv":    ["cash and equivalents", "cash and cash equivalents",
                      "cash balance"],
    "debt":          ["debt", "borrowings", "loans", "short-term debt",
                      "long-term debt"],
    "equity":        ["equity", "shareholders equity", "net worth"],
    "retained_earn": ["retained earnings", "retained profit"],
    "current_liab":  ["current liabilities"],
    "total_liab":    ["total liabilities"],
    "bs_amount":     ["amount", "book value", "balance", "value"],
    "cf_net":        ["cash flow", "net cash", "free cash flow",
                      "operating cash", "investing cash", "financing cash"],
}

# ---------------------------------------------------------------------------
# METRIC -> TABLE TYPE ROUTING
# ---------------------------------------------------------------------------

METRIC_ROUTES: Dict[str, List[str]] = {
    "net_profit":    ["INCOME_STATEMENT"],
    "gross_profit":  ["INCOME_STATEMENT"],
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
    "status":        ["BUDGET_VARIANCE"],
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
    "bs_amount":     ["BALANCE_SHEET"],
    "cf_net":        ["CASH_FLOW"],
}

ROW_ENTITY_TYPES: Set[str] = {"product", "sku", "category", "region"}
DEPT_ENTITY_TYPES: Set[str] = {"department"}

# ---------------------------------------------------------------------------
# OPERATION KEYWORDS
# ---------------------------------------------------------------------------

OPS = {
    "aggregate": [
        "total", "sum", "combined", "overall", "aggregate",
        "how much total", "add up", "cumulative", "what is the total",
        "how much", "how many",
    ],
    "maximum": [
        "highest", "most", "maximum", "max", "top", "best", "largest",
        "greatest", "peak", "most profitable", "best performing",
        "which had the highest", "which quarter had the highest",
        "which product had the highest", "which region had the highest",
        "outperformed", "leading",
    ],
    "minimum": [
        "lowest", "least", "minimum", "min", "bottom", "worst",
        "smallest", "weakest", "poorest", "underperformed",
        "which had the lowest", "which quarter had the lowest",
        "which product had the lowest", "which region had the lowest",
        "least profitable", "most over budget",
    ],
    "compare": [
        "compare", "versus", "vs", "difference between", "better than",
        "worse than", "compared to", "against", "relative to",
        "q1 vs", "q2 vs", "q3 vs", "q4 vs",
        "q1 and q2", "q2 and q3", "q3 and q4", "q1 and q3",
    ],
    "negative": [
        "loss", "losses", "deficit", "negative", "below zero",
        "in the red", "made a loss", "at a loss",
        "over budget", "exceeded budget", "exceeded",
    ],
    "under_budget": [
        "under budget", "below budget", "surplus", "favorable",
        "favourable", "saved", "underspent",
    ],
    "trend": [
        "trend", "over time", "progression", "all quarters",
        "every quarter", "each quarter", "quarter by quarter",
        "qoq", "quarter on quarter", "growth over", "across quarters",
        "over all periods",
    ],
    "list": [
        "list", "show all", "which departments", "which products",
        "which regions", "all departments", "all products",
    ],
}

# ---------------------------------------------------------------------------
# GROUP-BY VOCABULARY (expanded)
# ---------------------------------------------------------------------------

GROUP_BY_VOCAB: Dict[str, str] = {
    # Division / Department / Team / Branch / Unit
    "by division":           "department",
    "by department":         "department",
    "by team":               "department",
    "by unit":               "department",
    "by branch":             "department",
    "by business unit":      "department",
    "by cost centre":        "department",
    "by cost center":        "department",
    "per division":          "department",
    "per department":        "department",
    "per team":              "department",
    "per branch":            "department",
    "per unit":              "department",
    "for each division":     "department",
    "for each department":   "department",
    "for each branch":       "department",
    "for each team":         "department",
    "for each unit":         "department",
    "division wise":         "department",
    "division-wise":         "department",
    "department wise":       "department",
    "department-wise":       "department",
    "branch wise":           "department",
    "branch-wise":           "department",
    "across divisions":      "department",
    "across departments":    "department",
    "across branches":       "department",
    "across teams":          "department",
    # Region / City / Location / Territory / Zone
    "by region":             "region",
    "by city":               "region",
    "by location":           "region",
    "by zone":               "region",
    "by territory":          "region",
    "by area":               "region",
    "by market":             "region",
    "by country":            "region",
    "by state":              "region",
    "per region":            "region",
    "per city":              "region",
    "per location":          "region",
    "per zone":              "region",
    "per territory":         "region",
    "per area":              "region",
    "for each region":       "region",
    "for each city":         "region",
    "for each location":     "region",
    "for each zone":         "region",
    "for each territory":    "region",
    "region wise":           "region",
    "region-wise":           "region",
    "city wise":             "region",
    "city-wise":             "region",
    "location wise":         "region",
    "location-wise":         "region",
    "zone wise":             "region",
    "zone-wise":             "region",
    "across regions":        "region",
    "across cities":         "region",
    "across locations":      "region",
    "across zones":          "region",
    "across territories":    "region",
    # Product / Item
    "by product":            "product",
    "per product":           "product",
    "for each product":      "product",
    "product wise":          "product",
    "product-wise":          "product",
    "across products":       "product",
    "by item":               "product",
    "per item":              "product",
    "for each item":         "product",
    "item wise":             "product",
    "item-wise":             "product",
    # Category / Segment / Brand / Channel
    "by category":           "category",
    "per category":          "category",
    "for each category":     "category",
    "category wise":         "category",
    "category-wise":         "category",
    "across categories":     "category",
    "by segment":            "category",
    "per segment":           "category",
    "for each segment":      "category",
    "by product line":       "category",
    "per product line":      "category",
    "by brand":              "category",
    "per brand":             "category",
    "by channel":            "category",
    "per channel":           "category",
    "channel wise":          "category",
    # Salesperson / Rep / Agent
    "by salesperson":        "sales_person",
    "by sales person":       "sales_person",
    "by rep":                "sales_person",
    "by sales rep":          "sales_person",
    "by agent":              "sales_person",
    "by account manager":    "sales_person",
    "by executive":          "sales_person",
    "per salesperson":       "sales_person",
    "per sales person":      "sales_person",
    "per rep":               "sales_person",
    "per sales rep":         "sales_person",
    "per agent":             "sales_person",
    "for each salesperson":  "sales_person",
    "for each sales person": "sales_person",
    "for each rep":          "sales_person",
    "for each agent":        "sales_person",
    "salesperson wise":      "sales_person",
    "rep wise":              "sales_person",
    "rep-wise":              "sales_person",
    "across reps":           "sales_person",
    "across salespersons":   "sales_person",
    # Customer / Client / Account
    "by customer":           "customer",
    "per customer":          "customer",
    "for each customer":     "customer",
    "customer wise":         "customer",
    "customer-wise":         "customer",
    "by client":             "customer",
    "per client":            "customer",
    "by account":            "customer",
    "per account":           "customer",
    # Time periods
    "by quarter":            "quarter",
    "per quarter":           "quarter",
    "for each quarter":      "quarter",
    "quarter by quarter":    "quarter",
    "by month":              "month",
    "per month":             "month",
    "for each month":        "month",
    "month by month":        "month",
    "by year":               "year",
    "per year":              "year",
    "for each year":         "year",
    "year by year":          "year",
    "year on year":          "year",
}

# ---------------------------------------------------------------------------
# IMPLIED GROUP-BY for "top/bottom N [dimension]" queries
# ---------------------------------------------------------------------------

_IMPLIED_GROUPBY: List[tuple] = [
    (re.compile(r'\btop\s+\d+\s+products?\b',       re.I), "product"),
    (re.compile(r'\bbottom\s+\d+\s+products?\b',    re.I), "product"),
    (re.compile(r'\btop\s+\d+\s+items?\b',          re.I), "product"),
    (re.compile(r'\bbottom\s+\d+\s+items?\b',       re.I), "product"),
    (re.compile(r'\btop\s+\d+\s+(?:sales\s*)?reps?\b',  re.I), "sales_person"),
    (re.compile(r'\bbottom\s+\d+\s+(?:sales\s*)?reps?\b', re.I), "sales_person"),
    (re.compile(r'\btop\s+\d+\s+salespersons?\b',   re.I), "sales_person"),
    (re.compile(r'\bbottom\s+\d+\s+salespersons?\b',re.I), "sales_person"),
    (re.compile(r'\btop\s+\d+\s+agents?\b',         re.I), "sales_person"),
    (re.compile(r'\btop\s+\d+\s+regions?\b',        re.I), "region"),
    (re.compile(r'\bbottom\s+\d+\s+regions?\b',     re.I), "region"),
    (re.compile(r'\btop\s+\d+\s+cities\b',          re.I), "region"),
    (re.compile(r'\bbottom\s+\d+\s+cities\b',       re.I), "region"),
    (re.compile(r'\btop\s+\d+\s+locations?\b',      re.I), "region"),
    (re.compile(r'\btop\s+\d+\s+zones?\b',          re.I), "region"),
    (re.compile(r'\btop\s+\d+\s+territories\b',     re.I), "region"),
    (re.compile(r'\btop\s+\d+\s+categories\b',      re.I), "category"),
    (re.compile(r'\bbottom\s+\d+\s+categories\b',   re.I), "category"),
    (re.compile(r'\btop\s+\d+\s+segments?\b',       re.I), "category"),
    (re.compile(r'\btop\s+\d+\s+(?:divisions?|departments?|branches|teams?)\b', re.I), "department"),
    (re.compile(r'\bbottom\s+\d+\s+(?:divisions?|departments?|branches|teams?)\b', re.I), "department"),
    (re.compile(r'\btop\s+\d+\s+customers?\b',      re.I), "customer"),
    (re.compile(r'\bbottom\s+\d+\s+customers?\b',   re.I), "customer"),
    (re.compile(r'\btop\s+\d+\s+clients?\b',        re.I), "customer"),
]


# ---------------------------------------------------------------------------
# QUERY INTENT
# ---------------------------------------------------------------------------

class QueryIntent:
    def __init__(self, question: str, parsed: dict):
        q = question.lower()

        self.quarters: List[str] = re.findall(r"\bq[1-4]\b", q)
        self.years:    List[str] = re.findall(r"\b(20\d{2}|19\d{2}|fy\s?\d{2,4})\b", q)

        self.is_aggregate  = self._has(q, OPS["aggregate"])
        self.is_max        = self._has(q, OPS["maximum"])
        self.is_min        = self._has(q, OPS["minimum"])
        self.is_compare    = self._has(q, OPS["compare"])
        self.is_neg_filter = self._has(q, OPS["negative"])
        self.is_under_bud  = self._has(q, OPS["under_budget"])
        self.is_trend      = self._has(q, OPS["trend"])
        self.is_list       = self._has(q, OPS["list"])

        self.effective_quarters = [] if self.is_trend else self.quarters

        self.metrics: List[str] = self._detect_metrics(q)

        self.entities: List[str] = self._detect_entities(q, parsed)
        self.entity_col_types: List[str] = self._detect_entity_col_types(parsed)
        self.target_table_types: List[str] = self._resolve_routes()

        self.group_by_col: Optional[str] = self._detect_group_by(q, parsed)

    @staticmethod
    def _has(q: str, kws: List[str]) -> bool:
        return any(kw in q for kw in kws)

    def _detect_metrics(self, q: str) -> List[str]:
        found = []
        order = [
            "net_profit", "gross_profit", "ebit", "ebitda", "cogs",
            "operating_exp", "tax_exp", "depreciation", "interest_exp",
            "total_assets", "total_liab", "current_assets", "fixed_assets",
            "current_liab", "inventory", "receivables", "payables",
            "cash_equiv", "debt", "retained_earn", "equity",
            "margin_pct",
            "variance", "status", "actual", "budget",
            "revenue", "sales", "profit", "cost",
            "quantity", "unit_price", "bs_amount", "cf_net",
            "sales_person",
        ]
        for metric in order:
            pats = METRIC_PATTERNS.get(metric, [])
            for pat in sorted(pats, key=len, reverse=True):
                if re.search(r'\b' + re.escape(pat) + r'\b', q):
                    if metric not in found:
                        found.append(metric)
                    break
        pct_triggers = ['%', 'percent', 'percentage']
        margin_terms = ['gross profit', 'profit margin', 'gp', 'margin', 'profit %']
        if any(t in q for t in pct_triggers) and any(t in q for t in margin_terms):
            if 'margin_pct' not in found:
                found.insert(0, 'margin_pct')
        if 'margin_pct' in found and 'gross_profit' in found:
            found.remove('gross_profit')
        return found

    def _detect_entities(self, q: str, parsed: dict) -> List[str]:
        """
        Find actual NAMED DATA VALUES in the question.
        Skips financial vocabulary terms (ENTITY_BLACKLIST) so that words like
        'total', 'sales', 'revenue', 'q1' never filter rows incorrectly.
        """
        found, seen = [], set()
        period_tokens: Set[str] = set(self.quarters + self.years)

        for sheet in parsed.get("sheets", []):
            for row in sheet.get("rows", []):
                for col, val in row.get("data", {}).items():
                    if val is None or not isinstance(val, str):
                        continue
                    vl = val.strip().lower()
                    if (not vl
                            or len(vl) < 2
                            or vl.replace(".", "").replace("-", "").replace(",", "").isdigit()):
                        continue
                    if vl in ENTITY_BLACKLIST or vl in period_tokens:
                        continue
                    if vl not in seen and re.search(r'\b' + re.escape(vl) + r'\b', q):
                        found.append(vl)
                        seen.add(vl)
        return found

    def _detect_entity_col_types(self, parsed: dict) -> List[str]:
        if not self.entities:
            return []
        col_types: Set[str] = set()
        for sheet in parsed.get("sheets", []):
            fp = sheet.get("col_fingerprint", {})
            for row in sheet.get("rows", []):
                for col, val in row.get("data", {}).items():
                    if val is not None and str(val).lower().strip() in self.entities:
                        for ft, cols in fp.items():
                            if col in cols:
                                col_types.add(ft)
        return list(col_types)

    def _detect_group_by(self, q: str, parsed: dict) -> Optional[str]:
        """
        Pass 1: explicit 'by X / per X' phrases (longest first).
        Pass 2: implied 'top/bottom N [dimension]' patterns.
        Returns normalised column name from workbook or None.
        """
        def _find_col(col_type: str) -> Optional[str]:
            for sheet in parsed.get("sheets", []):
                fp = sheet.get("col_fingerprint", {})
                cols = fp.get(col_type, [])
                if cols:
                    return cols[0]
            return None

        for phrase in sorted(GROUP_BY_VOCAB.keys(), key=len, reverse=True):
            if phrase in q:
                return _find_col(GROUP_BY_VOCAB[phrase])

        for pattern, col_type in _IMPLIED_GROUPBY:
            if pattern.search(q):
                col = _find_col(col_type)
                if col is not None:
                    return col

        return None

    def _resolve_routes(self) -> List[str]:
        has_row_entity  = any(t in self.entity_col_types for t in ROW_ENTITY_TYPES)
        has_dept_entity = any(t in self.entity_col_types for t in DEPT_ENTITY_TYPES)

        ordered: List[str] = []

        for metric in self.metrics:
            preferred = list(METRIC_ROUTES.get(metric, []))

            if has_row_entity and "PRODUCT_SALES" in preferred:
                preferred = ["PRODUCT_SALES"] + [p for p in preferred if p != "PRODUCT_SALES"]
            if has_dept_entity and "BUDGET_VARIANCE" in preferred:
                preferred = ["BUDGET_VARIANCE"] + [p for p in preferred if p != "BUDGET_VARIANCE"]

            if metric in ("revenue", "profit", "cost") and not self.entities and not has_row_entity:
                if "INCOME_STATEMENT" in preferred:
                    preferred = ["INCOME_STATEMENT"] + [p for p in preferred if p != "INCOME_STATEMENT"]

            for t in preferred:
                if t not in ordered:
                    ordered.append(t)

        return ordered


# ---------------------------------------------------------------------------
# TABLE SCORER
# ---------------------------------------------------------------------------

def score_table(sheet: dict, intent: QueryIntent) -> float:
    score       = 0.0
    table_type  = sheet.get("table_type", "UNKNOWN")
    fingerprint = sheet.get("col_fingerprint", {})
    data_sig    = sheet.get("data_signals", {})

    if intent.target_table_types:
        if table_type in intent.target_table_types:
            idx = intent.target_table_types.index(table_type)
            score += 40.0 / (idx + 1)
        else:
            score -= 8.0

    for metric in intent.metrics:
        if metric in fingerprint:
            score += 10.0
        for related in _related_fields(metric):
            if related in fingerprint:
                score += 4.0
                break

    if intent.effective_quarters or intent.years:
        for period_ft in ("quarter", "month", "year", "date"):
            if period_ft in fingerprint:
                score += 5.0
                break
        if data_sig.get("has_quarter_vals"):
            score += 3.0

    if intent.entities:
        for row in sheet.get("rows", []):
            for col, val in row.get("data", {}).items():
                if val is not None and str(val).lower().strip() in intent.entities:
                    score += 8.0
                    break
            else:
                continue
            break

    if intent.is_neg_filter and data_sig.get("has_negative"):
        score += 4.0
    if intent.is_under_bud and data_sig.get("has_status_vals"):
        score += 5.0

    return score


def _related_fields(metric: str) -> List[str]:
    relations = {
        "net_profit":  ["profit", "ebit", "gross_profit"],
        "gross_profit":["profit", "revenue", "cogs"],
        "revenue":     ["sales", "net_profit"],
        "profit":      ["net_profit", "gross_profit", "margin_pct"],
        "budget":      ["actual", "variance"],
        "actual":      ["budget", "variance"],
        "variance":    ["budget", "actual"],
        "total_assets":["current_assets", "fixed_assets", "bs_amount"],
        "total_liab":  ["current_liab", "long_term_liab", "bs_amount"],
        "margin_pct":  ["gross_profit", "profit"],
        "sales_person":["sales", "revenue"],
    }
    return relations.get(metric, [])


# ---------------------------------------------------------------------------
# ROW FILTERS (strict AND logic)
# ---------------------------------------------------------------------------

def _row_val_matches_any(row: dict, candidates: List[str]) -> bool:
    for col, val in row.get("data", {}).items():
        if val is None:
            continue
        vl = str(val).lower().strip()
        if any(c == vl or c in vl for c in candidates):
            return True
    return False


def passes_period(row: dict, quarters: List[str], years: List[str]) -> bool:
    if not quarters and not years:
        return True
    return _row_val_matches_any(row, quarters + years)


def passes_metric(row: dict, metrics: List[str],
                  fingerprint: Dict[str, List[str]]) -> bool:
    if not metrics:
        return True
    for metric in metrics:
        for col in fingerprint.get(metric, []):
            if row["data"].get(col) is not None:
                return True
    return any(
        isinstance(v, (int, float))
        for v in row.get("data", {}).values()
        if v is not None
    )


def passes_entity(row: dict, entities: List[str]) -> bool:
    if not entities:
        return True
    return _row_val_matches_any(row, entities)


def passes_neg_filter(row: dict, metrics: List[str],
                      fingerprint: Dict[str, List[str]]) -> bool:
    for metric in metrics:
        for col in fingerprint.get(metric, []):
            val = row["data"].get(col)
            if isinstance(val, (int, float)) and val < 0:
                return True
    return False


def passes_under_budget(row: dict, fingerprint: Dict[str, List[str]],
                        variance_positive_means_under: Optional[bool] = None) -> bool:
    """
    Return True if the row represents an 'under budget' / 'favourable' state.

    1. Status column text is checked first (most reliable).
    2. Variance sign is interpreted per inferred convention when known;
       if unknown, any non-zero variance row passes (let LLM interpret).
    """
    under_keywords = {"under", "favorable", "favourable", "surplus",
                      "positive", "underspent", "saved", "below"}
    for col in fingerprint.get("status", []):
        val = row["data"].get(col)
        if val is None:
            continue
        vl = str(val).lower()
        if any(kw in vl for kw in under_keywords):
            return True

    for col in fingerprint.get("variance", []):
        val = row["data"].get(col)
        if not isinstance(val, (int, float)) or val == 0:
            continue
        if variance_positive_means_under is None:
            return True  # unknown convention -- pass all non-zero
        if variance_positive_means_under and val > 0:
            return True
        if not variance_positive_means_under and val < 0:
            return True

    return False


def _infer_variance_sign_convention(sheet: dict) -> Optional[bool]:
    """Infer whether positive variance means under-budget by analysing rows
    that have both a status and a variance value."""
    fingerprint  = sheet.get("col_fingerprint", {})
    status_cols  = fingerprint.get("status", [])
    var_cols     = fingerprint.get("variance", [])
    if not status_cols or not var_cols:
        return None

    under_kws = {"under", "favorable", "favourable", "surplus", "below", "saved"}
    over_kws  = {"over", "unfavorable", "unfavourable", "deficit", "above", "exceeded"}

    pos_under = neg_under = pos_over = neg_over = 0

    for row in sheet.get("rows", []):
        sv = None
        for sc in status_cols:
            v = row["data"].get(sc)
            if v is not None:
                sv = str(v).lower()
                break
        if sv is None:
            continue
        is_under = any(k in sv for k in under_kws)
        is_over  = any(k in sv for k in over_kws)
        if not is_under and not is_over:
            continue
        for vc in var_cols:
            num = row["data"].get(vc)
            if not isinstance(num, (int, float)):
                continue
            if is_under:
                if num > 0: pos_under += 1
                elif num < 0: neg_under += 1
            if is_over:
                if num > 0: pos_over += 1
                elif num < 0: neg_over += 1

    pos_evidence = pos_under + neg_over
    neg_evidence = neg_under + pos_over
    if pos_evidence + neg_evidence < 3:
        return None
    return pos_evidence >= neg_evidence


def filter_rows(
    sheet: dict,
    intent: QueryIntent,
    relax_entity: bool = False,
    relax_period: bool = False,
    exclude_subtotals: bool = True,
) -> List[dict]:
    """
    Apply AND-logic row filters.

    Subtotal rows are ALWAYS excluded when exclude_subtotals=True, regardless
    of is_aggregate.  Including pre-computed totals in aggregate queries causes
    double-counting; the retriever must sum detail rows only.
    """
    fingerprint = sheet.get("col_fingerprint", {})
    results     = []
    eff_q       = [] if relax_period else intent.effective_quarters
    eff_e       = [] if relax_entity else intent.entities

    var_sign_conv: Optional[bool] = None
    if intent.is_under_bud:
        var_sign_conv = _infer_variance_sign_convention(sheet)

    for row in sheet.get("rows", []):
        if exclude_subtotals and row.get("is_subtotal"):
            continue
        if not passes_period(row, eff_q, intent.years):
            continue
        if not passes_metric(row, intent.metrics, fingerprint):
            continue
        if not passes_entity(row, eff_e):
            continue
        if intent.is_neg_filter and not passes_neg_filter(row, intent.metrics, fingerprint):
            continue
        if intent.is_under_bud and not passes_under_budget(row, fingerprint, var_sign_conv):
            continue
        results.append(row)

    return results


# ---------------------------------------------------------------------------
# POST-FILTER OPERATIONS
# ---------------------------------------------------------------------------

def get_primary_value(row: dict, metrics: List[str],
                      fingerprint: Dict[str, List[str]]) -> Optional[float]:
    for metric in metrics:
        for col in fingerprint.get(metric, []):
            val = row["data"].get(col)
            if isinstance(val, (int, float)):
                return float(val)
    return None


def apply_max_min(rows: List[dict], intent: QueryIntent,
                  fingerprint: Dict[str, List[str]]) -> List[dict]:
    if not rows or not (intent.is_max or intent.is_min):
        return rows
    if not intent.metrics:
        return rows
    numeric = [
        (get_primary_value(r, intent.metrics, fingerprint), r)
        for r in rows
        if get_primary_value(r, intent.metrics, fingerprint) is not None
    ]
    if not numeric:
        return rows
    numeric.sort(key=lambda x: x[0], reverse=intent.is_max)
    return [numeric[0][1]]


# ---------------------------------------------------------------------------
# SOURCE BUILDER
# ---------------------------------------------------------------------------

def build_sources(rows: List[dict], metrics: List[str],
                  fingerprint: Dict[str, List[str]]) -> List[str]:
    sources, seen = [], set()
    metric_cols: Set[str] = set()
    for m in metrics:
        metric_cols.update(fingerprint.get(m, []))

    for row in rows:
        for col, val in row["data"].items():
            if val is None:
                continue
            if metric_cols and col not in metric_cols:
                continue
            src = f"{row['sheet_name']} -> Row {row['row_number']} -> {col}"
            if src not in seen:
                seen.add(src)
                sources.append(src)
        if len(sources) >= 6:
            break
    return sources


# ---------------------------------------------------------------------------
# MAIN ENTRY POINT
# ---------------------------------------------------------------------------

def retrieve_rows(question: str, parsed: dict, max_rows: int = 8) -> dict:
    """
    Retrieve the most relevant rows for a question.

    max_rows applies to non-aggregate, non-trend, non-group queries only.
    For aggregate/trend/group-by, ALL matching rows are returned.
    """
    intent = QueryIntent(question, parsed)

    scored = sorted(
        [(score_table(s, intent), s) for s in parsed.get("sheets", [])],
        key=lambda x: x[0], reverse=True
    )

    selected: List[dict]       = []
    used_sheet: Optional[dict] = None

    if intent.is_compare and len(intent.quarters) >= 2:
        for _, sheet in scored[:2]:
            rows = filter_rows(sheet, intent)
            if not rows:
                rows = filter_rows(sheet, intent, relax_entity=True)
            if rows:
                selected.extend(rows)
                used_sheet = used_sheet or sheet

    elif intent.is_trend:
        _, best = scored[0]
        rows = filter_rows(best, intent)
        if rows:
            selected   = rows
            used_sheet = best

    else:
        for _, sheet in scored:
            rows = filter_rows(sheet, intent)
            if rows:
                selected   = rows
                used_sheet = sheet
                break

        if not selected:
            for _, sheet in scored:
                rows = filter_rows(sheet, intent, relax_entity=True)
                if rows:
                    selected   = rows
                    used_sheet = sheet
                    break

        if not selected:
            for _, sheet in scored:
                rows = filter_rows(sheet, intent, relax_entity=True, relax_period=True)
                if rows:
                    selected   = rows
                    used_sheet = sheet
                    break

    if used_sheet and selected and not intent.group_by_col:
        fp       = used_sheet.get("col_fingerprint", {})
        selected = apply_max_min(selected, intent, fp)

    is_full_scan = intent.is_aggregate or intent.is_trend or bool(intent.group_by_col)
    if not is_full_scan:
        selected = selected[:max_rows]
    else:
        selected = selected[:200]

    fp      = used_sheet.get("col_fingerprint", {}) if used_sheet else {}
    sources = build_sources(selected, intent.metrics, fp)

    return {
        "rows":          selected,
        "sources":       sources,
        "is_aggregate":  intent.is_aggregate,
        "is_comparison": intent.is_compare,
        "is_highest":    intent.is_max,
        "is_lowest":     intent.is_min,
        "is_negative":   intent.is_neg_filter,
        "is_trend":      intent.is_trend,
        "metrics":       intent.metrics,
        "quarters":      intent.effective_quarters,
        "years":         intent.years,
        "entities":      intent.entities,
        "used_sheets":   [used_sheet["sheet_name"]] if used_sheet else [],
        "table_type":    used_sheet.get("table_type", "UNKNOWN") if used_sheet else "UNKNOWN",
        "group_by_col":  intent.group_by_col,
    }
