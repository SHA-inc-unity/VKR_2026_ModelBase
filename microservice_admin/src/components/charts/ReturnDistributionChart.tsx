'use client';
import {
  ComposedChart,
  Bar,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
  Legend,
  type TooltipProps,
} from 'recharts';
import type { Locale } from '@/lib/i18n';
import { getAnomalyText } from '@/lib/anomalyTranslations';

export interface DistributionBin {
  x: number;        // bin centre
  count: number;    // observed count
  normal: number;   // expected count under N(mean, std)
}

export interface ReturnDistributionChartProps {
  data: DistributionBin[];
  locale: Locale;
  height?: number;
}

const MUTED_FG = 'hsl(215 20% 65%)';
const PRIMARY  = 'hsl(217 91% 60%)';
const ACCENT   = 'hsl(45 93% 55%)';
const BG_CARD  = 'hsl(222 47% 16%)';
const BORDER   = 'hsl(217 33% 22%)';
const GRID     = 'hsl(217 33% 22%)';

function fmtX(v: number): string {
  if (!isFinite(v)) return String(v);
  const abs = Math.abs(v);
  if (abs === 0) return '0';
  if (abs >= 0.01) return v.toFixed(3);
  return v.toExponential(1);
}

function CustomTooltip({ active, payload, locale }: TooltipProps<number, string> & { locale: Locale }) {
  if (!active || !payload?.length) return null;
  const item = payload[0].payload as DistributionBin;
  const text = getAnomalyText(locale);
  return (
    <div style={{
      background: BG_CARD, border: `1px solid ${BORDER}`,
      borderRadius: 6, padding: '6px 10px',
    }}>
      <p style={{ color: MUTED_FG, fontSize: 11, marginBottom: 2 }}>
        {text('logReturnApprox', { value: fmtX(item.x) })}
      </p>
      <p style={{ color: PRIMARY, fontSize: 12 }}>
        {text('observed')}: {item.count.toLocaleString()}
      </p>
      <p style={{ color: ACCENT, fontSize: 12 }}>
        {text('normal')}: {item.normal.toFixed(1)}
      </p>
    </div>
  );
}

/**
 * Histogram of log-returns with a normal-distribution overlay scaled to
 * expected counts per bin. Bars = observed, line = N(mean, std) reference
 * for visual normality assessment.
 */
export function ReturnDistributionChart({ data, locale, height = 260 }: ReturnDistributionChartProps) {
  const text = getAnomalyText(locale);
  const display = data.map(d => ({ ...d, label: fmtX(d.x) }));
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={display} margin={{ top: 12, right: 16, left: 0, bottom: 8 }}>
        <CartesianGrid stroke={GRID} strokeDasharray="3 3" vertical={false} />
        <XAxis
          dataKey="label"
          stroke={MUTED_FG}
          tick={{ fontSize: 10 }}
          interval="preserveStartEnd"
          minTickGap={20}
        />
        <YAxis stroke={MUTED_FG} tick={{ fontSize: 10 }} allowDecimals={false} />
        <Tooltip content={<CustomTooltip locale={locale} />} cursor={{ fill: 'hsl(217 33% 22% / 0.3)' }} />
        <Legend
          wrapperStyle={{ fontSize: 11 }}
          iconSize={10}
          formatter={v => v === 'count' ? text('observed') : text('normal')}
        />
        <Bar dataKey="count"  fill={PRIMARY} radius={[2, 2, 0, 0]} />
        <Line
          dataKey="normal"
          stroke={ACCENT}
          strokeWidth={2}
          dot={false}
          isAnimationActive={false}
          type="monotone"
        />
      </ComposedChart>
    </ResponsiveContainer>
  );
}
