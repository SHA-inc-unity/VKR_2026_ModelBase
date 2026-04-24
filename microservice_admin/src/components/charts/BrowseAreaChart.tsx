'use client';

import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';

// Matching colour scheme from HistogramChart.tsx
const MUTED_FG  = 'hsl(215 20% 65%)';
const PRIMARY   = 'hsl(217 91% 60%)';
const BG_CARD   = 'hsl(222 47% 16%)';
const BORDER    = 'hsl(217 33% 22%)';

interface DataPoint {
  ts: number;
  val: number;
}

interface Props {
  data: DataPoint[];
}

function fmtNum(v: number, digits = 4): string {
  const abs = Math.abs(v);
  if (abs === 0)   return '0';
  if (abs >= 1e6)  return v.toExponential(2);
  if (abs >= 1000) return v.toFixed(0);
  if (abs >= 1)    return v.toFixed(Math.min(4, digits));
  return v.toPrecision(digits);
}

const CustomTooltip = ({
  active,
  payload,
}: {
  active?: boolean;
  payload?: { value: number; payload: DataPoint }[];
}) => {
  if (!active || !payload || payload.length === 0) return null;
  const { ts, val } = payload[0].payload;
  return (
    <div
      style={{
        background: BG_CARD,
        border: `1px solid ${BORDER}`,
        borderRadius: 6,
        padding: '6px 10px',
        fontSize: 11,
        color: MUTED_FG,
        lineHeight: '1.6',
      }}
    >
      <div>{new Date(ts).toLocaleString()}</div>
      <div style={{ color: PRIMARY, fontWeight: 600 }}>{fmtNum(val)}</div>
    </div>
  );
};

export function BrowseAreaChart({ data }: Props) {
  return (
    <div style={{ width: '100%', height: 220, background: BG_CARD, borderRadius: 8, padding: '10px 4px 4px' }}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id="browseGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%"  stopColor={PRIMARY} stopOpacity={0.25} />
              <stop offset="95%" stopColor={PRIMARY} stopOpacity={0.0}  />
            </linearGradient>
          </defs>
          <CartesianGrid stroke={BORDER} strokeDasharray="3 3" vertical={false} />
          <XAxis
            dataKey="ts"
            tickFormatter={(v: number) => new Date(v).toLocaleDateString()}
            tick={{ fill: MUTED_FG, fontSize: 10 }}
            axisLine={{ stroke: BORDER }}
            tickLine={false}
          />
          <YAxis
            tickFormatter={(v: number) => fmtNum(v)}
            tick={{ fill: MUTED_FG, fontSize: 10 }}
            axisLine={false}
            tickLine={false}
            width={60}
          />
          <Tooltip content={<CustomTooltip />} cursor={{ stroke: BORDER }} />
          <Area
            type="monotone"
            dataKey="val"
            stroke={PRIMARY}
            strokeWidth={1.5}
            fill="url(#browseGrad)"
            dot={false}
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
