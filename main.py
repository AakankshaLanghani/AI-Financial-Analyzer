"""
ICS AI Financial Analyzer — API Server v5
==========================================
New deterministic pipeline:

  Excel Upload
    → parser.py          (structure detection, column classification)
    → query_planner.py   (NL → QueryPlan — no analytics here)
    → analytics_engine.py (deterministic pandas execution)
    → validation_engine.py (finance-grade sanity checks)
    → llm.py             (explanation narration only — no arithmetic)
    → JSON response

The LLM never sees raw rows. It receives only the verified,
pre-computed result and writes 1–2 sentences of business insight.
"""

import uuid
import os
import time
from collections import OrderedDict
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel
from typing import Optional
import io
from dotenv import load_dotenv

from parser import parse_workbook
from query_planner import build_query_plan
from analytics_engine import execute_plan
from validation_engine import validate_result
from llm import generate_explanation
from report_engine import generate_report

load_dotenv()

OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")

# ── Upload safety limits ──────────────────────────────────────────────────────
_MAX_UPLOAD_BYTES: int = int(os.getenv("UPLOAD_MAX_MB", "50")) * 1_048_576

# ── Session LRU store ─────────────────────────────────────────────────────────
_MAX_SESSIONS: int = int(os.getenv("MAX_SESSIONS", "50"))


class _SessionStore:
    """LRU in-memory session cache (single-worker)."""

    def __init__(self, max_size: int = 50):
        self._store: OrderedDict = OrderedDict()
        self._max = max_size

    def get(self, key: str) -> Optional[dict]:
        if key not in self._store:
            return None
        self._store.move_to_end(key)
        self._store[key]["_last_access"] = time.monotonic()
        return self._store[key]

    def set(self, key: str, value: dict) -> None:
        if key in self._store:
            self._store.move_to_end(key)
        self._store[key] = value
        self._store[key]["_last_access"] = time.monotonic()
        while len(self._store) > self._max:
            self._store.popitem(last=False)

    def __contains__(self, key: str) -> bool:
        return key in self._store


sessions = _SessionStore(max_size=_MAX_SESSIONS)

app = FastAPI(title="ICS AI Financial Analyzer", version="5.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class AskRequest(BaseModel):
    session_id: str
    question:   str


# ─────────────────────────────────────────────────────────────────────────────
# HEALTH
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/")
def health():
    return {
        "status":   "ok",
        "service":  "ICS AI Financial Analyzer v5",
        "pipeline": [
            "parser → query_planner → analytics_engine → validation_engine → llm"
        ],
        "limits": {
            "max_upload_mb": _MAX_UPLOAD_BYTES // 1_048_576,
            "max_sessions":  _MAX_SESSIONS,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# UPLOAD
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    fname = (file.filename or "").strip()
    if not fname.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="Only .xlsx and .xls files are supported.")

    contents = await file.read()
    if len(contents) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if len(contents) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"File too large ({len(contents) // 1_048_576} MB). "
                f"Maximum is {_MAX_UPLOAD_BYTES // 1_048_576} MB."
            ),
        )

    try:
        parsed = parse_workbook(io.BytesIO(contents), fname)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse workbook: {e}")

    usable = [s for s in parsed.get("sheets", []) if s.get("rows")]
    if not usable:
        raise HTTPException(
            status_code=422,
            detail=(
                "No usable data found in this workbook. "
                "Please ensure the file contains at least one sheet "
                "with tabular data (headers + data rows)."
            ),
        )

    session_id = str(uuid.uuid4())
    sessions.set(session_id, {"filename": fname, "parsed": parsed})

    sheets_summary = [
        {
            "name":       s["sheet_name"],
            "rows":       len(s["rows"]),
            "columns":    s["original_columns"],
            "table_type": s.get("table_type", "UNKNOWN"),
        }
        for s in parsed["sheets"]
    ]

    return {
        "session_id": session_id,
        "filename":   fname,
        "sheets":     sheets_summary,
        "total_rows": sum(len(s["rows"]) for s in parsed["sheets"]),
        "message":    "Workbook parsed successfully.",
    }


# ─────────────────────────────────────────────────────────────────────────────
# ASK  — new deterministic pipeline
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/ask")
async def ask_question(req: AskRequest):
    session = sessions.get(req.session_id)
    if not session:
        raise HTTPException(
            status_code=404,
            detail="Session not found or expired. Please upload the file again.",
        )

    if not OPENAI_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="OPENAI_API_KEY is not configured in the backend .env file.",
        )

    question = req.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    parsed = session["parsed"]

    # ── Step 1: Build deterministic query plan ────────────────────────────────
    plan = build_query_plan(question, parsed)

    # ── Step 2: Execute analytics deterministically in Python ─────────────────
    result = execute_plan(plan, parsed)

    # ── Step 3: Validate the result (finance-grade sanity checks) ─────────────
    validation = validate_result(result, plan)

    # ── Step 4: Hard-fail if no data found ───────────────────────────────────
    if result.row_count == 0 and result.error:
        return {
            "answer": (
                "No relevant data found for this question. "
                f"Detail: {result.error}"
            ),
            "explanation":       "",
            "caveats":           "",
            "row_count":         0,
            "formula":           "",
            "validation_passed": False,
            # Diagnostic info for developers
            "_debug": {
                "plan":       {
                    "metric":          plan.metric,
                    "operation":       plan.operation,
                    "group_by":        plan.group_by,
                    "entity_filters":  plan.entity_filters,
                    "time_filters":    plan.time_filters,
                    "top_n":           plan.top_n,
                    "requires_weighted_pct": plan.requires_weighted_pct,
                    "target_table_types":    plan.target_table_types,
                },
                "validation": {
                    "summary":  validation.summary,
                    "warnings": validation.warnings,
                    "errors":   validation.errors,
                },
            },
        }

    # ── Step 5: LLM generates explanation only (no arithmetic) ───────────────
    llm_response = generate_explanation(question, plan, result, validation, OPENAI_API_KEY)

    # ── Attach debug plan info for transparency ───────────────────────────────
    llm_response["_debug"] = {
        "plan": {
            "metric":                plan.metric,
            "operation":             plan.operation,
            "group_by":              plan.group_by,
            "entity_filters":        plan.entity_filters,
            "time_filters":          plan.time_filters,
            "top_n":                 plan.top_n,
            "bottom_n":              plan.bottom_n,
            "requires_weighted_pct": plan.requires_weighted_pct,
            "numerator_col_type":    plan.numerator_col_type,
            "denominator_col_type":  plan.denominator_col_type,
            "target_table_types":    plan.target_table_types,
        },
        "result": {
            "sheet":        result.sheet_name,
            "row_count":    result.row_count,
            "formula":      result.formula,
            "columns_used": result.columns_used,
            "warnings":     result.warnings,
        },
        "validation": {
            "summary":  validation.summary,
            "warnings": validation.warnings,
            "errors":   validation.errors,
        },
    }

    return llm_response


# ─────────────────────────────────────────────────────────────────────────────
# REPORT  — auto-generated PDF analytics report
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/report")
async def generate_pdf_report(req: AskRequest):
    """
    Generate a full PDF analytics report for an uploaded workbook.
    Uses session_id to retrieve parsed data (re-uses the /upload session).
    The 'question' field is ignored — pass an empty string or any value.
    Returns a PDF binary with Content-Disposition: attachment.
    """
    session = sessions.get(req.session_id)
    if not session:
        raise HTTPException(
            status_code=404,
            detail="Session not found or expired. Please upload the file again.",
        )

    parsed   = session["parsed"]
    filename = session["filename"]

    try:
        pdf_bytes = generate_report(parsed, filename, OPENAI_API_KEY)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Report generation failed: {e}")

    safe_name = filename.replace(" ", "_").replace(".xlsx", "").replace(".xls", "")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}_report.pdf"',
            "Content-Length": str(len(pdf_bytes)),
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# SESSION INFO
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/session/{session_id}")
def get_session_info(session_id: str):
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found or expired.")
    parsed = session["parsed"]
    return {
        "filename": session["filename"],
        "sheets": [
            {
                "name":       s["sheet_name"],
                "rows":       len(s["rows"]),
                "columns":    s["original_columns"],
                "table_type": s.get("table_type", "UNKNOWN"),
            }
            for s in parsed["sheets"]
        ],
    }
