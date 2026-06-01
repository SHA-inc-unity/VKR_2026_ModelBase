import type { IngestStage, RepairStage } from '@/lib/types';
import type { DatasetJobView } from '@/hooks/useDatasetJobs';

export const PARAMS_KEY = 'modelline:params:dataset';
export const PARAMS_TTL = 60 * 60 * 24 * 365 * 5;
export const CACHE_TABLES_KEY         = 'modelline:dataset-tables:v1';
export const CACHE_TABLES_TTL         = 3600; // 60 minutes
export const CACHE_COVERAGE_TTL       = 1800; // 30 minutes
export const ALL_INGEST_ERROR_RETENTION_MS = 10_000;
export const ACTIVE_EXCHANGES = [
  { value: 'bybit', label: 'Bybit' },
  { value: 'binance', label: 'Binance' },
  { value: 'kraken', label: 'Kraken' },
] as const;
export const EXCHANGES = [
  { value: 'all', label: 'ALL' },
  ...ACTIVE_EXCHANGES,
] as const;
export type DatasetExchange = typeof EXCHANGES[number]['value'];

export function coverageCacheKey(symbol: string, timeframe: string, exchange: DatasetExchange) {
  return `modelline:dataset-coverage:v1:${exchange}:${symbol}:${timeframe}`;
}
export function allCoverageCacheKey(symbol: string, exchange: DatasetExchange) {
  return `modelline:dataset-allcoverage:v1:${exchange}:${symbol}`;
}

export function todayStr() { return new Date().toISOString().slice(0, 10); }
export function daysAgoStr(n: number) {
  const d = new Date(); d.setDate(d.getDate() - n); return d.toISOString().slice(0, 10);
}

export function shortenMessage(value: string | null | undefined, max = 180): string {
  const normalized = (value ?? '').replace(/\s+/g, ' ').trim();
  if (!normalized) return '';
  return normalized.length <= max ? normalized : `${normalized.slice(0, max - 1)}…`;
}

export interface DatasetPageParams {
  symbol?: string;
  timeframe?: string;
  dateFrom?: string;
  dateTo?: string;
  exchange?: DatasetExchange;
}

export function loadParams(): DatasetPageParams | null {
  if (typeof window === 'undefined') return null;
  try { const r = localStorage.getItem(PARAMS_KEY); return r ? JSON.parse(r) : null; }
  catch { return null; }
}

export interface DataTableInfo {
  table_name: string;
  rows: number;
  rows_known?: boolean;
  coverage_pct?: number | null;
  date_from?: string;
  date_to?: string;
}

export interface CoverageResult {
  table_name: string;
  rows: number;
  rows_known?: boolean;
  expected: number | null;
  coverage_pct?: number | null;
  gaps: number | null;
}

export interface AllCoverageItem {
  tf: string;
  rows: number;
  rows_known?: boolean;
  coverage_pct?: number | null;
  date_from?: string;
  date_to?: string;
}

export function formatRows(rows: number | undefined, rowsKnown?: boolean): string {
  if (!rows || rows <= 0) return '—';
  return rowsKnown === false ? `~${rows.toLocaleString()}` : rows.toLocaleString();
}

// 'pending'  — local placeholder before kafkaCall returns (or before queue insert)
// 'queued'   — job persisted in DB, scheduler hasn't picked it up yet
// 'running'  — scheduler dispatched it; first progress event arrived
// 'done'/'error' — terminal
export type TfStatus = 'pending' | 'queued' | 'running' | 'done' | 'error';

export interface TfMeta {
  startedAt: number;
  runningAt?: number;
  endedAt?: number;
  rows?: number;
  pct?: number;    // live job progress 0–100
  stage?: string;  // current job stage label
  detail?: string; // backend-provided detail for the current step
  error?: string;  // error message for failed jobs
}

/**
 * Adaptive timeout: ~800 ms per 1 000 candles + 45 s base, capped at 10 min.
 * For 1m × 90 days ≈ 149 s; for 1d × 90 days ≈ 45 s.
 */
export function calcIngestTimeout(stepMs: number, startMs: number, endMs: number): number {
  const candles = Math.ceil((endMs - startMs) / stepMs);
  return Math.min(Math.round(candles / 1_000 * 800) + 45_000, 600_000);
}

export const INITIAL_STAGES: IngestStage[] = [
  { id: 'prepare',       label: 'Подготовка таблицы',    status: 'pending', progress: 0 },
  { id: 'fetch_klines',  label: 'Загрузка свечей',       status: 'pending', progress: 0 },
  { id: 'fetch_funding', label: 'Загрузка funding rate', status: 'pending', progress: 0 },
  { id: 'fetch_oi',      label: 'Загрузка open interest',status: 'pending', progress: 0 },
  { id: 'compute_rsi',   label: 'Вычисление RSI',        status: 'pending', progress: 0 },
  { id: 'upsert',        label: 'Запись в базу',         status: 'pending', progress: 0 },
];

// Stage templates for the two repair flows. The audit/repair pipeline emits
// only a subset of these IDs depending on which "Исправить" button was used.
export const INITIAL_REPAIR_STAGES_OHLCV: RepairStage[] = [
  { id: 'prepare', label: 'Подготовка',     status: 'pending', progress: 0 },
  { id: 'fetch',   label: 'Загрузка свечей', status: 'pending', progress: 0 },
  { id: 'upsert',  label: 'Запись в базу',   status: 'pending', progress: 0 },
];

export const INITIAL_REPAIR_STAGES_RECOMPUTE: RepairStage[] = [
  { id: 'prepare',   label: 'Подготовка',  status: 'pending', progress: 0 },
  { id: 'recompute', label: 'Пересчёт фич', status: 'pending', progress: 0 },
];

// Parse the canonical table name back into (exchange, SYMBOL, timeframe).
// Legacy Bybit tables remain `{symbol}_{timeframe}`; other exchanges are
// stored as `{exchange}_{symbol}_{timeframe}`.
export function parseTableName(table: string): { exchange: DatasetExchange; symbol: string; timeframe: string } | null {
  const parts = table.split('_');
  if (parts.length < 2) return null;
  const timeframe = parts.at(-1);
  if (!timeframe) return null;
  let exchange: DatasetExchange = 'bybit';
  let symbolParts = parts.slice(0, -1);
  const maybeExchange = symbolParts[0]?.toLowerCase();
  if (maybeExchange && ACTIVE_EXCHANGES.some((item) => item.value === maybeExchange)) {
    exchange = maybeExchange as DatasetExchange;
    symbolParts = symbolParts.slice(1);
  }
  if (symbolParts.length === 0) return null;
  return {
    exchange,
    symbol: symbolParts.join('_').toUpperCase(),
    timeframe,
  };
}

export function buildIngestScopeKey(exchange: DatasetExchange, symbol: string, timeframe: string): string {
  return `${exchange}::${symbol}::${timeframe}`;
}

export function parseIngestScopeKey(scopeKey: string): { exchange: DatasetExchange | null; symbol: string | null; timeframe: string } {
  const parts = scopeKey.split('::');
  if (parts.length >= 3) {
    return {
      exchange: parts[0] as DatasetExchange,
      symbol: parts[1] || null,
      timeframe: parts.slice(2).join('::'),
    };
  }
  if (parts.length === 2) {
    return {
      exchange: null,
      symbol: parts[0] || null,
      timeframe: parts[1],
    };
  }
  return {
    exchange: null,
    symbol: null,
    timeframe: scopeKey,
  };
}

export function formatIngestScopeLabel(scopeKey: string): string {
  const parsed = parseIngestScopeKey(scopeKey);
  const exchangeLabel = parsed.exchange
    ? EXCHANGES.find((item) => item.value === parsed.exchange)?.label ?? parsed.exchange.toUpperCase()
    : null;
  return [exchangeLabel, parsed.symbol, parsed.timeframe].filter(Boolean).join(' ');
}

// Strip rows, in execution order — matches INITIAL_STAGES ids one-to-one.
export const STRIP_STAGE_ORDER = ['prepare', 'fetch_klines', 'fetch_funding', 'fetch_oi', 'compute_rsi', 'upsert'] as const;

/** Index of the strip row that the backend job stage currently maps to.
 *  `compute_features`/`done` are past the strip (everything done); unknown → -1. */
export function jobStageToStripIndex(stage?: string | null): number {
  switch (stage) {
    case 'starting':
    case 'prepare':          return 0;
    case 'fetch':            // legacy alias for the kline fetch
    case 'fetch_klines':     return 1;
    case 'fetch_funding':    return 2;
    case 'fetch_oi':         return 3;
    case 'compute_rsi':      return 4;
    case 'upsert':           return 5;
    case 'compute_features':
    case 'done':             return STRIP_STAGE_ORDER.length; // past the strip
    default:                 return -1;
  }
}

// Slice of the overall progress each strip stage occupies. Used to derive a
// smooth, monotonic local 0–100 for stages the backend does NOT report a
// per-stage `stage_progress` for (prepare / funding / RSI). Monotonic because
// overall progress is monotonic.
export const STRIP_STAGE_OVERALL_RANGE: Record<string, [number, number]> = {
  prepare:       [0, 5],
  fetch_klines:  [5, 40],
  fetch_funding: [40, 42],
  fetch_oi:      [42, 50],
  compute_rsi:   [50, 70],
  upsert:        [70, 90],
};

export function clampPct(value: number): number {
  return Math.max(0, Math.min(100, Math.round(value)));
}

/** Local 0–100 for a running strip stage: prefer the backend's own
 *  per-stage `stage_progress`; otherwise map the overall % onto the stage's
 *  range so it still fills 0→100 within that stage. */
export function stripStageLocalPct(stageId: string, job: DatasetJobView): number {
  if (typeof job.stage_progress === 'number') return clampPct(job.stage_progress);
  const range = STRIP_STAGE_OVERALL_RANGE[stageId];
  if (!range) return clampPct(job.progress);
  const [lo, hi] = range;
  if (hi <= lo) return 100;
  return clampPct(((job.progress - lo) / (hi - lo)) * 100);
}

/** Map a running/finished job's state onto INITIAL_STAGES so IngestProgress
 *  shows a sequential, per-stage strip for job-based ingest. Each stage fills
 *  0→100 within itself; on completion every stage is marked done/100 (so the
 *  strip never freezes mid-run when the terminal SSE/poll update arrives). */
export function mapJobToStages(prev: IngestStage[], job: DatasetJobView): IngestStage[] {
  const succeeded = job.status === 'succeeded' || job.status === 'skipped';
  const failed = job.status === 'failed';
  const cur = jobStageToStripIndex(job.stage);
  return prev.map((s, i) => {
    if (succeeded) return { ...s, status: 'done' as const, progress: 100 };
    if (failed)    return { ...s, status: i <= cur && cur >= 0 ? 'error' as const : 'pending' as const };
    if (cur < 0)   return { ...s, status: 'pending' as const };
    if (i < cur)   return { ...s, status: 'done' as const, progress: 100 };
    if (i === cur) return { ...s, status: 'running' as const, progress: stripStageLocalPct(s.id, job) };
    return { ...s, status: 'pending' as const };
  });
}

export function humanizeJobStage(stage?: string | null): string {
  switch (stage) {
    case 'starting':
      return 'Запуск job';
    case 'prepare':
      return 'Подготовка таблицы';
    case 'fetch':
      return 'Загрузка источников';
    case 'fetch_klines':
      return 'Загрузка свечей';
    case 'fetch_funding':
      return 'Загрузка funding';
    case 'fetch_oi':
      return 'Загрузка open interest';
    case 'compute_rsi':
      return 'Расчёт RSI';
    case 'upsert':
      return 'Запись в БД';
    case 'compute_features':
      return 'Пересчёт фич';
    default:
      return stage ? stage.replace(/_/g, ' ') : 'Ожидание статуса';
  }
}

export const INGEST_EXECUTION_SLOT_COUNT = 4;

export function formatIngestSuccessToast(rows?: number | null): string {
  const completedRows = rows ?? 0;
  return completedRows > 0
    ? `Ingest завершён: ${completedRows.toLocaleString()} новых строк`
    : 'Ingest завершён: новых строк не потребовалось';
}

export function formatErrorHint(msg: string): string {
  if (/timeout|timed out/i.test(msg)) return 'Таймаут ответа';
  if (/table not found/i.test(msg))   return 'Таблица не найдена';
  const colonIdx = msg.indexOf('column_stats failed:');
  if (colonIdx !== -1) {
    const tail = msg.slice(colonIdx + 'column_stats failed:'.length).trim();
    return tail.length > 45 ? tail.slice(0, 45) + '…' : tail;
  }
  return msg.length > 45 ? msg.slice(0, 45) + '…' : msg;
}
