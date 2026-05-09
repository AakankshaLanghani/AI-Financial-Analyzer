import React from 'react';
import { ICSMark } from './ICSLogo';

// ─── Avatars ──────────────────────────────────────────────────────────────────
function ICSAvatar() {
  return (
    <div style={{
      width: 34, height: 34, borderRadius: 10, flexShrink: 0,
      background: '#C31D27',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      boxShadow: '0 2px 8px rgba(195,29,39,0.2)',
    }}>
      <ICSMark size={18} color="white" />
    </div>
  );
}

function UserAvatar() {
  return (
    <div style={{
      width: 34, height: 34, borderRadius: 10, flexShrink: 0,
      background: '#1a1f2e',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      fontSize: 10, fontWeight: 700, color: '#ffffff', letterSpacing: '0.03em',
    }}>YOU</div>
  );
}

// ─── Messages ─────────────────────────────────────────────────────────────────
export function UserMessage({ text }) {
  return (
    <div className="msg-appear" style={{ display: 'flex', gap: 10, flexDirection: 'row-reverse', alignItems: 'flex-start' }}>
      <UserAvatar />
      <div style={{
        maxWidth: '70%', padding: '12px 17px',
        background: '#1a1f2e',
        borderRadius: 14, borderTopRightRadius: 4,
        fontSize: 13.5, lineHeight: 1.65, color: '#f0f2f5',
        boxShadow: '0 2px 8px rgba(0,0,0,0.12)',
      }}>
        {text}
      </div>
    </div>
  );
}

export function AIMessage({ data }) {
  const rowCount = data.row_count;

  return (
    <div className="msg-appear" style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
      <ICSAvatar />
      <div style={{
        flex: 1, maxWidth: 'calc(100% - 44px)',
        background: '#ffffff',
        border: '1px solid #e2e5ea',
        borderRadius: 14, borderTopLeftRadius: 4,
        padding: '16px 20px',
        boxShadow: '0 1px 6px rgba(0,0,0,0.05)',
      }}>

        {/* Answer — main result */}
        <div style={{
          padding: '14px 16px',
          background: '#fdf8f8',
          border: '1px solid #fad0d3',
          borderRadius: 10,
          marginBottom: data.explanation ? 12 : 0,
        }}>
          <div style={{
            fontSize: 10, fontWeight: 700, letterSpacing: '0.1em',
            textTransform: 'uppercase', color: '#C31D27', marginBottom: 7,
          }}>
            Answer
          </div>
          <div style={{ fontSize: 16, fontWeight: 700, color: '#1a1f2e', lineHeight: 1.5 }}>
            {data.answer}
          </div>
        </div>

        {/* Explanation */}
        {data.explanation && (
          <div style={{
            fontSize: 13, color: '#5a6278', lineHeight: 1.75,
            paddingTop: 4,
          }}>
            {data.explanation}
          </div>
        )}

        {/* Rows analysed badge */}
        {rowCount > 0 && (
          <div style={{ marginTop: 12, display: 'flex', alignItems: 'center', gap: 5 }}>
            <svg width="11" height="11" fill="none" viewBox="0 0 24 24" stroke="#9aa0ad" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"/>
            </svg>
            <span style={{ fontSize: 11, color: '#9aa0ad' }}>
              Based on {rowCount.toLocaleString()} row{rowCount !== 1 ? 's' : ''} from your data
            </span>
          </div>
        )}
      </div>
    </div>
  );
}

export function ThinkingMessage() {
  return (
    <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
      <ICSAvatar />
      <div style={{
        padding: '13px 18px',
        background: '#ffffff', border: '1px solid #e2e5ea',
        borderRadius: 14, borderTopLeftRadius: 4,
        display: 'flex', alignItems: 'center', gap: 12,
        boxShadow: '0 1px 4px rgba(0,0,0,0.04)',
      }}>
        <div style={{ display: 'flex', gap: 5 }}>
          {[1,2,3].map(i => (
            <div key={i} className={`dot-${i}`} style={{
              width: 7, height: 7, borderRadius: '50%', background: '#C31D27',
            }} />
          ))}
        </div>
        <span style={{ fontSize: 13, color: '#6b7280', fontWeight: 500 }}>
          Analysing your data…
        </span>
      </div>
    </div>
  );
}

export function SystemMessage({ text }) {
  return (
    <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
      <ICSAvatar />
      <div style={{
        padding: '12px 16px',
        background: '#f7f8fa', border: '1px solid #e2e5ea',
        borderRadius: 14, borderTopLeftRadius: 4,
        fontSize: 13, color: '#5a6278', lineHeight: 1.7,
        boxShadow: '0 1px 3px rgba(0,0,0,0.03)',
        maxWidth: '78%',
      }}
        dangerouslySetInnerHTML={{
          __html: text.replace(/\*\*(.*?)\*\*/g, '<strong style="color:#1a1f2e">$1</strong>'),
        }}
      />
    </div>
  );
}
