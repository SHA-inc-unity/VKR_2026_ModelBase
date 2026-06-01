import type { TableCoverage } from '@/lib/types';

export const HEALTH_TIMEOUT   = 5_000;
export const TABLES_TIMEOUT   = 8_000;
export const COVERAGE_TIMEOUT = 5_000;

export const DASHBOARD_CACHE_KEY = 'modelline:dashboard:v1';
export const DASHBOARD_CACHE_TTL = 3600; // 1 hour

export interface DashboardCache {
  tables: string[];
  coverage: Record<string, TableCoverage>;
  modelCount: number | null;
}

export function hasTableRows(cv: TableCoverage | undefined): boolean {
  if (!cv?.exists) return false;
  if (cv.rows_known === false) return cv.max_ts_ms !== null;
  return (cv.rows ?? 0) > 0;
}

export type DashboardExchange = 'bybit' | 'binance' | 'kraken';
export type DashboardExchangeFilter = 'all' | DashboardExchange;

export const DASHBOARD_EXCHANGES: DashboardExchange[] = ['bybit', 'binance', 'kraken'];

export function getTableExchange(table: string): DashboardExchange {
  if (table.startsWith('binance_')) return 'binance';
  if (table.startsWith('kraken_')) return 'kraken';
  return 'bybit';
}

// ── Sub-components ──
export type AccentColor = 'primary' | 'success' | 'warning' | 'destructive';

export const ACCENT_BORDER: Record<AccentColor, string> = {
  primary:     'border-l-primary',
  success:     'border-l-success',
  warning:     'border-l-warning',
  destructive: 'border-l-destructive',
};
