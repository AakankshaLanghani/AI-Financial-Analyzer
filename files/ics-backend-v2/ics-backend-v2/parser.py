"""
ICS AI Financial Analyzer — Robust Excel Parser v2
===================================================
Handles messy real-world spreadsheets:
  - Headers anywhere in first 20 rows
  - Title rows / metadata / notes above tables
  - Blank rows between data
  - Merged cells (openpyxl flattens them)
  - Subtotal / total rows detected and flagged
  - Multiple tables in one sheet (basic detection)
  - Unnamed / duplicate columns
  - Mixed text+numeric sections
  - Wide-format tables (time periods as columns, financial items as rows)
  - Repeated header row skipping

v2 Improvements over v1:
  ① Deduplicated column fingerprint — revenue_1, revenue_2 still classify as "revenue"
  ② Percentage detection uses column name context to avoid false positives on monetary amounts
  ③ Monetary value signal (has_monetary_vals) added for large absolute numeric values
  ④ Wide-format table detection: periods as column headers, financial items as row labels
     → supplemented fingerprint from text-column values for correct table classification
     → period_col_order list for retriever to do column-level period selection
  ⑤ Header detection: numeric penalty relaxed when financial keyword density is high
     (handles year-wide headers like "Item | 2022 | 2023 | 2024")
  ⑥ Repeated header row detection and skipping (common in large Excel exports)
  ⑦ passes_under_budget: infers variance sign convention from budget/actual columns

v3 Bug-fixes:
  ① "Sales Person" / "Sales Rep" no longer mis-classified as sales metric column
  ② "Gross Profit %" / "GP%" now correctly mapped to margin_pct, not gross_profit
  ③ "Product Category" now correctly mapped to category, not bs_category
  ④ Serial-number column ("S. #", "Sr. No") recognised and kept as-is
  ⑤ "Customer Name" / "Customer" mapped to new customer field type
  ⑥ normalize_col priority reordered so specific types beat generic ones
"""

import re
import pandas as pd
import numpy as np
from typing import Any, List, Dict, Tuple, Optional, Set



# ─────────────────────────────────────────────────────────────────────────────
# COLUMN VOCABULARY
# Maps column name patterns → semantic field type
# Ordered from most specific to most generic to avoid over-matching
# ─────────────────────────────────────────────────────────────────────────────

COL_VOCAB: Dict[str, List[str]] = {
    # ── Serial / row-number columns ───────────────────────────────────────────
    "serial_no":       ["s. #", "s.#", "s#", "sr. no", "sr.no", "sr no",
                        "serial no", "serial number", "sno", "no.", "sr",
                        "row no", "row #", "#"],

    # ── Person / name columns (must come before sales / revenue) ──────────────
    "sales_person":    ["sales person", "salesperson", "sales rep",
                        "sales representative", "sales executive",
                        "sale person", "rep name", "rep", "agent",
                        "account manager", "account executive"],
    "customer":        ["customer name", "customer", "client name", "client",
                        "buyer", "account name", "account", "party name",
                        "party", "distributor name"],

    # ── Specific P&L / Income Statement fields ────────────────────────────────
    "net_profit":      ["net profit", "net income", "profit after tax", "pat",
                        "bottom line", "net earnings", "profit for the period",
                        "net loss", "profit/(loss)", "net profit/(loss)"],
    "gross_profit":    ["gross profit", "gross margin amount", "gross income", "gp"],
    "ebit":            ["ebit", "operating profit", "operating income",
                        "profit before interest", "pbit"],
    "ebitda":          ["ebitda", "earnings before interest tax depreciation"],
    "operating_exp":   ["operating expense", "opex", "sg&a", "sga",
                        "selling general admin", "admin expense",
                        "selling expense", "operating cost"],
    "cogs":            ["cogs", "cost of goods sold", "cost of sales",
                        "cost of revenue", "direct cost", "cost of production"],
    "depreciation":    ["depreciation", "amortization", "amortisation",
                        "d&a", "depreciation and amortization"],
    "interest_exp":    ["interest expense", "finance cost", "finance charge",
                        "interest cost", "borrowing cost"],
    "tax_exp":         ["tax", "income tax", "tax expense", "taxation",
                        "current tax", "deferred tax"],
    "revenue":         ["revenue", "net sales", "gross sales", "turnover",
                        "net revenue", "total revenue", "sales revenue",
                        "income from operations"],

    # ── Product / Sales table fields ──────────────────────────────────────────
    "product":         ["product", "product name", "item name", "item",
                        "goods", "description", "product description"],
    "sku":             ["sku", "sku id", "product id", "item code",
                        "product code", "article", "part no", "part number"],
    # NOTE: "sales" is deliberately BELOW "revenue" and other specifics.
    # "Sales Amt" → sales;  "Sales Person" → already caught by sales_person above.
    "sales":           ["sales", "sales amt", "sales amount", "net sales",
                        "total sales"],
    "cost":            ["cost", "unit cost", "total cost", "cost amount",
                        "expense amount", "spend", "expenditure"],
    "profit":          ["profit", "profit amount", "product profit"],
    # margin_pct MUST list all "Gross Profit %" style variants so they win
    # over the generic "gross profit" match in gross_profit field.
    "margin_pct":      ["margin %", "margin%", "profit margin",
                        "gross margin %", "gross margin%",
                        "margin percentage", "gm%", "profit %", "profit%",
                        "gross profit %", "gross profit%",
                        "gross profit percent", "gross profit percentage",
                        "gp%", "gp %", "gp percent",
                        "net margin %", "net margin%",
                        "profit margin %", "profit margin%"],
    "quantity":        ["quantity", "qty", "units sold", "units", "volume",
                        "sales volume", "no of units"],
    "unit_price":      ["price", "unit price", "selling price", "rate",
                        "price per unit", "list price"],
    # ── Payment / receivables columns ─────────────────────────────────────────
    "total_received":  ["total received", "total receipt", "amount received",
                        "received amount", "cash received", "total collected"],
    "payment_days":    ["avg payment days", "average payment days",
                        "payment days", "days to pay", "avg days"],
    "credit_limit":    ["credit limit", "credit ceiling", "limit"],
    "credit_term":     ["credit term", "credit terms", "payment terms",
                        "payment term", "terms"],

    # category here catches "Customer Type", "Business-Type", "Type", etc.
    # "Product Category" is also caught here (better than bs_category).
    "category":        ["category", "segment", "sub-category", "subcategory",
                        "business-type", "business type", "biz type",
                        "type", "class", "brand", "product line",
                        "product category", "item category",
                        "customer type", "cutomer type", "cust type"],
    # ── City — separate from region so City and Region columns can be distinguished
    "city":            ["city", "cities"],
    "region":          ["region", "area", "zone", "territory",
                        "country", "state", "location", "market"],

    # ── Budget / Variance table fields ────────────────────────────────────────
    "department":      ["department", "dept", "division", "business unit",
                        "cost centre", "cost center", "unit", "team",
                        "function", "business area"],
    "budget":          ["budget", "plan", "planned", "target",
                        "forecast", "budgeted", "planned amount"],
    "actual":          ["actual", "actuals", "actual amount", "realized",
                        "achieved", "actual spend", "actual cost"],
    "variance":        ["variance", "var", "difference", "delta",
                        "deviation", "over/under", "budget variance"],
    "variance_pct":    ["variance %", "variance%", "var %", "var%",
                        "deviation %", "deviation%"],
    "status":          ["status", "flag", "indicator", "note",
                        "remark", "over budget", "under budget",
                        "on track", "off track"],

    # ── Balance Sheet fields ──────────────────────────────────────────────────
    "bs_item":         ["line item", "account", "bs item", "item description",
                        "financial item", "account name", "account description"],
    "bs_amount":       ["amount", "value", "book value", "carrying value",
                        "balance", "net amount"],
    "bs_category":     ["classification", "section",
                        "balance sheet section", "balance sheet category",
                        "asset class"],
    "current_assets":  ["current assets", "current asset", "total current assets"],
    "fixed_assets":    ["fixed assets", "non-current assets", "property plant",
                        "ppe", "intangibles", "long term assets"],
    "total_assets":    ["total assets"],
    "current_liab":    ["current liabilities", "current liability",
                        "total current liabilities"],
    "long_term_liab":  ["long-term liabilities", "non-current liabilities",
                        "long term debt", "deferred tax liability"],
    "total_liab":      ["total liabilities"],
    "equity":          ["equity", "shareholders equity", "stockholders equity",
                        "total equity", "net worth", "owners equity"],
    "retained_earn":   ["retained earnings", "retained profit", "accumulated profit"],
    "share_capital":   ["share capital", "paid up capital", "common stock",
                        "ordinary shares"],
    "inventory":       ["inventory", "stock", "inventories", "closing stock",
                        "opening stock"],
    "receivables":     ["accounts receivable", "receivable", "trade receivable",
                        "debtors", "sundry debtors"],
    "payables":        ["accounts payable", "payable", "trade payable",
                        "creditors", "sundry creditors"],
    "cash_equiv":      ["cash", "cash and equivalents", "cash and cash equivalents",
                        "bank", "liquid assets"],
    "debt":            ["debt", "borrowings", "loans", "loan payable",
                        "bank loan", "term loan", "short-term debt", "long-term debt"],

    # ── Cash Flow fields ──────────────────────────────────────────────────────
    "cf_item":         ["cash flow item", "activity", "cf description"],
    "cf_operating":    ["operating activities", "cash from operations",
                        "operating cash flow", "net cash from operations"],
    "cf_investing":    ["investing activities", "cash from investing",
                        "investing cash flow", "capex", "capital expenditure"],
    "cf_financing":    ["financing activities", "cash from financing",
                        "financing cash flow", "dividends paid"],
    "cf_net":          ["net cash", "net change in cash", "free cash flow",
                        "cash generated", "cash used"],

    # ── Period / Time fields ──────────────────────────────────────────────────
    "quarter":         ["quarter", "qtr", "q1", "q2", "q3", "q4", "period"],
    "month":           ["month", "jan", "feb", "mar", "apr", "may", "jun",
                        "jul", "aug", "sep", "oct", "nov", "dec",
                        "january", "february", "march", "april", "june",
                        "july", "august", "september", "october", "november", "december"],
    "year":            ["year", "fy", "fiscal year", "annual", "yearly",
                        "financial year"],
    "date":            ["date", "as of", "as at", "period end", "period date",
                        "reporting date", "year end"],
}

# ─────────────────────────────────────────────────────────────────────────────
# SUBTOTAL / TOTAL ROW DETECTION
# ─────────────────────────────────────────────────────────────────────────────

SUBTOTAL_LABELS = {
    "total", "subtotal", "grand total", "sum", "net total",
    "total revenue", "total cost", "total profit", "total assets",
    "total liabilities", "total equity", "total expenses",
    "total budget", "total actual", "total variance",
    "total income", "total sales",
}

# ── REMOVED from v3: "net profit" and "gross profit" ─────────────────────────
# These are valid P&L LINE ITEMS (especially in wide-format income statements
# where each row IS a metric).  Flagging them as subtotals caused the system to
# silently drop the most important rows from analysis.  True subtotals must
# contain the word "total" / "subtotal" / "grand total" explicitly.
# ─────────────────────────────────────────────────────────────────────────────

# Extra guard: labels that are ONLY a subtotal when the row contains NO other
# meaningful text (i.e. the label fills the row by itself with numeric values)
_SUBTOTAL_NUMERIC_LABELS = {
    "net profit", "gross profit",
}

_TOTAL_KEYWORDS = {"total", "subtotal", "grand total", "sum", "net total"}


def is_subtotal_row(row_vals: list) -> bool:
    """
    Return True if this row is a pre-aggregated summary / total row that
    should be excluded from raw-data aggregation.

    Rules (in order):
    1. Any cell text contains an explicit "total" / "subtotal" / "grand total"
       keyword → True  (e.g. "Total Revenue", "Grand Total")
    2. Any cell text EXACTLY matches a known subtotal label (SUBTOTAL_LABELS)
       → True
    3. For _SUBTOTAL_NUMERIC_LABELS ("net profit", "gross profit"): only flag
       if the row has EXACTLY ONE non-empty text value AND at least one numeric
       → these are bottom-line totals in narrow-format P&L sheets.
       In wide-format sheets these rows have MANY numeric columns so we keep
       them (they are data rows, not totals).
    """
    text_vals = [v.lower().strip() for v in row_vals if isinstance(v, str) and str(v).strip()]
    numeric_count = sum(1 for v in row_vals if isinstance(v, (int, float)))

    for vl in text_vals:
        # Rule 1 — explicit total keyword inside the cell value
        if any(kw in vl for kw in _TOTAL_KEYWORDS):
            return True
        # Rule 2 — exact match of a full subtotal label
        if vl in SUBTOTAL_LABELS:
            return True

    # Rule 3 — special labels only when the row looks like a narrow-format total
    # (single text value + ≤ 2 numbers, e.g. "Net Profit | 1,250,000")
    if len(text_vals) == 1 and numeric_count <= 2:
        if text_vals[0] in _SUBTOTAL_NUMERIC_LABELS:
            return True

    return False


# ─────────────────────────────────────────────────────────────────────────────
# COLUMN CLASSIFIER
# ─────────────────────────────────────────────────────────────────────────────

def classify_column(col_name: str) -> List[str]:
    """Return all semantic field types matching this column name."""
    if not col_name or col_name.startswith("col_"):
        return ["unknown"]
    col_l = col_name.lower().strip()
    col_l = re.sub(r'\s*\(.*?\)', '', col_l).strip()
    matches = []
    for field_type, patterns in COL_VOCAB.items():
        for pat in patterns:
            if pat == col_l or pat in col_l or (len(col_l) > 3 and col_l in pat):
                matches.append(field_type)
                break
    return matches if matches else ["unknown"]


def build_col_fingerprint(columns: List[str]) -> Dict[str, List[str]]:
    """
    field_type -> [col_names that matched it].

    Strategy (in order):
    1. Strip _N deduplication suffix to get base name.
    2. If base name IS a COL_VOCAB key, use it directly.
       This correctly handles normalized names like "gross_profit" and "margin_pct"
       which use underscores while COL_VOCAB patterns use spaces.
    3. Otherwise, run vocabulary pattern matching via classify_column().
    """
    fp: Dict[str, List[str]] = {}
    for col in columns:
        base_col = re.sub(r'_\d+$', '', col)
        if base_col in COL_VOCAB:
            types = [base_col]
        else:
            types = classify_column(base_col)
            if types == ["unknown"] and base_col != col:
                types = classify_column(col)
        for ft in types:
            fp.setdefault(ft, []).append(col)
    return fp


# ─────────────────────────────────────────────────────────────────────────────
# DATA SIGNAL SCANNER
# ─────────────────────────────────────────────────────────────────────────────

QUARTER_RE   = re.compile(r'\bq[1-4]\b', re.I)
MONTH_NAMES  = {
    "jan","feb","mar","apr","may","jun","jul","aug","sep","oct","nov","dec",
    "january","february","march","april","june","july","august",
    "september","october","november","december"
}
STATUS_TERMS = {
    "over budget","under budget","above","below","surplus","deficit",
    "favorable","unfavorable","favourable","unfavourable",
    "on track","off track","adverse","positive","negative",
}
BS_SECTION_TERMS = {
    "current assets","fixed assets","non-current assets","total assets",
    "current liabilities","non-current liabilities","total liabilities",
    "shareholders equity","equity","retained earnings",
}

PCT_COL_KEYWORDS = ("pct", "percent", "%", "margin", "rate", "ratio", "yield", "growth")


def scan_data_signals(rows: List[dict]) -> Dict:
    signals = {
        "has_negative":       False,
        "has_quarter_vals":   False,
        "has_month_vals":     False,
        "has_status_vals":    False,
        "has_bs_sections":    False,
        "has_pct_vals":       False,
        "has_monetary_vals":  False,
        "numeric_col_count":  0,
        "text_col_count":     0,
        "approx_row_count":   len(rows),
        "numeric_cols":       set(),
        "text_cols":          set(),
        "period_values_seen": set(),
    }
    for row in rows:
        for col, val in row.get("data", {}).items():
            if val is None:
                continue
            if isinstance(val, (int, float)):
                signals["numeric_cols"].add(col)
                if val < 0:
                    signals["has_negative"] = True
                if abs(val) >= 1_000:
                    signals["has_monetary_vals"] = True
                col_lower = col.lower()
                col_suggests_pct = any(kw in col_lower for kw in PCT_COL_KEYWORDS)
                if col_suggests_pct and 0 <= abs(val) <= 100:
                    signals["has_pct_vals"] = True
                elif isinstance(val, float) and 0 < abs(val) < 1.0:
                    signals["has_pct_vals"] = True
            elif isinstance(val, str):
                signals["text_cols"].add(col)
                vl = val.lower().strip()
                if QUARTER_RE.search(vl):
                    signals["has_quarter_vals"] = True
                    signals["period_values_seen"].add(vl.strip())
                if vl in MONTH_NAMES:
                    signals["has_month_vals"] = True
                    signals["period_values_seen"].add(vl)
                if any(s in vl for s in STATUS_TERMS):
                    signals["has_status_vals"] = True
                if any(s in vl for s in BS_SECTION_TERMS):
                    signals["has_bs_sections"] = True

    signals["numeric_col_count"] = len(signals["numeric_cols"])
    signals["text_col_count"]    = len(signals["text_cols"])
    signals["numeric_cols"]      = list(signals["numeric_cols"])
    signals["text_cols"]         = list(signals["text_cols"])
    signals["period_values_seen"]= list(signals["period_values_seen"])
    return signals


# ─────────────────────────────────────────────────────────────────────────────
# TABLE TYPE CLASSIFIER
# ─────────────────────────────────────────────────────────────────────────────

TABLE_DEFINITIONS: Dict[str, Dict] = {
    "INCOME_STATEMENT": {
        "required": [
            ["revenue"],
            ["net_profit", "ebit", "ebitda", "gross_profit", "operating_exp"],
        ],
        "boosted":   ["cogs", "tax_exp", "depreciation", "interest_exp", "quarter", "year"],
        "penalised": ["product", "sku", "department", "budget", "actual",
                      "variance", "bs_item", "bs_amount", "equity", "cf_net"],
        "data_boost": {"has_quarter_vals": 3, "has_negative": 2, "has_monetary_vals": 2},
        "base": 10,
    },
    "PRODUCT_SALES": {
        "required": [
            ["product", "sku", "category"],
            ["revenue", "sales", "cost", "profit"],
        ],
        "boosted":   ["margin_pct", "quantity", "unit_price", "region", "quarter",
                      "sales_person", "customer"],
        "penalised": ["net_profit", "ebit", "ebitda", "department", "budget",
                      "actual", "variance", "bs_item", "equity", "cf_net"],
        "data_boost": {"has_quarter_vals": 2, "has_monetary_vals": 1},
        "base": 10,
    },
    "BUDGET_VARIANCE": {
        "required": [
            ["department"],
            ["budget", "actual"],
        ],
        "boosted":   ["variance", "variance_pct", "status", "quarter"],
        "penalised": ["product", "sku", "net_profit", "ebit", "revenue",
                      "bs_item", "equity", "cf_net"],
        "data_boost": {"has_status_vals": 5, "has_quarter_vals": 2, "has_monetary_vals": 1},
        "base": 10,
    },
    "BALANCE_SHEET": {
        "required": [
            ["bs_item", "current_assets", "fixed_assets", "total_assets",
             "current_liab", "total_liab", "equity",
             "receivables", "payables", "cash_equiv",
             "retained_earn", "share_capital"],
            ["bs_amount", "bs_category", "total_assets", "total_liab"],
        ],
        "boosted":   ["date", "total_assets", "total_liab", "long_term_liab",
                      "inventory", "debt"],
        "penalised": ["revenue", "quarter", "product", "department",
                      "budget", "variance", "cf_net"],
        "data_boost": {"has_bs_sections": 5, "has_monetary_vals": 2},
        "base": 10,
    },
    "CASH_FLOW": {
        "required": [
            ["cf_operating", "cf_investing", "cf_financing", "cf_net", "cf_item"],
        ],
        "boosted":   ["date", "year"],
        "penalised": ["product", "sku", "department", "budget", "variance",
                      "bs_item", "equity"],
        "data_boost": {"has_negative": 2, "has_monetary_vals": 1},
        "base": 10,
    },
    "EXPENSE_BREAKDOWN": {
        "required": [
            ["department", "category"],
            ["cost", "operating_exp", "bs_amount"],
        ],
        "boosted":   ["quarter", "year"],
        "penalised": ["revenue", "product", "sku", "budget", "actual",
                      "variance", "equity", "cf_net"],
        "data_boost": {"has_monetary_vals": 1},
        "base": 8,
    },
}


def classify_table_type(
    fingerprint: Dict[str, List[str]],
    data_signals: Dict,
) -> Tuple[str, Dict[str, float]]:
    """Score every table type and return (best_type, all_scores)."""
    scores: Dict[str, float] = {}

    for ttype, defn in TABLE_DEFINITIONS.items():
        score = 0.0

        all_req_met = True
        for group in defn.get("required", []):
            if not any(ft in fingerprint for ft in group):
                all_req_met = False
                break
        if not all_req_met:
            scores[ttype] = 0.0
            continue

        score += defn["base"]

        for ft in defn.get("boosted", []):
            if ft in fingerprint:
                score += 3.0

        for ft in defn.get("penalised", []):
            if ft in fingerprint:
                score -= 5.0

        for sig, bonus in defn.get("data_boost", {}).items():
            if data_signals.get(sig):
                score += bonus

        scores[ttype] = max(score, 0.0)

    if not scores or max(scores.values()) == 0:
        return "UNKNOWN", scores

    best = max(scores, key=lambda k: scores[k])
    return best, scores


# ─────────────────────────────────────────────────────────────────────────────
# HEADER DETECTOR — finds the true header row
# ─────────────────────────────────────────────────────────────────────────────

def detect_header_row(df: pd.DataFrame) -> int:
    """
    Find the row index that most likely contains column headers.
    Checks up to first 20 rows.
    """
    best_row   = 0
    best_score = -1

    for i in range(min(20, len(df) - 2)):
        row       = df.iloc[i]
        non_empty = row.notna().sum()
        if non_empty < 2:
            continue

        text_count    = sum(1 for v in row if isinstance(v, str) and str(v).strip())
        numeric_count = sum(1 for v in row if isinstance(v, (int, float)) and not pd.isna(v))

        fin_hits = 0
        for v in row:
            if isinstance(v, str):
                vl = v.lower().strip()
                for pats in COL_VOCAB.values():
                    if any(pat in vl for pat in pats):
                        fin_hits += 1
                        break

        consistency = 0
        if i + 1 < len(df):
            next_row     = df.iloc[i + 1]
            next_numeric  = sum(1 for v in next_row if isinstance(v, (int, float)) and not pd.isna(v))
            next_nonempty = next_row.notna().sum()
            if next_nonempty >= non_empty - 1:
                consistency += 2
            if next_numeric >= 1:
                consistency += 2

        numeric_penalty = numeric_count if fin_hits < 3 else 0

        score = text_count * 2 + fin_hits * 4 - numeric_penalty + consistency
        if score > best_score and non_empty >= 2:
            best_score = score
            best_row   = i

    return best_row


# ─────────────────────────────────────────────────────────────────────────────
# COLUMN NAME NORMALIZER
# ─────────────────────────────────────────────────────────────────────────────

def normalize_col(col: str) -> str:
    """
    Map raw column name → primary semantic field type, or cleaned raw name.

    Priority order matters — more specific types must come before generic ones.
    Key fixes in v3:
      • serial_no, sales_person, customer checked FIRST (before sales/revenue)
      • margin_pct checked BEFORE gross_profit so "Gross Profit %" wins correctly
      • category checked BEFORE bs_category so "Product Category" wins correctly
    """
    col_l = str(col).lower().strip()
    col_l = re.sub(r'\s*\(.*?\)', '', col_l).strip()

    priority = [
        # ── Serial / identity columns (catch first to avoid mis-matching) ─────
        "serial_no",
        # ── Person / party columns (must beat sales/revenue) ──────────────────
        "sales_person", "customer",
        # ── Specific P&L — ordered most-specific first ────────────────────────
        "net_profit", "ebit", "ebitda", "cogs", "operating_exp",
        "tax_exp", "depreciation", "interest_exp",
        # ── Percentage columns BEFORE their absolute counterparts ─────────────
        "margin_pct", "variance_pct",
        # ── Absolute P&L amounts ───────────────────────────────────────────────
        "gross_profit",
        # ── Budget / variance ──────────────────────────────────────────────────
        "variance", "status", "actual", "budget",
        # ── Balance sheet ──────────────────────────────────────────────────────
        "total_assets", "total_liab", "current_assets", "fixed_assets",
        "current_liab", "long_term_liab", "inventory", "receivables",
        "payables", "cash_equiv", "debt", "retained_earn", "share_capital", "equity",
        "bs_amount", "bs_item",
        # ── category BEFORE bs_category so "Product Category" maps correctly ──
        "category", "bs_category",
        # ── Cash flow ──────────────────────────────────────────────────────────
        "cf_net", "cf_operating", "cf_investing", "cf_financing", "cf_item",
        # ── Payment / receivables ──────────────────────────────────────────────
        "total_received", "payment_days", "credit_limit", "credit_term",
        # ── Sales / product / generic ──────────────────────────────────────────
        "revenue", "sales", "profit", "cost",
        "product", "sku", "city", "region",
        "department", "quantity", "unit_price",
        # ── Time ───────────────────────────────────────────────────────────────
        "quarter", "month", "year", "date",
    ]

    for field in priority:
        for pat in COL_VOCAB.get(field, []):
            if pat == col_l or (len(pat) > 3 and pat in col_l):
                return field

    clean = re.sub(r"[^a-z0-9_]", "_", col_l)
    clean = re.sub(r"_+", "_", clean).strip("_")
    return clean if clean else "col"


# ─────────────────────────────────────────────────────────────────────────────
# SAFE VALUE CONVERTER
# ─────────────────────────────────────────────────────────────────────────────

def safe_val(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, float) and (np.isnan(v) or np.isinf(v)):
        return None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return float(v)
    if isinstance(v, pd.Timestamp):
        return str(v.date())
    if isinstance(v, str):
        s = v.strip()
        if s in ("", "nan", "None", "NaN", "N/A", "n/a", "#N/A"):
            return None
        return s
    return v


# ─────────────────────────────────────────────────────────────────────────────
# REPEATED HEADER ROW DETECTOR
# ─────────────────────────────────────────────────────────────────────────────

def is_repeated_header_row(raw_vals: list, deduped_raw: list) -> bool:
    text_raw = [str(v).strip().lower() if isinstance(v, str) else "" for v in raw_vals]
    hdr_raw  = [str(h).strip().lower() for h in deduped_raw]
    if not text_raw:
        return False
    non_empty_text = sum(1 for t in text_raw if t)
    if non_empty_text < 2:
        return False
    matches = sum(
        1 for t, h in zip(text_raw, hdr_raw)
        if t and h and (t == h or t.replace(" ", "_") == h.replace(" ", "_"))
    )
    return matches >= max(2, non_empty_text * 0.6)


# ─────────────────────────────────────────────────────────────────────────────
# WIDE-FORMAT TABLE DETECTOR
# ─────────────────────────────────────────────────────────────────────────────

def _detect_wide_format(
    rows: List[dict],
    final_cols: List[str],
    deduped_raw: List[str],
) -> Tuple[bool, List[str], Dict[str, List[str]]]:
    if not rows or len(final_cols) < 3:
        return False, [], {}

    text_col: Optional[str] = None
    period_cols: List[str]  = []

    # Build a mapping from normalized col name → original (raw) col name so we
    # can check raw headers for bare year numbers like "2020", "2021".
    norm_to_raw_pre: Dict[str, str] = {}
    for fc, rc in zip(final_cols, deduped_raw):
        if fc not in norm_to_raw_pre:
            norm_to_raw_pre[fc] = rc

    _YEAR_RE = re.compile(r'^(19|20)\d{2}$')  # 4-digit year column header

    for fc in final_cols:
        col_vals = [r["data"].get(fc) for r in rows if r["data"].get(fc) is not None]
        if not col_vals:
            continue
        n_text = sum(1 for v in col_vals if isinstance(v, str))
        n_num  = sum(1 for v in col_vals if isinstance(v, (int, float)))
        total  = len(col_vals)

        if n_text >= max(2, total * 0.5) and n_num == 0:
            if text_col is None:
                text_col = fc
        elif n_num > n_text:
            raw_header = norm_to_raw_pre.get(fc, fc)
            is_named_period = (
                fc.startswith("quarter") or fc.startswith("month")
                or fc.startswith("year")  or fc.startswith("date")
                or re.match(r'^(q\d|fy)', fc, re.I)
            )
            # Also detect bare year numbers used as column headers (2020, 2021…)
            is_year_header = bool(_YEAR_RE.match(str(raw_header).strip()))
            if is_named_period or is_year_header:
                period_cols.append(fc)

    if text_col is None or len(period_cols) < 2:
        return False, [], {}

    text_vals = [
        str(r["data"].get(text_col, "")).lower().strip()
        for r in rows
        if r["data"].get(text_col) is not None
    ]
    fin_hits: Set[str] = set()
    for val in text_vals:
        if not val:
            continue
        for ft, pats in COL_VOCAB.items():
            if ft in ("quarter", "month", "year", "date"):
                continue
            if any(pat == val or (len(pat) > 3 and pat in val) for pat in pats):
                fin_hits.add(ft)

    if len(fin_hits) < 2:
        return False, [], {}

    supp_fp: Dict[str, List[str]] = {}
    for ft in fin_hits:
        supp_fp.setdefault(ft, []).append(text_col)

    norm_to_raw: Dict[str, str] = {}
    for fc, rc in zip(final_cols, deduped_raw):
        if fc not in norm_to_raw:
            norm_to_raw[fc] = rc
    period_col_order = [norm_to_raw.get(fc, fc) for fc in period_cols]

    return True, period_col_order, supp_fp


# ─────────────────────────────────────────────────────────────────────────────
# SHEET PARSER
# ─────────────────────────────────────────────────────────────────────────────

def parse_sheet(sheet_df: pd.DataFrame, sheet_name: str) -> dict:
    empty_result = {
        "sheet_name":       sheet_name,
        "rows":             [],
        "columns":          [],
        "original_columns": [],
        "table_type":       "UNKNOWN",
        "type_scores":      {},
        "col_fingerprint":  {},
        "data_signals":     {},
        "subtotal_row_ids": [],
        "is_wide_format":   False,
        "period_col_order": [],
    }

    if sheet_df.empty or sheet_df.shape[0] < 2:
        return empty_result

    hdr_idx    = detect_header_row(sheet_df)
    header_row = sheet_df.iloc[hdr_idx]

    raw_cols = []
    for i, v in enumerate(header_row):
        if pd.notna(v) and str(v).strip() not in ("", "nan"):
            raw_cols.append(str(v).strip())
        else:
            raw_cols.append(f"col_{i}")

    seen_raw: Dict[str, int] = {}
    deduped_raw = []
    for c in raw_cols:
        if c not in seen_raw:
            seen_raw[c] = 0
            deduped_raw.append(c)
        else:
            seen_raw[c] += 1
            deduped_raw.append(f"{c}_{seen_raw[c]}")

    norm_cols = [normalize_col(c) for c in deduped_raw]

    seen_norm: Dict[str, int] = {}
    final_cols = []
    for c in norm_cols:
        if c not in seen_norm:
            seen_norm[c] = 0
            final_cols.append(c)
        else:
            seen_norm[c] += 1
            final_cols.append(f"{c}_{seen_norm[c]}")

    data_df = sheet_df.iloc[hdr_idx + 1:].reset_index(drop=True)
    data_df.columns = range(len(data_df.columns))

    rows             = []
    subtotal_row_ids = []
    actual_row_num   = hdr_idx + 2

    for idx in range(len(data_df)):
        raw_row  = data_df.iloc[idx]
        raw_vals = [safe_val(v) for v in raw_row]

        non_null = sum(1 for v in raw_vals if v is not None)
        if non_null == 0:
            actual_row_num += 1
            continue

        if is_repeated_header_row(raw_vals, deduped_raw):
            actual_row_num += 1
            continue

        row_data  = {}
        orig_data = {}
        for j, (fc, rc) in enumerate(zip(final_cols, deduped_raw)):
            if j < len(raw_vals):
                row_data[fc]  = raw_vals[j]
                orig_data[rc] = raw_vals[j]

        row_id = f"{sheet_name.replace(' ', '_')}_{actual_row_num}"
        is_sub = is_subtotal_row(raw_vals)
        if is_sub:
            subtotal_row_ids.append(row_id)

        rows.append({
            "row_id":             row_id,
            "sheet_name":         sheet_name,
            "row_number":         actual_row_num,
            "is_subtotal":        is_sub,
            "data":               row_data,
            "original_data":      orig_data,
            "original_columns":   deduped_raw,
            "normalized_columns": final_cols,
        })
        actual_row_num += 1

    fingerprint  = build_col_fingerprint(final_cols)
    data_signals = scan_data_signals(rows)

    is_wide, period_col_order, supp_fp = _detect_wide_format(rows, final_cols, deduped_raw)
    if is_wide:
        for ft, cols in supp_fp.items():
            for col in cols:
                fingerprint.setdefault(ft, [])
                if col not in fingerprint[ft]:
                    fingerprint[ft].append(col)

    ttype, tscores = classify_table_type(fingerprint, data_signals)

    return {
        "sheet_name":       sheet_name,
        "table_type":       ttype,
        "type_scores":      tscores,
        "col_fingerprint":  fingerprint,
        "data_signals":     data_signals,
        "columns":          final_cols,
        "original_columns": deduped_raw,
        "rows":             rows,
        "header_row":       hdr_idx,
        "subtotal_row_ids": subtotal_row_ids,
        "is_wide_format":   is_wide,
        "period_col_order": period_col_order,
    }


# ─────────────────────────────────────────────────────────────────────────────
# WORKBOOK PARSER
# ─────────────────────────────────────────────────────────────────────────────

def parse_workbook(file_bytes, filename: str) -> dict:
    import io
    if isinstance(file_bytes, (bytes, bytearray)):
        file_bytes = io.BytesIO(file_bytes)
    try:
        xl = pd.ExcelFile(file_bytes, engine="openpyxl")
    except Exception:
        try:
            xl = pd.ExcelFile(file_bytes, engine="xlrd")
        except Exception as e:
            raise ValueError(f"Cannot open workbook: {e}")

    parsed_sheets = []
    for sheet_name in xl.sheet_names:
        try:
            df     = xl.parse(sheet_name, header=None)
            parsed = parse_sheet(df, sheet_name)
            parsed_sheets.append(parsed)
        except Exception as e:
            parsed_sheets.append({
                "sheet_name":       sheet_name,
                "table_type":       "ERROR",
                "type_scores":      {},
                "col_fingerprint":  {},
                "data_signals":     {},
                "columns":          [],
                "original_columns": [],
                "rows":             [],
                "subtotal_row_ids": [],
                "is_wide_format":   False,
                "period_col_order": [],
                "error":            str(e),
            })

    return {
        "filename":    filename,
        "sheets":      parsed_sheets,
        "sheet_names": xl.sheet_names,
    }
