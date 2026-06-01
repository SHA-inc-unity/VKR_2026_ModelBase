import type { AnomalyTypeMeta } from './types';

export const PARAMS_KEY = 'modelline:params:anomaly';
export const ANOMALY_CACHE_TTL = 1800; // 30 minutes

export function anomalyCacheKey(symbol: string, timeframe: string): string {
  return `modelline:anomaly:v1:${symbol}:${timeframe}`;
}

export function loadParams() {
  if (typeof window === 'undefined') return null;
  try { const r = localStorage.getItem(PARAMS_KEY); return r ? JSON.parse(r) : null; }
  catch { return null; }
}

// ── Numeric dtype detection (mirrors backend whitelist) ──────────────────────

export const NUMERIC_TYPES = new Set([
  'numeric', 'double precision', 'real', 'integer', 'bigint', 'smallint',
]);
export const isNumeric = (dtype: string) => NUMERIC_TYPES.has(dtype.toLowerCase());

export function fmtNum(v: number | null | undefined, digits = 4): string {
  if (v === null || v === undefined || !isFinite(v)) return '–';
  const abs = Math.abs(v);
  if (abs === 0)     return '0';
  if (abs >= 1e6)    return v.toExponential(2);
  if (abs >= 1000)   return v.toFixed(0);
  if (abs >= 1)      return v.toFixed(Math.min(4, digits));
  return v.toPrecision(digits);
}

export const ANOMALY_TYPE_META: Record<string, AnomalyTypeMeta> = {
  duplicate:                { rank: 0, recommendedOp: 'drop_duplicates' },
  ohlc_violation:           { rank: 0, recommendedOp: 'fix_ohlc' },
  negative_value:           { rank: 0 },
  stale_price:              { rank: 0 },
  return_outlier:           { rank: 0 },
  gap:                      { rank: 1, recommendedOp: 'fill_gaps' },
  zero_streak:              { rank: 1, recommendedOp: 'fill_zero_streaks' },
  iqr:                      { rank: 1 },
  zscore:                   { rank: 1 },
  rolling_zscore:           { rank: 1 },
  rolling_iqr:              { rank: 1 },
  volume_turnover_mismatch: { rank: 1 },
};

export function metaFor(type: string): AnomalyTypeMeta {
  return ANOMALY_TYPE_META[type] ?? { rank: 2 };
}

// ── Stable order of anomaly type rows on the timeline chart ─────────────────
export const TIMELINE_TYPE_ORDER: string[] = [
  'duplicate', 'ohlc_violation', 'negative_value', 'stale_price', 'return_outlier',
  'gap', 'zero_streak', 'rolling_zscore', 'rolling_iqr',
  'iqr', 'zscore', 'volume_turnover_mismatch',
];
