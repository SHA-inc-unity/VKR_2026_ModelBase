'use client';
import {
  ScatterChart,
  Scatter,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
  Cell,
  type TooltipProps,
} from 'recharts';
import type { Locale } from '@/lib/i18n';
import {
  getAnomalySeverityLabel,
  getAnomalyTypeLabel,
  localizeAnomalyDetails,
} from '@/lib/anomalyTranslations';

export interface AnomalyTimelinePoint {
  ts: number;             // epoch ms — X axis
  type: string;           // anomaly_type — Y axis (categorical)
  severity: 'critical' | 'warning';
  value: number | null;
  details: string | null;
}

export interface AnomalyTimelineChartProps {
  data:   AnomalyTimelinePoint[];
  /** Order in which categories are stacked vertically (top → bottom). */
  types:  string[];
  locale: Locale;
  height?: number;
}

const MUTED_FG = 'hsl(215 20% 65%)';
const BG_CARD  = 'hsl(222 47% 16%)';
const BORDER   = 'hsl(217 33% 22%)';
const GRID     = 'hsl(217 33% 22%)';
const RED      = 'hsl(0 75% 60%)';
const YELLOW   = 'hsl(45 93% 55%)';

function fmtDate(ms: number): string {
  // YYYY-MM-DD HH:MM (UTC) — keeps the tooltip narrow.
  const d = new Date(ms);
  return d.toISOString().slice(0, 16).replace('T', ' ');
}

function CustomTooltip({ active, payload, locale }: TooltipProps<number, string> & { locale: Locale }) {
  if (!active || !payload?.length) return null;
  const item = payload[0].payload as AnomalyTimelinePoint;
  const detailText = localizeAnomalyDetails(locale, item.type, item.details) ?? item.details;
  return (
    <div
      style={{
        background:   BG_CARD,
        border:       `1px solid ${BORDER}`,
        borderRadius: 6,
        padding:      '6px 10px',
        maxWidth:     320,
      }}
    >
      <p style={{ color: MUTED_FG, fontSize: 11, marginBottom: 2 }}>
        {fmtDate(item.ts)}
      </p>
      <p style={{
        color: item.severity === 'critical' ? RED : YELLOW,
        fontWeight: 600,
        fontSize:   12,
        marginBottom: 2,
      }}>
        {getAnomalyTypeLabel(locale, item.type)} · {getAnomalySeverityLabel(locale, item.severity)}
      </p>
      {detailText && (
        <p style={{ color: MUTED_FG, fontSize: 10, lineHeight: 1.3 }}>
          {detailText}
        </p>
      )}
    </div>
  );
}

/**
 * Scatter chart with categorical Y axis — anomaly type per row, time on X,
 * point colour encodes severity. The categorical axis is implemented by
 * mapping each ``type`` to its index in ``types`` and rendering as a
 * numeric YAxis with custom ticks (works around recharts' fragile
 * ``type='category'`` behaviour for scatter charts).
 */
export function AnomalyTimelineChart({
  data, types, locale, height = 260,
}: AnomalyTimelineChartProps) {
  // Map type name → row index (0 = top). We invert later via reversed ticks.
  const indexByType = new Map(types.map((t, i) => [t, i]));
  const points = data
    .map(d => ({ ...d, y: indexByType.get(d.type) ?? -1 }))
    .filter(p => p.y >= 0);

  // Build numeric tick array; YAxis renders them with custom labels
  const ticks = types.map((_, i) => i);

  return (
    <ResponsiveContainer width="100%" height={height}>
      <ScatterChart margin={{ top: 12, right: 16, left: 8, bottom: 12 }}>
        <CartesianGrid stroke={GRID} strokeDasharray="3 3" />
        <XAxis
          type="number"
          dataKey="ts"
          domain={['dataMin', 'dataMax']}
          tickFormatter={v => fmtDate(v as number).slice(0, 10)}
          stroke={MUTED_FG}
          tick={{ fontSize: 10 }}
          minTickGap={40}
        />
        <YAxis
          type="number"
          dataKey="y"
          domain={[-0.5, types.length - 0.5]}
          ticks={ticks}
          tickFormatter={v => getAnomalyTypeLabel(locale, types[v as number] ?? '')}
          stroke={MUTED_FG}
          tick={{ fontSize: 10 }}
          width={150}
          interval={0}
        />
        <Tooltip content={<CustomTooltip locale={locale} />} cursor={{ strokeDasharray: '3 3' }} />
        <Scatter data={points} shape="circle">
          {points.map((p, i) => (
            <Cell key={i} fill={p.severity === 'critical' ? RED : YELLOW} />
          ))}
        </Scatter>
      </ScatterChart>
    </ResponsiveContainer>
  );
}
