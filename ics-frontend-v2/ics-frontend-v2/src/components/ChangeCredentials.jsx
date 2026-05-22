import React, { useState, useCallback } from 'react';

const inputStyle = {
  width: '100%', boxSizing: 'border-box',
  padding: '10px 13px', fontSize: 13.5,
  border: '1.5px solid #e2e5ea', borderRadius: 9,
  outline: 'none', color: '#1a1f2e', background: '#fafbfc',
};
const inputStyleWithToggle = { ...inputStyle, paddingRight: 40 };
const labelStyle = { fontSize: 12, fontWeight: 600, color: '#374151', display: 'block', marginBottom: 6 };

const EyeOff = () => (
  <svg width="15" height="15" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
    <path strokeLinecap="round" strokeLinejoin="round" d="M13.875 18.825A10.05 10.05 0 0112 19c-4.478 0-8.268-2.943-9.543-7a9.97 9.97 0 011.563-3.029m5.858.908a3 3 0 114.243 4.243M9.878 9.878l4.242 4.242M9.88 9.88l-3.29-3.29m7.532 7.532l3.29 3.29M3 3l3.59 3.59m0 0A9.953 9.953 0 0112 5c4.478 0 8.268 2.943 9.543 7a10.025 10.025 0 01-4.132 5.411m0 0L21 21" />
  </svg>
);
const EyeOn = () => (
  <svg width="15" height="15" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
    <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
    <path strokeLinecap="round" strokeLinejoin="round" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
  </svg>
);

export default function ChangeCredentials({ isOpen, onClose, onSuccess, token, backendUrl }) {
  const [currentPassword, setCurrentPassword] = useState('');
  const [newEmail,        setNewEmail]         = useState('');
  const [newPassword,     setNewPassword]      = useState('');
  const [confirmPassword, setConfirmPassword]  = useState('');
  const [showCurrent,     setShowCurrent]      = useState(false);
  const [showNew,         setShowNew]          = useState(false);
  const [loading,         setLoading]          = useState(false);
  const [error,           setError]            = useState('');
  const [success,         setSuccess]          = useState('');

  const toggleCurrent = useCallback(() => setShowCurrent(v => !v), []);
  const toggleNew     = useCallback(() => setShowNew(v => !v), []);

  const onChangeCurrent = useCallback(e => setCurrentPassword(e.target.value), []);
  const onChangeEmail   = useCallback(e => setNewEmail(e.target.value), []);
  const onChangeNew     = useCallback(e => setNewPassword(e.target.value), []);
  const onChangeConfirm = useCallback(e => setConfirmPassword(e.target.value), []);

  const reset = useCallback(() => {
    setCurrentPassword(''); setNewEmail('');
    setNewPassword(''); setConfirmPassword('');
    setError(''); setSuccess('');
    setShowCurrent(false); setShowNew(false);
  }, []);

  const handleClose = useCallback(() => { reset(); onClose(); }, [reset, onClose]);

  const handleSubmit = useCallback(async (e) => {
    e.preventDefault();
    setError(''); setSuccess('');
    if (!currentPassword || !newEmail || !newPassword || !confirmPassword) {
      setError('All fields are required.'); return;
    }
    if (newPassword !== confirmPassword) {
      setError('New passwords do not match.'); return;
    }
    if (newPassword.length < 8) {
      setError('New password must be at least 8 characters.'); return;
    }
    setLoading(true);
    try {
      const res  = await fetch(`${backendUrl}/change-credentials`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
        body:    JSON.stringify({
          current_password: currentPassword,
          new_email:        newEmail.trim(),
          new_password:     newPassword,
        }),
      });
      const data = await res.json();
      if (!res.ok) { setError(data.detail || 'Failed to update credentials.'); return; }
      setSuccess('Credentials updated! You will be logged out in 3 seconds…');
      setTimeout(() => { reset(); onSuccess(); }, 3000);
    } catch {
      setError('Cannot reach the server.');
    } finally {
      setLoading(false);
    }
  }, [currentPassword, newEmail, newPassword, confirmPassword, backendUrl, token, reset, onSuccess]);

  if (!isOpen) return null;

  const disabled = loading || !!success;

  return (
    <div onClick={handleClose} style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.45)',
      zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center',
    }}>
      <div onClick={e => e.stopPropagation()} style={{
        background: '#fff', border: '1px solid #e2e5ea', borderRadius: 16,
        padding: '32px 36px', width: 420, maxWidth: '92vw',
        boxShadow: '0 8px 40px rgba(0,0,0,0.12)',
        fontFamily: 'IBM Plex Sans, system-ui, sans-serif',
      }}>
        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 24 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <div style={{ width: 34, height: 34, borderRadius: 9, background: '#fdf1f2', border: '1px solid #fad0d3', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <svg width="16" height="16" fill="none" viewBox="0 0 24 24" stroke="#C31D27" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
              </svg>
            </div>
            <div>
              <div style={{ fontSize: 15, fontWeight: 700, color: '#1a1f2e' }}>Change Credentials</div>
              <div style={{ fontSize: 11.5, color: '#9aa0ad', marginTop: 1 }}>Update your login email & password</div>
            </div>
          </div>
          <button onClick={handleClose} style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#9aa0ad', padding: 4, borderRadius: 6, display: 'flex', alignItems: 'center' }}>
            <svg width="16" height="16" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.5">
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {success ? (
          <div style={{ background: '#f0faf4', border: '1px solid #b7e4c7', borderRadius: 10, padding: '16px 18px', textAlign: 'center' }}>
            <div style={{ fontSize: 28, marginBottom: 8 }}>✅</div>
            <div style={{ fontSize: 13.5, fontWeight: 600, color: '#1a7f4b', marginBottom: 4 }}>Done!</div>
            <div style={{ fontSize: 12.5, color: '#6b7280', lineHeight: 1.6 }}>{success}</div>
          </div>
        ) : (
          <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>

            {/* Current Password */}
            <div>
              <label style={labelStyle}>Current Password</label>
              <div style={{ position: 'relative' }}>
                <input type={showCurrent ? 'text' : 'password'} value={currentPassword} onChange={onChangeCurrent}
                  placeholder="Your current password" disabled={disabled} style={inputStyleWithToggle} />
                <button type="button" onClick={toggleCurrent} tabIndex={-1} style={{ position: 'absolute', right: 11, top: '50%', transform: 'translateY(-50%)', background: 'none', border: 'none', cursor: 'pointer', color: '#9aa0ad', display: 'flex', alignItems: 'center', padding: 2 }}>
                  {showCurrent ? <EyeOff /> : <EyeOn />}
                </button>
              </div>
            </div>

            <div style={{ height: 1, background: '#f0f2f5', margin: '2px 0' }} />

            {/* New Email */}
            <div>
              <label style={labelStyle}>New Email</label>
              <input type="email" value={newEmail} onChange={onChangeEmail}
                placeholder="new@example.com" disabled={disabled} style={inputStyle} />
            </div>

            {/* New Password */}
            <div>
              <label style={labelStyle}>New Password</label>
              <div style={{ position: 'relative' }}>
                <input type={showNew ? 'text' : 'password'} value={newPassword} onChange={onChangeNew}
                  placeholder="Min. 8 characters" disabled={disabled} style={inputStyleWithToggle} />
                <button type="button" onClick={toggleNew} tabIndex={-1} style={{ position: 'absolute', right: 11, top: '50%', transform: 'translateY(-50%)', background: 'none', border: 'none', cursor: 'pointer', color: '#9aa0ad', display: 'flex', alignItems: 'center', padding: 2 }}>
                  {showNew ? <EyeOff /> : <EyeOn />}
                </button>
              </div>
            </div>

            {/* Confirm Password */}
            <div>
              <label style={labelStyle}>Confirm New Password</label>
              <input type="password" value={confirmPassword} onChange={onChangeConfirm}
                placeholder="Re-enter new password" disabled={disabled} style={inputStyle} />
            </div>

            {error && (
              <div style={{ background: '#fff5f5', border: '1px solid #fecaca', borderRadius: 8, padding: '9px 13px', fontSize: 12.5, color: '#b91c1c', display: 'flex', alignItems: 'center', gap: 7 }}>
                <svg width="14" height="14" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2" style={{ flexShrink: 0 }}>
                  <circle cx="12" cy="12" r="10" /><path strokeLinecap="round" d="M12 8v4m0 4h.01" />
                </svg>
                {error}
              </div>
            )}

            <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
              <button type="button" onClick={handleClose} style={{ padding: '10px 18px', background: 'none', border: '1.5px solid #e2e5ea', borderRadius: 9, color: '#6b7280', fontSize: 13, fontWeight: 500, cursor: 'pointer', fontFamily: 'inherit' }}>
                Cancel
              </button>
              <button type="submit" disabled={loading} style={{ flex: 1, padding: '10px', background: loading ? '#f3f4f6' : '#C31D27', color: loading ? '#9ca3af' : '#fff', border: 'none', borderRadius: 9, fontSize: 13, fontWeight: 600, cursor: loading ? 'not-allowed' : 'pointer', fontFamily: 'inherit', transition: 'background 0.15s', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 7 }}
                onMouseOver={e => { if (!loading) e.currentTarget.style.background = '#a31721'; }}
                onMouseOut={e => { if (!loading) e.currentTarget.style.background = '#C31D27'; }}
              >
                {loading ? (
                  <>
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" style={{ animation: 'spin 1s linear infinite' }}>
                      <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83" strokeLinecap="round" />
                    </svg>
                    Saving…
                  </>
                ) : 'Save Changes'}
              </button>
            </div>
          </form>
        )}
      </div>
      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  );
}
