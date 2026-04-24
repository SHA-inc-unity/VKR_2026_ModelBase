'use client';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  type TooltipProps,
} from 'recharts';

// ── Types ─────────────────────────────────────────────────────────────────────
export interface BarDatum {
  name: string;
  pct: number;
}

export interface CoverageBarProps {
  data: BarDatum[];
  /** Chart height in px. @default 220 */
  height?: number;
}

// ── Constants (inline HSL so we don't rely on CSS vars in SVG context) ────────
const MUTED_FG = 'hsl(215 20% 65%)';
const PRIMARY  = 'hsl(217 91% 60%)';
const BG_CARD  = 'hsl(222 47% 16%)';
const BORDER   = 'hsl(217 33% 22%)';

// ── Custom tooltip ────────────────────────────────────────────────────────────
function CustomTooltip({ active, payload, label }: TooltipProps<number, string>) {
  if (!active || !payload?.length) return null;
  const val = Number(payload[0].value);
  return (
    <div
      style={{
        background:   BG_CARD,
        border:       `1px solid ${BORDER}`,
        borderRadius: 6,
        padding:      '6px 10px',
      }}
    >
      <p style={{ color: MUTED_FG, fontSize: 11, marginBottom: 2 }}>{label}</p>
      <p style={{ color: PRIMARY, fontWeight: 600, fontSize: 13 }}>
        {val.toFixed(1)}%
      </p>
    </div>
  );
}

// ── Component ─────────────────────────────────────────────────────────────────
export function CoverageBar({ data, height = 220 }: CoverageBarProps) {
  const display = data.map(d => ({
    ...d,
    name: d.name.length > 20 ? `${d.name.slice(0, 20)}\u2026` : d.name,
  }));

  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart
        layout="vertical"
        data={display}
        margin={{ top: 4, right: 32, bottom: 4, left: 8 }}
      >
        <XAxis
          type="number"
          domain={[0, 100]}
          tickFormatter={v => `${v}%`}
          tick={{ fill: MUTED_FG, fontSize: 11 }}
          axisLine={false}
          tickLine={false}
        />
        <YAxis
          type="category"
          dataKey="name"
          width={140}
          tick={{ fill: MUTED_FG, fontSize: 11 }}
          axisLine={false}
          tickLine={false}
        />
        <Tooltip
          content={<CustomTooltip />}
          cursor={{ fill: 'hsla(217, 33%, 20%, 0.5)' }}
        />
        <Bar dataKey="pct" fill={PRIMARY} radius={[0, 4, 4, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}
