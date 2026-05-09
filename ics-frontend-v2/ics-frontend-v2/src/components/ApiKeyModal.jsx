import React, { useState, useEffect } from 'react';

export default function ApiKeyModal({ isOpen, onClose, onSave, currentKey }) {
  const [val, setVal] = useState(currentKey || '');

  useEffect(() => {
    if (isOpen) setVal(currentKey || '');
  }, [isOpen, currentKey]);

  if (!isOpen) return null;

  const handleSave = () => {
    onSave(val.trim());
    onClose();
  };

  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0,
        background: 'rgba(0,0,0,0.75)',
        zIndex: 1000,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          background: 'var(--surface)',
          border: '1px solid var(--border2)',
          borderRadius: 14,
          padding: 24,
          width: 400,
          maxWidth: '90vw',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
          <div style={{
            width: 32, height: 32, borderRadius: 8,
            background: 'rgba(195,29,39,0.1)', border: '1px solid rgba(195,29,39,0.2)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>
            <svg width="14" height="14" fill="none" viewBox="0 0 24 24" stroke="#C31D27" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z" />
            </svg>
          </div>
          <div style={{ fontSize: 15, fontWeight: 600, color: 'var(--text)' }}>OpenAI API Key</div>
        </div>
        <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 16, lineHeight: 1.6 }}>
          Your key is stored in memory only and never sent anywhere except OpenAI via your local backend. Required for LLM-powered answers.
        </div>
        <input
          autoFocus
          type="password"
          placeholder="sk-..."
          value={val}
          onChange={e => setVal(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && handleSave()}
          style={{
            width: '100%',
            background: 'var(--surface2)',
            border: '1px solid var(--border)',
            borderRadius: 8,
            padding: '10px 12px',
            fontSize: 13,
            color: 'var(--text)',
            outline: 'none',
            fontFamily: 'IBM Plex Mono',
          }}
          onFocus={e => e.target.style.borderColor = '#C31D27'}
          onBlur={e => e.target.style.borderColor = 'var(--border)'}
        />
        <div style={{ display: 'flex', gap: 8, marginTop: 14 }}>
          <button
            onClick={onClose}
            style={{
              padding: '9px 16px',
              background: 'none',
              border: '1px solid var(--border)',
              borderRadius: 8,
              color: 'var(--muted)',
              fontSize: 13,
              cursor: 'pointer',
              fontFamily: 'IBM Plex Sans',
            }}
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            style={{
              flex: 1, padding: 9,
              background: '#C31D27',
              border: 'none', borderRadius: 8,
              color: '#fff', fontSize: 13, fontWeight: 500,
              cursor: 'pointer', fontFamily: 'IBM Plex Sans',
              transition: 'background 0.15s',
            }}
            onMouseOver={e => e.target.style.background = '#8B1219'}
            onMouseOut={e => e.target.style.background = '#C31D27'}
          >
            Save Key
          </button>
        </div>
      </div>
    </div>
  );
}
