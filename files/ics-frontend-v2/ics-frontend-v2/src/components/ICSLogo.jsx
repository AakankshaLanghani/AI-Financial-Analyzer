import React from 'react';

/**
 * ICSLogo — Full wordmark: asterisk mark + "ICS" text
 * Matches the real ICS logo: red 6-spoke asterisk (3 bars at 0/60/120°) + bold black "ICS"
 */
export default function ICSLogo({ height = 36, className = '' }) {
  const w = height * 3.8;
  const h = height;
  const markSize = h * 0.9;
  const cx = markSize / 2;
  const cy = markSize / 2;
  const barW = markSize * 0.18;
  const barH = markSize * 0.92;
  const rx = barW * 0.45;
  const x0 = cx - barW / 2;
  const y0 = cy - barH / 2;

  return (
    <svg
      width={w}
      height={h}
      viewBox={`0 0 ${w} ${h}`}
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      aria-label="ICS Logo"
    >
      {/* Asterisk mark */}
      <g transform={`translate(${cx},${cy})`}>
        <rect x={-barW/2} y={-barH/2} width={barW} height={barH} rx={rx} fill="#C31D27" />
        <rect x={-barW/2} y={-barH/2} width={barW} height={barH} rx={rx} fill="#C31D27" transform="rotate(60)" />
        <rect x={-barW/2} y={-barH/2} width={barW} height={barH} rx={rx} fill="#C31D27" transform="rotate(120)" />
      </g>

      {/* ICS wordmark */}
      <text
        x={markSize + h * 0.22}
        y={h * 0.76}
        fontFamily="Georgia, 'Times New Roman', serif"
        fontWeight="400"
        fontSize={h * 0.82}
        fill="#111111"
        letterSpacing="-0.02em"
      >
        ICS
      </text>
    </svg>
  );
}

/**
 * ICSMark — just the asterisk, no text. Used for avatars and small icons.
 */
export function ICSMark({ size = 28, color = '#C31D27' }) {
  const cx = size / 2;
  const barW = size * 0.18;
  const barH = size * 0.92;
  const rx = barW * 0.45;
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} xmlns="http://www.w3.org/2000/svg">
      <g transform={`translate(${cx},${cx})`}>
        <rect x={-barW/2} y={-barH/2} width={barW} height={barH} rx={rx} fill={color} />
        <rect x={-barW/2} y={-barH/2} width={barW} height={barH} rx={rx} fill={color} transform="rotate(60)" />
        <rect x={-barW/2} y={-barH/2} width={barW} height={barH} rx={rx} fill={color} transform="rotate(120)" />
      </g>
    </svg>
  );
}
