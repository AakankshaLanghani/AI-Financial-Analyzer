# ICS AI Financial Analyzer

> AI-powered financial data analysis platform — upload Excel data, ask questions in plain English, and generate professional PDF reports instantly.

---

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Architecture](#architecture)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Getting Started](#getting-started)
  - [Prerequisites](#prerequisites)
  - [Backend Setup](#backend-setup)
  - [Frontend Setup](#frontend-setup)
- [Environment Variables](#environment-variables)
- [API Reference](#api-reference)
- [Deployment](#deployment)
- [Data Format](#data-format)
- [How It Works](#how-it-works)

---

## Overview

ICS AI Financial Analyzer is a full-stack web application that allows business users to upload monthly sales/financial data (Excel), interact with it via a natural language chatbot, and download auto-generated PDF analytics reports — all without writing a single line of code or formula.

---

## Features

- **Excel Upload** — Supports `.xlsx` files with auto-detection of column types regardless of header casing or spacing
- **AI Chatbot** — Ask financial questions in plain English (e.g. *"which city had the highest GP%?"*, *"how many customers are overdue?"*)
- **PDF Report Generation** — Professionally formatted multi-page PDF with KPIs, charts, and customer detail tables
- **Accurate Computations** — All analytics computed deterministically in Python (pandas); LLM only writes the explanation, never the numbers
- **JWT Authentication** — Secure login with configurable credentials
- **Flexible Column Detection** — Fuzzy matching handles variations in header names across different monthly files

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                   React Frontend                     │
│          (Upload → Chat → Download Report)          │
└──────────────────────┬──────────────────────────────┘
                       │ HTTPS / REST
┌──────────────────────▼──────────────────────────────┐
│                  FastAPI Backend                     │
│                                                      │
│  /upload  →  parser.py       (Excel → parsed dict)  │
│  /ask     →  query_planner   (NL → QueryPlan)        │
│           →  analytics_engine (QueryPlan → Result)   │
│           →  llm.py          (Result → Explanation)  │
│  /report  →  report_engine   (parsed → PDF)          │
└──────────────────────────────────────────────────────┘
```

**Key design principle:** The LLM never touches raw data or computes numbers. All aggregations, rankings, and percentage calculations are done in Python. The LLM only generates business-language explanations from pre-computed results.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | React 18, CSS Modules |
| Backend | FastAPI, Python 3.11 |
| Data Processing | Pandas, OpenPyXL |
| PDF Generation | ReportLab, Matplotlib |
| AI / NLP | OpenAI GPT-4o-mini |
| Authentication | JWT (PyJWT) |
| Frontend Hosting | Vercel |
| Backend Hosting | Hugging Face Spaces |

---

## Project Structure

```
ICS AI Financial Analyzer/
│
├── files/
│   ├── ics-backend-v2/
│   │   └── ics-backend-v2/
│   │       ├── main.py               # FastAPI app, routes, session management
│   │       ├── parser.py             # Excel parser + column fingerprinting
│   │       ├── query_planner.py      # NL question → QueryPlan dataclass
│   │       ├── analytics_engine.py   # Deterministic pandas computations
│   │       ├── report_engine.py      # PDF report builder (ReportLab)
│   │       ├── kpi_engine.py         # Weighted ratio registry (GP%, margins)
│   │       ├── llm.py                # LLM explanation layer (OpenAI)
│   │       ├── validation_engine.py  # Finance-grade sanity checks
│   │       ├── overview_engine.py    # General data overview computations
│   │       ├── requirements.txt
│   │       └── Dockerfile
│   │
│   └── ics-frontend-v2/
│       └── ics-frontend-v2/
│           ├── src/
│           │   ├── App.js            # Main app component
│           │   └── ...
│           └── package.json
│
└── hf-space/                         # Hugging Face Spaces deployment copy
    ├── main.py
    ├── parser.py
    ├── query_planner.py
    ├── analytics_engine.py
    ├── report_engine.py
    ├── kpi_engine.py
    ├── llm.py
    └── requirements.txt
```

---

## Getting Started

### Prerequisites

- Python 3.11+
- Node.js 18+
- An OpenAI API key

### Backend Setup

```powershell
# Navigate to backend directory
cd "files\ics-backend-v2\ics-backend-v2"

# Create and activate virtual environment
py -3.11 -m venv venv
.\venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt

# Create .env file (see Environment Variables section)
# Then start the server
uvicorn main:app --reload --port 8000
```

Backend will be running at `http://localhost:8000`

### Frontend Setup

```powershell
# Open a second terminal
cd "files\ics-frontend-v2\ics-frontend-v2"

# Install dependencies
npm install

# Start the development server
npm start
```

Frontend will open at `http://localhost:3000`

---

## Environment Variables

Create a `.env` file in `files/ics-backend-v2/ics-backend-v2/`:

```env
# OpenAI — required for chatbot explanations
OPENAI_API_KEY=sk-...

# App login credentials
APP_EMAIL=your@email.com
APP_PASSWORD=YourPassword

# JWT secret — use a long random string in production
JWT_SECRET=your-secret-key-here
JWT_EXPIRE_HOURS=8

# Optional
UPLOAD_MAX_MB=50
MAX_SESSIONS=50
```

> **Never commit `.env` to version control.** It is listed in `.gitignore`.

---

## API Reference

All endpoints except `/health` require a Bearer token obtained from `/login`.

### `POST /login`
```json
{ "email": "demo@example.com", "password": "password" }
```
Returns: `{ "access_token": "...", "token_type": "bearer" }`

---

### `POST /upload`
Upload an Excel file. Returns a `session_id` used for all subsequent requests.

**Form data:** `file` (`.xlsx`)

Returns:
```json
{
  "session_id": "abc123",
  "sheets": [{ "name": "Sheet1", "rows": 319, "table_type": "PRODUCT_SALES" }],
  "total_rows": 319
}
```

---

### `POST /ask`
Ask a natural language question about the uploaded data.

```json
{ "session_id": "abc123", "question": "which city had the highest sales?" }
```

Returns:
```json
{
  "answer": "1. Quetta: 15,919,407 (15.92M)",
  "explanation": "Quetta leads in sales, indicating strong market demand in that region.",
  "caveats": "",
  "row_count": 319,
  "formula": "SUM(Sale) grouped by city"
}
```

---

### `POST /report`
Generate and download a PDF analytics report.

```json
{ "session_id": "abc123", "question": "" }
```

Returns: PDF binary (`Content-Disposition: attachment; filename=report.pdf`)

---

## Deployment

### Frontend — Vercel

1. Push the `ics-frontend-v2` directory to your repository
2. Connect the repo to [vercel.com](https://vercel.com)
3. Set the root directory to `files/ics-frontend-v2/ics-frontend-v2`
4. Set environment variable: `REACT_APP_API_URL=https://your-hf-space.hf.space`
5. Deploy

### Backend — Hugging Face Spaces

1. Go to [huggingface.co/spaces](https://huggingface.co/spaces) → Create Space → Docker
2. Get your User Access Token from [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) (Write access)
3. Authenticate and push:

```powershell
cd hf-space
git remote set-url origin https://YOUR_HF_USERNAME:YOUR_HF_TOKEN@huggingface.co/spaces/YOUR_HF_USERNAME/YOUR_SPACE_NAME
git add .
git commit -m "deploy: updated backend"
git push origin main
```

4. Add secrets in the Space settings:
   - `OPENAI_API_KEY`
   - `APP_EMAIL`
   - `APP_PASSWORD`
   - `JWT_SECRET`

---

## Data Format

The application is designed for monthly sales/receivables data. Expected columns (header names are flexible — the parser uses fuzzy matching):

| Column | Description |
|---|---|
| `Customer Name` | Customer identifier |
| `Credit Term` | Payment terms (e.g. "CREDIT 60 DAYS") |
| `Credit Limit` | Approved credit ceiling |
| `Business-Type` | Category (Wholesaler, Distributor, Hospital, Retail Pharmacy) |
| `City` | Customer city |
| `Region` | Sales region |
| `Sale` | Total sales amount |
| `GP` | Gross profit amount |
| `GP%` | Gross profit percentage |
| `Total Received` | Amount collected |
| `Received_Before Due Date` | Collections received before due date |
| `Received_After Due Date` | Collections received after due date |
| `Payment Status` | On Time / Delayed / Overdue / Partial |
| `Avg Payment Days` | Average days taken to pay |

> Headers must stay consistent across months. Data rows can change freely.

---

## How It Works

### Column Detection
The parser normalises column headers (strips spaces, hyphens, underscores, lowercases) and matches them against a vocabulary registry. This means `"avg payment days"`, `"Avg Payment Days"`, and `"AVG_PAYMENT_DAYS"` all resolve to the same internal type.

### Query Pipeline
```
User question
    → query_planner.py   detects metric, operation, group_by, filters
    → analytics_engine   executes pandas computation deterministically
    → validation_engine  checks for data anomalies
    → llm.py             GPT-4o-mini writes explanation (numbers come from pandas, not LLM)
    → JSON response
```

### GP% Accuracy
Gross profit percentages are **always** computed as `SUM(GP) / SUM(Sale) × 100`. The GP% column in Excel is never summed or averaged directly — this prevents the common mistake of averaging percentages across unequal sales bases.

### PDF Report Sections
1. KPI Summary (Total Sales, GP, GP%, Collections, Avg Payment Days)
2. Sales by City / Region / Business Type
3. Top & Bottom 10 Customers by Sales
4. Top & Bottom 10 Customers by GP
5. Profitability Analysis (GP% with amounts)
6. Receivables & Collections (before/after due date)
7. Customer Detail Table (Top 10 by Sales with full metrics)
8. AI Insights

---

## License

Internal use only — ICS Group. Not for public distribution.