# AI Financial Analyzer

A full-stack AI-powered financial analysis tool that lets you upload Excel workbooks and ask financial questions in plain English. Every answer is grounded strictly in your data — no guesses, no hallucinations.

---

## How It Works

1. **Upload** any `.xlsx` or `.xls` workbook
2. The backend **auto-detects** tables, sheets, and column types
3. **Ask questions** in plain English (e.g. *"Which product had the highest margin?"*)
4. Get **cited answers** backed by deterministic analytics — the LLM only narrates, never does arithmetic

### Pipeline

```
Excel Upload
  → parser.py            (structure detection, column classification)
  → query_planner.py     (NL → QueryPlan)
  → analytics_engine.py  (deterministic pandas execution)
  → validation_engine.py (finance-grade sanity checks)
  → llm.py               (explanation narration only — no arithmetic)
  → JSON response
```

---

## Project Structure

```
├── backend/
│   ├── main.py               # FastAPI server (v5)
│   ├── parser.py             # Workbook structure detection
│   ├── query_planner.py      # Natural language → QueryPlan
│   ├── analytics_engine.py   # Deterministic pandas execution
│   ├── validation_engine.py  # Finance-grade sanity checks
│   ├── llm.py                # OpenAI narration (no arithmetic)
│   ├── kpi_engine.py         # KPI computations
│   ├── retriever.py          # Data retrieval utilities
│   ├── report_engine.py      # PDF report generation
│   └── requirements.txt
│
└── frontend/
    ├── src/
    │   ├── App.jsx
    │   └── components/
    │       ├── UploadPanel.jsx
    │       ├── Messages.jsx
    │       ├── ApiKeyModal.jsx
    │       └── Logo.jsx
    ├── public/
    └── package.json
```

---

## Getting Started

### Prerequisites

- Python 3.10+
- Node.js 18+
- An OpenAI API key

---

### Backend Setup

```bash
cd backend

# Create and activate a virtual environment
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS/Linux

# Install dependencies
pip install -r requirements.txt

# Create your .env file
echo OPENAI_API_KEY=your_key_here > .env

# Start the server
uvicorn main:app --reload --port 8000
```

The API will be available at `http://localhost:8000`.

---

### Frontend Setup

```bash
cd frontend

npm install
npm start
```

The app will open at `http://localhost:3000` and proxy API calls to `http://localhost:8000`.

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/` | Health check |
| `POST` | `/upload` | Upload an Excel workbook, returns `session_id` |
| `POST` | `/ask` | Ask a question against an uploaded workbook |
| `POST` | `/report` | Generate and download a full PDF analytics report |
| `GET` | `/session/{id}` | Get info about an active session |

### Upload limits
- Max file size: **50 MB** (configurable via `UPLOAD_MAX_MB` env var)
- Max concurrent sessions: **50** (LRU eviction, configurable via `MAX_SESSIONS`)

---

## Tech Stack

**Backend**
- FastAPI + Uvicorn
- Pandas + NumPy (deterministic analytics)
- OpenAI API (narration only)
- ReportLab (PDF generation)
- openpyxl / xlrd (Excel parsing)

**Frontend**
- React 18
- Axios
- Tailwind CSS

---

## Environment Variables

Create a `.env` file in the `backend/` directory:

```env
OPENAI_API_KEY=your_openai_api_key_here
UPLOAD_MAX_MB=50       # optional, default 50
MAX_SESSIONS=50        # optional, default 50
```

---

## Features

- **Zero hallucinations** — LLM never sees raw data rows; it only narrates pre-computed, validated results
- **Auto table detection** — automatically identifies financial table types (P&L, balance sheet, sales data, etc.)
- **Cited answers** — every response references the exact source rows used
- **PDF report generation** — one-click full analytics report for any uploaded workbook
- **Session management** — LRU in-memory session store supports multiple concurrent users
