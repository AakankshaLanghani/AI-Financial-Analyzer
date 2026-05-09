import React, { useState, useRef, useEffect, useCallback } from 'react';
import UploadPanel from './components/UploadPanel';
import { UserMessage, AIMessage, ThinkingMessage, SystemMessage } from './components/Messages';
import ICSLogo, { ICSMark } from './components/ICSLogo';

const BACKEND = 'http://localhost:8000';

export default function App() {
  const [messages, setMessages]   = useState([]);
  const [fileInfo, setFileInfo]   = useState(null);
  const [sessionId, setSessionId] = useState(null);
  const [question, setQuestion]   = useState('');
  const [thinking, setThinking]         = useState(false);
  const [generatingReport, setGeneratingReport] = useState(false);
  const chatRef     = useRef(null);
  const textareaRef = useRef(null);

  useEffect(() => {
    setMessages([{
      id: 'welcome',
      type: 'system',
      text: 'Welcome to the <strong>ICS AI Financial Analyzer</strong>. Upload an Excel workbook using the panel on the left, then ask financial questions in plain English. Every answer is grounded strictly in your data — no guesses, no hallucinations.',
    }]);
  }, []);

  useEffect(() => {
    if (chatRef.current) chatRef.current.scrollTop = chatRef.current.scrollHeight;
  }, [messages, thinking]);

  const addMessage = useCallback((msg) => {
    setMessages(prev => [...prev, { id: Date.now() + Math.random(), ...msg }]);
  }, []);

  const handleFileUploaded = useCallback((data) => {
    setFileInfo(data);
    setSessionId(data.session_id);
    const sheetList = data.sheets.map(s => `<strong>${s.name}</strong> (${s.rows} rows)`).join(', ');
    addMessage({
      type: 'system',
      text: `Workbook <strong>${data.filename}</strong> loaded successfully — ${data.sheets.length} sheet(s) detected: ${sheetList}. ${data.total_rows} total rows parsed and ready for analysis.`,
    });
  }, [addMessage]);

  const handleSampleQuestion = useCallback((q) => {
    setQuestion(q);
    textareaRef.current?.focus();
  }, []);

  const sendMessage = useCallback(async () => {
    const q = question.trim();
    if (!q || thinking) return;
    if (!sessionId) {
      addMessage({ type: 'system', text: 'Please upload an Excel file before asking questions.' });
      return;
    }
    setQuestion('');
    if (textareaRef.current) textareaRef.current.style.height = 'auto';
    addMessage({ type: 'user', text: q });
    setThinking(true);

    try {
      const res  = await fetch(`${BACKEND}/ask`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId, question: q }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Request failed');
      addMessage({ type: 'ai', data });
    } catch (err) {
      addMessage({
        type: 'ai',
        data: {
          answer: `Error: ${err.message}`,
          explanation: 'Please verify the backend server is running on port 8000.',
          sources: [],
          source_rows: [],
        },
      });
    } finally {
      setThinking(false);
    }
  }, [question, thinking, sessionId, addMessage]);

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  };

  const handleTextareaChange = (e) => {
    setQuestion(e.target.value);
    e.target.style.height = 'auto';
    e.target.style.height = Math.min(e.target.scrollHeight, 120) + 'px';
  };

  const canSend = !!question.trim() && !thinking && !!sessionId;

  const handleGenerateReport = useCallback(async () => {
    if (!sessionId || generatingReport) return;
    setGeneratingReport(true);
    addMessage({ type: 'system', text: '📊 Generating your analytics report — this may take 10–20 seconds...' });
    try {
      const res = await fetch(`${BACKEND}/report`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId, question: '' }),
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Report generation failed');
      }
      const blob = await res.blob();
      const url  = URL.createObjectURL(blob);
      const a    = document.createElement('a');
      const safeName = (fileInfo?.filename || 'report').replace(/\.[^.]+$/, '');
      a.href     = url;
      a.download = `${safeName}_ICS_Report.pdf`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      addMessage({ type: 'system', text: '✅ Report downloaded successfully. Check your Downloads folder.' });
    } catch (err) {
      addMessage({ type: 'system', text: `❌ Report error: ${err.message}` });
    } finally {
      setGeneratingReport(false);
    }
  }, [sessionId, generatingReport, fileInfo, addMessage]);

  return (
    <div style={{
      height: '100vh', display: 'flex', flexDirection: 'column',
      overflow: 'hidden', background: '#f0f2f5',
    }}>

      {/* ── HEADER ── */}
      <header style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '0 28px', height: 56,
        background: '#ffffff',
        borderBottom: '1px solid #e2e5ea',
        flexShrink: 0,
        boxShadow: '0 1px 4px rgba(0,0,0,0.06)',
        zIndex: 10,
      }}>
        {/* Brand */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          <ICSLogo height={30} />
          <div style={{ width: 1, height: 26, background: '#e2e5ea' }} />
          <div>
            <div style={{ fontSize: 13, fontWeight: 600, color: '#1a1f2e', lineHeight: 1.25, letterSpacing: '-0.01em' }}>
              AI Financial Analyzer
            </div>
            <div style={{ fontSize: 10.5, color: '#9aa0ad', letterSpacing: '0.03em', marginTop: 1 }}>
              Data-grounded · Zero hallucinations
            </div>
          </div>
        </div>

        {/* Right side: report button + status pill */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>

          {/* Generate Report button — only shown when file is loaded */}
          {fileInfo && (
            <button
              onClick={handleGenerateReport}
              disabled={generatingReport}
              title="Generate full PDF analytics report"
              style={{
                display: 'flex', alignItems: 'center', gap: 7,
                padding: '6px 14px',
                background: generatingReport ? '#f3f4f6' : '#C31D27',
                border: 'none',
                borderRadius: 20,
                fontSize: 11.5, fontWeight: 600,
                color: generatingReport ? '#9ca3af' : '#ffffff',
                cursor: generatingReport ? 'not-allowed' : 'pointer',
                transition: 'background 0.15s, transform 0.1s',
                boxShadow: generatingReport ? 'none' : '0 2px 8px rgba(195,29,39,0.28)',
                userSelect: 'none',
                whiteSpace: 'nowrap',
              }}
              onMouseOver={e => { if (!generatingReport) e.currentTarget.style.background = '#a31721'; }}
              onMouseOut={e => { if (!generatingReport) e.currentTarget.style.background = '#C31D27'; }}
              onMouseDown={e => { if (!generatingReport) e.currentTarget.style.transform = 'scale(0.95)'; }}
              onMouseUp={e => e.currentTarget.style.transform = 'scale(1)'}
            >
              {generatingReport ? (
                <>
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
                    style={{ animation: 'spin 1s linear infinite' }}>
                    <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83" strokeLinecap="round"/>
                  </svg>
                  Generating…
                </>
              ) : (
                <>
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                  </svg>
                  Generate Report
                </>
              )}
            </button>
          )}

          {/* Status pill */}
          <div style={{
            display: 'flex', alignItems: 'center', gap: 7,
            padding: '5px 14px',
            background: fileInfo ? '#f0faf4' : '#f7f8fa',
            border: `1px solid ${fileInfo ? '#b7e4c7' : '#e2e5ea'}`,
            borderRadius: 20,
            fontSize: 11.5, fontWeight: 500,
            color: fileInfo ? '#1a7f4b' : '#6b7280',
            userSelect: 'none',
          }}>
            <div style={{
              width: 7, height: 7, borderRadius: '50%',
              background: fileInfo ? '#22c55e' : '#d1d5db',
              animation: fileInfo ? 'pulse-dot 2.5s ease-in-out infinite' : 'none',
            }} />
            {fileInfo
              ? `${fileInfo.filename} · ${fileInfo.total_rows} rows loaded`
              : 'Ready — upload a workbook to begin'}
          </div>

        </div>
      </header>

      {/* ── BODY ── */}
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>

        {/* ── SIDEBAR ── */}
        <UploadPanel
          onFileUploaded={handleFileUploaded}
          fileInfo={fileInfo}
          onSampleQuestion={handleSampleQuestion}
        />

        {/* ── CHAT AREA ── */}
        <main style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minWidth: 0 }}>

          {/* Messages scroll area */}
          <div
            ref={chatRef}
            className="chat-scroll"
            style={{
              flex: 1, overflowY: 'auto',
              padding: '32px 40px',
              display: 'flex', flexDirection: 'column', gap: 20,
              background: '#f0f2f5',
            }}
          >
            {/* Welcome hero — shown when no file loaded */}
            {!fileInfo && messages.length <= 1 && (
              <div style={{
                display: 'flex', flexDirection: 'column',
                alignItems: 'center', justifyContent: 'center',
                flex: 1, textAlign: 'center', padding: '48px 32px', gap: 20,
              }}>
                <div style={{
                  width: 88, height: 88,
                  background: '#fff',
                  border: '1.5px solid #e9d7d8',
                  borderRadius: 24,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  boxShadow: '0 4px 20px rgba(195,29,39,0.10)',
                }}>
                  <ICSMark size={48} />
                </div>
                <div>
                  <div style={{ fontSize: 22, fontWeight: 700, color: '#1a1f2e', marginBottom: 8, letterSpacing: '-0.02em' }}>
                    ICS AI Financial Analyzer
                  </div>
                  <div style={{ fontSize: 13.5, color: '#6b7280', maxWidth: 400, lineHeight: 1.8, margin: '0 auto' }}>
                    Upload any Excel workbook and ask financial questions in plain English.
                    Every answer cites the exact rows it came from.
                  </div>
                </div>

                {/* Steps */}
                <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                  {[
                    { n: '1', label: 'Upload .xlsx or .xls' },
                    { n: '2', label: 'Auto-detect tables' },
                    { n: '3', label: 'Ask in plain English' },
                    { n: '4', label: 'Get cited answers' },
                  ].map((s) => (
                    <div key={s.n} style={{
                      display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8,
                      padding: '14px 18px',
                      background: '#fff',
                      border: '1px solid #e2e5ea',
                      borderRadius: 12,
                      minWidth: 110,
                    }}>
                      <div style={{
                        width: 28, height: 28, borderRadius: '50%',
                        background: '#C31D27',
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                        fontSize: 12, fontWeight: 700, color: '#fff',
                      }}>{s.n}</div>
                      <span style={{ fontSize: 11.5, color: '#374151', fontWeight: 500, textAlign: 'center', lineHeight: 1.4 }}>
                        {s.label}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {messages.map(msg => {
              if (msg.type === 'user')   return <UserMessage   key={msg.id} text={msg.text} />;
              if (msg.type === 'ai')     return <AIMessage     key={msg.id} data={msg.data} />;
              if (msg.type === 'system') return <SystemMessage key={msg.id} text={msg.text} />;
              return null;
            })}

            {thinking && <ThinkingMessage />}
          </div>

          {/* ── INPUT BAR ── */}
          <div style={{
            padding: '12px 28px 16px',
            borderTop: '1px solid #e2e5ea',
            background: '#ffffff',
            flexShrink: 0,
          }}>
            <div
              style={{
                display: 'flex', gap: 10, alignItems: 'flex-end',
                background: fileInfo ? '#fafbfc' : '#f7f8fa',
                border: '1.5px solid #e2e5ea',
                borderRadius: 12,
                padding: '10px 14px',
                transition: 'border-color 0.15s, box-shadow 0.15s',
              }}
              onFocusCapture={e => {
                if (fileInfo) {
                  e.currentTarget.style.borderColor = '#C31D27';
                  e.currentTarget.style.boxShadow = '0 0 0 3px rgba(195,29,39,0.08)';
                }
              }}
              onBlurCapture={e => {
                e.currentTarget.style.borderColor = '#e2e5ea';
                e.currentTarget.style.boxShadow = 'none';
              }}
            >
              <textarea
                ref={textareaRef}
                value={question}
                onChange={handleTextareaChange}
                onKeyDown={handleKeyDown}
                placeholder={
                  !fileInfo
                    ? 'Upload an Excel workbook first to begin analysis...'
                    : 'Ask a financial question — e.g. "Which product had the highest margin?"'
                }
                disabled={!fileInfo || thinking}
                rows={1}
                style={{
                  flex: 1, background: 'none', border: 'none', outline: 'none',
                  fontFamily: 'IBM Plex Sans', fontSize: 13.5, color: '#1a1f2e',
                  lineHeight: 1.6, minHeight: 24, maxHeight: 120,
                  opacity: !fileInfo ? 0.5 : 1,
                }}
              />
              <button
                onClick={sendMessage}
                disabled={!canSend}
                title="Send (Enter)"
                style={{
                  width: 36, height: 36,
                  background: canSend ? '#C31D27' : '#e8eaed',
                  border: 'none', borderRadius: 9,
                  cursor: canSend ? 'pointer' : 'not-allowed',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  flexShrink: 0,
                  transition: 'background 0.15s, transform 0.1s',
                  boxShadow: canSend ? '0 2px 8px rgba(195,29,39,0.25)' : 'none',
                }}
                onMouseOver={e => { if (canSend) e.currentTarget.style.background = '#a31721'; }}
                onMouseOut={e => { if (canSend) e.currentTarget.style.background = '#C31D27'; }}
                onMouseDown={e => { if (canSend) e.currentTarget.style.transform = 'scale(0.91)'; }}
                onMouseUp={e => e.currentTarget.style.transform = 'scale(1)'}
              >
                <svg width="14" height="14" fill="none" viewBox="0 0 24 24" stroke="white" strokeWidth="2.5">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M5 12h14m-7-7l7 7-7 7" />
                </svg>
              </button>
            </div>
            <div style={{ marginTop: 6, fontSize: 11, color: '#9aa0ad', textAlign: 'center', letterSpacing: '0.01em' }}>
              Enter to send &nbsp;·&nbsp; Shift+Enter for new line &nbsp;·&nbsp; Answers cite exact source rows
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}
