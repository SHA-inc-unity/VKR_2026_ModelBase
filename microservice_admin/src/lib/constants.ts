// Shared constants for ModelLine Admin Panel

export const SYMBOLS = [
  'BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT',
  'ADAUSDT', 'DOGEUSDT', 'AVAXUSDT', 'DOTUSDT', 'LINKUSDT',
  'MATICUSDT', 'UNIUSDT', 'ATOMUSDT', 'LTCUSDT', 'NEARUSDT',
  'TONUSDT', 'SUIUSDT', 'APTUSDT',
] as const;

export const TIMEFRAMES = [
  '1m', '3m', '5m', '15m', '30m', '60m', '120m', '240m', '360m', '720m', '1d',
] as const;

export const TIMEFRAMES_ALL = ['ALL', ...TIMEFRAMES] as const;

export const TF_STEP_MS: Record<string, number> = {
  '1m':   60_000,
  '3m':   180_000,
  '5m':   300_000,
  '15m':  900_000,
  '30m':  1_800_000,
  '60m':  3_600_000,
  '120m': 7_200_000,
  '240m': 14_400_000,
  '360m': 21_600_000,
  '720m': 43_200_000,
  '1d':   86_400_000,
};

export function makeTableName(symbol: string, timeframe: string): string {
  return `${symbol.toLowerCase()}_${timeframe}`;
}

/** Format an epoch-ms timestamp as ISO `YYYY-MM-DD` in UTC. */
export function formatDateFromMs(ms: number | null | undefined): string | undefined {
  if (!ms) return undefined;
  return new Date(ms).toISOString().slice(0, 10);
}

/**
 * Compute coverage percentage from raw coverage metadata.
 *
 * Shared between Dashboard ("Available Tables") and Dataset ("Check Coverage")
 * pages — the backend also reports its own `coverage_pct`, but the UI
 * recomputes it client-side so every caller agrees on a single definition.
 *
 * Step is inferred from the table-name suffix (`btcusdt_5m` → `5m`).
 * Returns `null` when row count, timestamps, or timeframe are unavailable.
 */
export function getCoveragePct(
  table: string,
  cv: { rows?: number | null; min_ts_ms?: number | null; max_ts_ms?: number | null } | null | undefined,
): number | null {
  if (!cv?.rows || !cv.min_ts_ms || !cv.max_ts_ms) return null;
  const tf = table.split('_').pop();
  const stepMs = tf ? TF_STEP_MS[tf] : undefined;
  if (!stepMs) return null;
  const expected = Math.max(1, (cv.max_ts_ms - cv.min_ts_ms) / stepMs + 1);
  return Math.min(100, Math.round((cv.rows / expected) * 100));
}
