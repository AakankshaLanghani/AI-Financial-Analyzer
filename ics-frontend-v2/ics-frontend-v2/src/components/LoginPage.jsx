import React, { useState } from 'react';
import ICSLogo from './ICSLogo';

export default function LoginPage({ onLogin, backendUrl }) {
  const [email, setEmail]       = useState('');
  const [password, setPassword] = useState('');
  const [showPw, setShowPw]     = useState(false);
  const [loading, setLoading]   = useState(false);
  const [error, setError]       = useState('');

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    if (!email.trim() || !password) {
      setError('Please enter your email and password.');
      return;
    }
    setLoading(true);
    try {
      const res = await fetch(`${backendUrl}/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: email.trim(), password }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.detail || 'Invalid email or password.');
      } else {
        onLogin(data.access_token);
      }
    } catch {
      setError('Cannot reach the server. Make sure the backend is running.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{
      height: '100vh',
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      background: '#f0f2f5',
      fontFamily: 'IBM Plex Sans, system-ui, sans-serif',
    }}>

      {/* Card */}
      <div style={{
        background: '#ffffff',
        border: '1px solid #e2e5ea',
        borderRadius: 18,
        padding: '40px 44px',
        width: 400,
        maxWidth: '92vw',
        boxShadow: '0 4px 32px rgba(0,0,0,0.08)',
      }}>

        {/* Logo + Title */}
        <div style={{ textAlign: 'center', marginBottom: 32 }}>
          <ICSLogo height={34} />
          <div style={{
            marginTop: 14,
            fontSize: 19,
            fontWeight: 700,
            color: '#1a1f2e',
            letterSpacing: '-0.02em',
          }}>
            AI Financial Analyzer
          </div>
          <div style={{ fontSize: 12.5, color: '#9aa0ad', marginTop: 4 }}>
            Sign in to continue
          </div>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>

          {/* Email */}
          <div>
            <label style={{ fontSize: 12, fontWeight: 600, color: '#374151', display: 'block', marginBottom: 6 }}>
              Email
            </label>
            <input
              type="email"
              autoComplete="username"
              placeholder="you@icsgroup.com.pk"
              value={email}
              onChange={e => setEmail(e.target.value)}
              disabled={loading}
              style={{
                width: '100%',
                boxSizing: 'border-box',
                padding: '10px 13px',
                fontSize: 13.5,
                border: '1.5px solid #e2e5ea',
                borderRadius: 9,
                outline: 'none',
                color: '#1a1f2e',
                background: '#fafbfc',
                transition: 'border-color 0.15s, box-shadow 0.15s',
              }}
              onFocus={e => {
                e.target.style.borderColor = '#C31D27';
                e.target.style.boxShadow = '0 0 0 3px rgba(195,29,39,0.08)';
              }}
              onBlur={e => {
                e.target.style.borderColor = '#e2e5ea';
                e.target.style.boxShadow = 'none';
              }}
            />
          </div>

          {/* Password */}
          <div>
            <label style={{ fontSize: 12, fontWeight: 600, color: '#374151', display: 'block', marginBottom: 6 }}>
              Password
            </label>
            <div style={{ position: 'relative' }}>
              <input
                type={showPw ? 'text' : 'password'}
                autoComplete="current-password"
                placeholder="••••••••"
                value={password}
                onChange={e => setPassword(e.target.value)}
                disabled={loading}
                style={{
                  width: '100%',
                  boxSizing: 'border-box',
                  padding: '10px 40px 10px 13px',
                  fontSize: 13.5,
                  border: '1.5px solid #e2e5ea',
                  borderRadius: 9,
                  outline: 'none',
                  color: '#1a1f2e',
                  background: '#fafbfc',
                  transition: 'border-color 0.15s, box-shadow 0.15s',
                }}
                onFocus={e => {
                  e.target.style.borderColor = '#C31D27';
                  e.target.style.boxShadow = '0 0 0 3px rgba(195,29,39,0.08)';
                }}
                onBlur={e => {
                  e.target.style.borderColor = '#e2e5ea';
                  e.target.style.boxShadow = 'none';
                }}
              />
              {/* Show/hide toggle */}
              <button
                type="button"
                onClick={() => setShowPw(v => !v)}
                tabIndex={-1}
                style={{
                  position: 'absolute', right: 11, top: '50%',
                  transform: 'translateY(-50%)',
                  background: 'none', border: 'none',
                  cursor: 'pointer', padding: 2,
                  color: '#9aa0ad',
                  display: 'flex', alignItems: 'center',
                }}
              >
                {showPw ? (
                  <svg width="16" height="16" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M13.875 18.825A10.05 10.05 0 0112 19c-4.478 0-8.268-2.943-9.543-7a9.97 9.97 0 011.563-3.029m5.858.908a3 3 0 114.243 4.243M9.878 9.878l4.242 4.242M9.88 9.88l-3.29-3.29m7.532 7.532l3.29 3.29M3 3l3.59 3.59m0 0A9.953 9.953 0 0112 5c4.478 0 8.268 2.943 9.543 7a10.025 10.025 0 01-4.132 5.411m0 0L21 21" />
                  </svg>
                ) : (
                  <svg width="16" height="16" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                    <path strokeLinecap="round" strokeLinejoin="round" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
                  </svg>
                )}
              </button>
            </div>
          </div>

          {/* Error */}
          {error && (
            <div style={{
              background: '#fff5f5',
              border: '1px solid #fecaca',
              borderRadius: 8,
              padding: '9px 13px',
              fontSize: 12.5,
              color: '#b91c1c',
              display: 'flex', alignItems: 'center', gap: 7,
            }}>
              <svg width="14" height="14" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2" style={{ flexShrink: 0 }}>
                <circle cx="12" cy="12" r="10" /><path strokeLinecap="round" d="M12 8v4m0 4h.01" />
              </svg>
              {error}
            </div>
          )}

          {/* Submit */}
          <button
            type="submit"
            disabled={loading}
            style={{
              marginTop: 4,
              width: '100%',
              padding: '11px',
              background: loading ? '#f3f4f6' : '#C31D27',
              color: loading ? '#9ca3af' : '#fff',
              border: 'none',
              borderRadius: 9,
              fontSize: 14,
              fontWeight: 600,
              cursor: loading ? 'not-allowed' : 'pointer',
              fontFamily: 'IBM Plex Sans, system-ui, sans-serif',
              transition: 'background 0.15s',
              display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8,
            }}
            onMouseOver={e => { if (!loading) e.currentTarget.style.background = '#a31721'; }}
            onMouseOut={e => { if (!loading) e.currentTarget.style.background = '#C31D27'; }}
          >
            {loading ? (
              <>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
                  style={{ animation: 'spin 1s linear infinite' }}>
                  <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83" strokeLinecap="round" />
                </svg>
                Signing in…
              </>
            ) : 'Sign In'}
          </button>
        </form>

        {/* Footer note */}
        <div style={{ marginTop: 24, textAlign: 'center', fontSize: 11.5, color: '#c4c8d0' }}>
          Internal tool · ICS Group · Access restricted
        </div>
      </div>

      <style>{`
        @keyframes spin { to { transform: rotate(360deg); } }
      `}</style>
    </div>
  );
}
