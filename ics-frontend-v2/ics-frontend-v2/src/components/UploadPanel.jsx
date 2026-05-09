import React, { useRef, useState, useEffect } from 'react';

// ─────────────────────────────────────────────────────────────────────────────
// Dynamic question generator — reads actual column names + table type from
// the upload response and produces relevant, file-specific suggestions.
// Zero hardcoded domain values.
// ─────────────────────────────────────────────────────────────────────────────

const TABLE_TYPE_LABELS = {
  PRODUCT_SALES:     { label: 'Product Sales',      color: '#0e7490', bg: '#ecfeff' },
  INCOME_STATEMENT:  { label: 'Income Statement',   color: '#065f46', bg: '#ecfdf5' },
  BUDGET_VARIANCE:   { label: 'Budget Variance',    color: '#92400e', bg: '#fffbeb' },
  BALANCE_SHEET:     { label: 'Balance Sheet',      color: '#3730a3', bg: '#eef2ff' },
  CASH_FLOW:         { label: 'Cash Flow',          color: '#6d28d9', bg: '#f5f3ff' },
  EXPENSE_BREAKDOWN: { label: 'Expense Breakdown',  color: '#9d174d', bg: '#fdf2f8' },
  UNKNOWN:           { label: 'General Data',       color: '#6b7280', bg: '#f9fafb' },
};

// Map normalized column names to pretty display names
const COL_LABELS = {
  sales: 'sales', revenue: 'revenue', gross_profit: 'gross profit',
  margin_pct: 'gross profit %', net_profit: 'net profit', profit: 'profit',
  quantity: 'quantity', unit_price: 'unit price', cost: 'cost',
  budget: 'budget', actual: 'actual', variance: 'variance',
  ebit: 'EBIT', ebitda: 'EBITDA', cogs: 'COGS',
  total_assets: 'total assets', equity: 'equity', inventory: 'inventory',
  receivables: 'receivables', payables: 'payables', cash_equiv: 'cash',
  cf_net: 'net cash flow',
  product: 'product', category: 'category', region: 'city/region',
  department: 'department/division', sales_person: 'salesperson',
  customer: 'customer', sku: 'SKU',
};

function label(col) {
  const base = col.replace(/_\d+$/, '');
  return COL_LABELS[base] || col.replace(/_/g, ' ');
}

function generateQuestions(sheets) {
  if (!sheets || sheets.length === 0) return [];
  const questions = [];

  sheets.forEach(sheet => {
    const cols    = sheet.columns || [];
    const ttype   = sheet.table_type || 'UNKNOWN';
    const hasCol  = (key) => cols.some(c => c.replace(/_\d+$/, '') === key);

    // ── Money/value columns for totals ──────────────────────────────────────
    const valueCols = ['sales','revenue','gross_profit','net_profit','profit',
                       'cost','budget','actual','cf_net','total_assets'].filter(hasCol);
    const pctCols   = ['margin_pct','variance_pct'].filter(hasCol);
    const countCols = ['quantity'].filter(hasCol);
    const groupCols = ['product','category','region','department',
                       'sales_person','customer','sku'].filter(hasCol);
    const periodCols = ['quarter','month','year','date'].filter(hasCol);

    // Total / sum questions
    if (valueCols.length > 0) {
      questions.push(`What is the total ${label(valueCols[0])}?`);
    }
    if (countCols.length > 0 && groupCols.length > 0) {
      questions.push(`What is the total ${label(countCols[0])} sold?`);
    }

    // Highest / lowest by group
    if (groupCols.length > 0 && valueCols.length > 0) {
      questions.push(`Which ${label(groupCols[0])} had the highest ${label(valueCols[0])}?`);
    }
    if (groupCols.length > 0 && valueCols.length > 0) {
      questions.push(`Which ${label(groupCols[0])} had the lowest ${label(valueCols[0])}?`);
    }

    // Percentage-based ranking
    if (pctCols.length > 0 && groupCols.length > 0) {
      questions.push(`Which ${label(groupCols[0])} had the highest ${label(pctCols[0])}?`);
    }

    // Second group column (e.g. by region, by salesperson)
    if (groupCols.length > 1 && valueCols.length > 0) {
      questions.push(`Which ${label(groupCols[1])} had the highest ${label(valueCols[0])}?`);
    }

    // Trend / period question
    if (periodCols.length > 0 && valueCols.length > 0) {
      questions.push(`How did ${label(valueCols[0])} trend across ${label(periodCols[0])}s?`);
    }

    // Budget vs actual comparison
    if (hasCol('budget') && hasCol('actual')) {
      questions.push('Which departments are most over budget?');
      questions.push('Which departments are under budget?');
    }

    // Variance / status
    if (hasCol('variance') && groupCols.length > 0) {
      questions.push(`Which ${label(groupCols[0])} had the largest variance?`);
    }

    // Balance sheet specifics
    if (ttype === 'BALANCE_SHEET') {
      questions.push('What is the total assets value?');
      questions.push('What is the total equity?');
    }

    // Cash flow specifics
    if (ttype === 'CASH_FLOW') {
      questions.push('What is the net cash flow?');
      questions.push('What is the operating cash flow?');
    }

    // Income statement specifics
    if (ttype === 'INCOME_STATEMENT') {
      if (hasCol('revenue')) questions.push('What is the total revenue?');
      if (hasCol('net_profit')) questions.push('What is the net profit?');
      if (hasCol('cogs')) questions.push('What is the total cost of goods sold?');
    }

    // Multi-sheet: add sheet name prefix for context
    if (sheets.length > 1) {
      questions.forEach((q, i) => {
        if (!q.startsWith('(')) questions[i] = q;
      });
    }
  });

  // Deduplicate and cap
  const seen = new Set();
  return questions.filter(q => {
    if (seen.has(q)) return false;
    seen.add(q);
    return true;
  }).slice(0, 8);
}

// ─────────────────────────────────────────────────────────────────────────────
// Guide shown before any file is uploaded
// ─────────────────────────────────────────────────────────────────────────────

const QUESTION_GUIDE = [
  {
    icon: '∑',
    title: 'Totals & Sums',
    examples: [
      'What is the total revenue?',
      'How many units were sold in total?',
    ],
  },
  {
    icon: '↑',
    title: 'Rankings',
    examples: [
      'Which product had the highest profit?',
      'Which region had the lowest sales?',
    ],
  },
  {
    icon: '⇄',
    title: 'Comparisons',
    examples: [
      'Compare Q1 vs Q3 revenue',
      'Which departments exceeded budget?',
    ],
  },
  {
    icon: '◎',
    title: 'Filters',
    examples: [
      'Show all rows where profit is negative',
      'What are the sales for [specific item]?',
    ],
  },
];

// ─────────────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────────────

export default function UploadPanel({ onFileUploaded, fileInfo, onSampleQuestion }) {
  const [dragging,   setDragging]   = useState(false);
  const [uploading,  setUploading]  = useState(false);
  const [error,      setError]      = useState('');
  const [suggestions, setSuggestions] = useState([]);
  const fileRef = useRef(null);

  useEffect(() => {
    if (fileInfo) {
      setSuggestions(generateQuestions(fileInfo.sheets));
    }
  }, [fileInfo]);

  const handleFile = async (file) => {
    if (!file) return;
    if (!file.name.match(/\.(xlsx|xls)$/i)) {
      setError('Only .xlsx and .xls files are supported.');
      return;
    }
    setError('');
    setUploading(true);
    const formData = new FormData();
    formData.append('file', file);
    try {
      const res = await fetch('http://localhost:8000/upload', { method: 'POST', body: formData });
      if (!res.ok) { const e = await res.json(); throw new Error(e.detail || 'Upload failed'); }
      const data = await res.json();
      onFileUploaded(data);
    } catch (e) {
      setError(e.message || 'Could not connect to backend.');
    } finally {
      setUploading(false);
    }
  };

  const onDrop = (e) => { e.preventDefault(); setDragging(false); handleFile(e.dataTransfer.files[0]); };

  return (
    <aside style={{
      width: 272, minWidth: 272,
      background: '#ffffff',
      borderRight: '1px solid #e2e5ea',
      display: 'flex', flexDirection: 'column',
      overflowY: 'auto', flexShrink: 0,
    }}>

      {/* ── Upload zone ── */}
      <div style={{ padding: '18px 16px', borderBottom: '1px solid #f0f2f5' }}>
        <div style={LABEL}>Workbook</div>

        <div
          onClick={() => !uploading && fileRef.current?.click()}
          onDragOver={e => { e.preventDefault(); setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          style={{
            border: `2px dashed ${dragging ? '#C31D27' : fileInfo ? '#b7e4c7' : '#dde0e6'}`,
            borderRadius: 10, padding: '18px 12px',
            textAlign: 'center', cursor: uploading ? 'wait' : 'pointer',
            background: dragging ? '#fdf1f2' : fileInfo ? '#f0faf4' : '#fafbfc',
            transition: 'all 0.2s',
          }}
        >
          {!fileInfo ? (
            <>
              <div style={{
                width: 40, height: 40, margin: '0 auto 10px',
                background: '#fdf1f2', border: '1px solid #fad0d3',
                borderRadius: 10, display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}>
                <svg width="18" height="18" fill="none" viewBox="0 0 24 24" stroke="#C31D27" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12"/>
                </svg>
              </div>
              <div style={{ fontSize: 13, fontWeight: 500, color: '#374151', marginBottom: 3 }}>
                {uploading ? 'Parsing workbook…' : 'Drop file or click to browse'}
              </div>
              <div style={{ fontSize: 11, color: '#9aa0ad' }}>.xlsx · .xls supported</div>
            </>
          ) : (
            <>
              <div style={{
                width: 40, height: 40, margin: '0 auto 10px',
                background: '#f0faf4', border: '1px solid #b7e4c7',
                borderRadius: 10, display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}>
                <svg width="18" height="18" fill="none" viewBox="0 0 24 24" stroke="#1a7f4b" strokeWidth="2.2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7"/>
                </svg>
              </div>
              <div style={{ fontSize: 12, fontWeight: 600, color: '#1a7f4b', wordBreak: 'break-all', marginBottom: 2 }}>
                {fileInfo.filename}
              </div>
              <div style={{ fontSize: 11, color: '#6b7280' }}>
                {fileInfo.total_rows.toLocaleString()} rows · {fileInfo.sheets.length} sheet{fileInfo.sheets.length !== 1 ? 's' : ''}
              </div>
              <div style={{ fontSize: 10.5, color: '#9aa0ad', marginTop: 6 }}>Click to replace</div>
            </>
          )}
        </div>

        <input ref={fileRef} type="file" accept=".xlsx,.xls" style={{ display: 'none' }}
          onChange={e => handleFile(e.target.files[0])} />

        {uploading && (
          <div style={{
            marginTop: 10, padding: '8px 12px', borderRadius: 8,
            background: '#fffbeb', border: '1px solid #fde68a',
            display: 'flex', alignItems: 'center', gap: 8,
            fontSize: 11.5, color: '#92400e',
          }}>
            <div style={{ display: 'flex', gap: 3 }}>
              {[1,2,3].map(i => (
                <div key={i} className={`dot-${i}`} style={{ width: 5, height: 5, borderRadius: '50%', background: '#f59e0b' }} />
              ))}
            </div>
            Detecting table types…
          </div>
        )}

        {error && (
          <div style={{
            marginTop: 10, padding: '8px 12px', borderRadius: 8,
            background: '#fef2f2', border: '1px solid #fecaca',
            fontSize: 11.5, color: '#b91c1c', lineHeight: 1.5,
          }}>{error}</div>
        )}
      </div>

      {/* ── Sheet info (after upload) ── */}
      {fileInfo && (
        <div style={{ padding: '14px 16px', borderBottom: '1px solid #f0f2f5' }}>
          <div style={LABEL}>Detected Sheets</div>
          {fileInfo.sheets.map((s, i) => {
            const ttype = s.table_type || 'UNKNOWN';
            const meta  = TABLE_TYPE_LABELS[ttype] || TABLE_TYPE_LABELS.UNKNOWN;
            return (
              <div key={i} style={{
                display: 'flex', alignItems: 'flex-start', gap: 8,
                padding: '8px 10px', borderRadius: 8, marginBottom: 4,
                background: '#fafbfc', border: '1px solid #ebedf0',
              }}>
                <div style={{ width: 7, height: 7, borderRadius: '50%', background: '#C31D27', flexShrink: 0, marginTop: 4 }} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 12, fontWeight: 600, color: '#1a1f2e', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {s.name}
                  </div>
                  <div style={{ fontSize: 10.5, color: '#9aa0ad', marginTop: 1 }}>
                    {s.rows.toLocaleString()} rows · {(s.columns || []).length} cols
                  </div>
                  <span style={{
                    display: 'inline-block', marginTop: 4,
                    padding: '2px 7px',
                    background: meta.bg, color: meta.color,
                    borderRadius: 4, fontSize: 9.5,
                    fontWeight: 700, letterSpacing: '0.04em', textTransform: 'uppercase',
                  }}>
                    {meta.label}
                  </span>
                </div>
              </div>
            );
          })}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginTop: 8 }}>
            {[
              { val: fileInfo.sheets.length, lbl: 'Sheet(s)' },
              { val: fileInfo.total_rows.toLocaleString(), lbl: 'Total Rows' },
            ].map((s, i) => (
              <div key={i} style={{
                background: '#fdf1f2', border: '1px solid #fad0d3',
                borderRadius: 8, padding: '9px 12px',
              }}>
                <div style={{ fontSize: 20, fontWeight: 700, color: '#C31D27', fontFamily: 'IBM Plex Mono' }}>
                  {s.val}
                </div>
                <div style={{ fontSize: 10.5, color: '#9aa0ad', marginTop: 2 }}>{s.lbl}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── Dynamic suggestions (after upload) ── */}
      {fileInfo && suggestions.length > 0 && (
        <div style={{ padding: '14px 16px', flex: 1 }}>
          <div style={{ ...LABEL, display: 'flex', alignItems: 'center', gap: 6 }}>
            Suggested Questions
            <span style={{
              padding: '1px 6px',
              background: '#f0f2f5', color: '#9aa0ad',
              borderRadius: 8, fontSize: 9, fontWeight: 600,
            }}>based on your data</span>
          </div>
          {suggestions.map((q, i) => (
            <div
              key={i}
              onClick={() => onSampleQuestion(q)}
              style={{
                padding: '8px 10px',
                border: '1px solid #ebedf0', borderRadius: 8,
                fontSize: 12, color: '#374151', cursor: 'pointer',
                marginBottom: 5, lineHeight: 1.55,
                transition: 'all 0.15s', background: '#fafbfc',
              }}
              onMouseOver={e => {
                e.currentTarget.style.borderColor = '#C31D27';
                e.currentTarget.style.background = '#fdf1f2';
                e.currentTarget.style.color = '#C31D27';
              }}
              onMouseOut={e => {
                e.currentTarget.style.borderColor = '#ebedf0';
                e.currentTarget.style.background = '#fafbfc';
                e.currentTarget.style.color = '#374151';
              }}
            >
              <span style={{ color: '#C31D27', marginRight: 6, fontSize: 9, opacity: 0.8 }}>▸</span>
              {q}
            </div>
          ))}
        </div>
      )}

      {/* ── Question guide (before upload) ── */}
      {!fileInfo && (
        <div style={{ padding: '16px 16px', flex: 1 }}>
          <div style={LABEL}>What you can ask</div>
          <div style={{ fontSize: 11.5, color: '#9aa0ad', marginBottom: 12, lineHeight: 1.6 }}>
            Upload any Excel file — questions are generated from your actual data, never hardcoded.
          </div>
          {QUESTION_GUIDE.map((g, i) => (
            <div key={i} style={{
              padding: '11px 12px',
              background: '#fafbfc', border: '1px solid #ebedf0',
              borderRadius: 9, marginBottom: 8,
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 7 }}>
                <div style={{
                  width: 24, height: 24, borderRadius: 6,
                  background: '#fdf1f2', border: '1px solid #fad0d3',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: 12, color: '#C31D27', fontWeight: 600,
                }}>{g.icon}</div>
                <span style={{ fontSize: 11.5, fontWeight: 600, color: '#374151' }}>{g.title}</span>
              </div>
              {g.examples.map((ex, j) => (
                <div key={j} style={{
                  fontSize: 11, color: '#6b7280', lineHeight: 1.55,
                  paddingLeft: 8, borderLeft: '2px solid #f0f2f5', marginBottom: 3,
                  fontStyle: 'italic',
                }}>
                  "{ex}"
                </div>
              ))}
            </div>
          ))}
        </div>
      )}
    </aside>
  );
}

const LABEL = {
  fontSize: 9.5, fontWeight: 700, letterSpacing: '0.12em',
  textTransform: 'uppercase', color: '#9aa0ad', marginBottom: 10,
  display: 'block',
};
