'use client';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
  type TooltipProps,
} from 'recharts';

export interface HistogramDatum {
  range_start: number;
  range_end: number;
  count: number;
}

export interface HistogramChartProps {
  data: HistogramDatum[];
  height?: number;
}

const MUTED_FG = 'hsl(215 20% 65%)';
const PRIMARY  = 'hsl(217 91% 60%)';
const BG_CARD  = 'hsl(222 47% 16%)';
const BORDER   = 'hsl(217 33% 22%)';
const GRID     = 'hsl(217 33% 22%)';

function fmt(n: number): string {
  if (!isFinite(n)) return String(n);
  const abs = Math.abs(n);
  if (abs === 0) return '0';
  if (abs >= 1000) return n.toFixed(0);
  if (abs >= 1)    return n.toFixed(2);
  return n.toPrecision(3);
}

function CustomTooltip({ active, payload }: TooltipProps<number, string>) {
  if (!active || !payload?.length) return null;
  const item = payload[0].payload as HistogramDatum;
  return (
    <div
      style={{
        background:   BG_CARD,
        border:       `1px solid ${BORDER}`,
        borderRadius: 6,
        padding:      '6px 10px',
      }}
    >
      <p style={{ color: MUTED_FG, fontSize: 11, marginBottom: 2 }}>
        [{fmt(item.range_start)}, {fmt(item.range_end)})
      </p>
      <p style={{ color: PRIMARY, fontWeight: 600, fontSize: 13 }}>
        count: {item.count.toLocaleString()}
      </p>
    </div>
  );
}

export function HistogramChart({ data, height = 240 }: HistogramChartProps) {
  const display = data.map(d => ({
    ...d,
    label: fmt(d.range_start),
  }));

  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={display} margin={{ top: 8, right: 12, left: 0, bottom: 8 }}>
        <CartesianGrid stroke={GRID} strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="label" stroke={MUTED_FG} tick={{ fontSize: 10 }} interval="preserveStartEnd" />
        <YAxis stroke={MUTED_FG} tick={{ fontSize: 10 }} allowDecimals={false} />
        <Tooltip content={<CustomTooltip />} cursor={{ fill: 'hsl(217 33% 22% / 0.4)' }} />
        <Bar dataKey="count" fill={PRIMARY} radius={[2, 2, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}
