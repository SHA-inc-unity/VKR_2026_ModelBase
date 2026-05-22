'use client';
import dynamic from 'next/dynamic';
import { Fragment, useEffect, useMemo, useRef, useState } from 'react';
import { cacheRead, cacheWrite } from '@/lib/cacheClient';
import { Download, Info, Loader2, RefreshCw, ShieldAlert, Wand2 } from 'lucide-react';
import { kafkaCall } from '@/lib/kafkaClient';
import { Topics } from '@/lib/topics';
import { useToast } from '@/components/Toast';
import { SYMBOLS, TIMEFRAMES, makeTableName, formatDateFromMs, TF_STEP_MS } from '@/lib/constants';
import type { TableCoverage } from '@/lib/types';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Separator } from '@/components/ui/separator';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Collapsible } from '@/components/ui/collapsible';
import { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider } from '@/components/ui/tooltip';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { downloadCsv, downloadJson, buildReportFilename } from '@/lib/exportFile';
import { cn } from '@/lib/utils';
import { useLocale } from '@/lib/i18nContext';
import {
  getAnomalySeverityLabel,
  getAnomalySuggestion,
  getAnomalyText,
  getAnomalyTypeLabel,
  getCleanOpLabel,
  localizeAnomalyDetails,
  localizeAnomalyRuntimeMessage,
} from '@/lib/anomalyTranslations';

// Dynamic import — avoids Recharts SSR errors
const HistogramChart = dynamic(
  () => import('@/components/charts/HistogramChart').then(m => m.HistogramChart),
  { ssr: false, loading: () => <Skeleton className="h-[240px] w-full" /> },
);

const BrowseAreaChart = dynamic(
  () => import('@/components/charts/BrowseAreaChart').then(m => m.BrowseAreaChart),
  { ssr: false, loading: () => <Skeleton className="h-[220px] w-full" /> },
);

const AnomalyTimelineChart = dynamic(
  () => import('@/components/charts/AnomalyTimelineChart').then(m => m.AnomalyTimelineChart),
  { ssr: false, loading: () => <Skeleton className="h-[260px] w-full" /> },
);

const ReturnDistributionChart = dynamic(
  () => import('@/components/charts/ReturnDistributionChart').then(m => m.ReturnDistributionChart),
  { ssr: false, loading: () => <Skeleton className="h-[260px] w-full" /> },
);

const PARAMS_KEY = 'modelline:params:anomaly';
const ANOMALY_CACHE_TTL = 1800; // 30 minutes

function anomalyCacheKey(symbol: string, timeframe: string): string {
  return `modelline:anomaly:v1:${symbol}:${timeframe}`;
}

function loadParams() {
  if (typeof window === 'undefined') return null;
  try { const r = localStorage.getItem(PARAMS_KEY); return r ? JSON.parse(r) : null; }
  catch { return null; }
}

// ── Backend response shapes ──────────────────────────────────────────────────

interface ColumnStat {
  name: string;
  dtype: string;
  non_null: number;
  null_count: number;
  null_pct: number;
  min: number | null;
  max: number | null;
  mean: number | null;
  std: number | null;
}

interface ColumnStatsResponse {
  table: string;
  total_rows: number;
  columns: ColumnStat[];
  error?: string;
}

interface HistogramBucket {
  range_start: number;
  range_end: number;
  count: number;
}

interface HistogramResponse {
  column: string;
  min: number | null;
  max: number | null;
  buckets: HistogramBucket[];
  error?: string;
}

interface BrowseResponse {
  table: string;
  page: number;
  page_size: number;
  /**
   * Exact COUNT(*). Source of truth for pagination math.
   * Only present when the backend computed it (typically page 0).
   * Once received, the UI must pin this value and IGNORE the estimate
   * across subsequent pages — exact never gets overwritten by estimate.
   */
  total_rows: number | null;
  /**
   * pg_class.reltuples — informational only. May lag by a few percent.
   * Must NOT drive page-count math or button availability.
   */
  total_rows_estimate?: number | null;
  total_rows_known?: boolean;
  rows: Record<string, unknown>[];
  error?: string;
}

// ── Anomaly + clean response shapes ─────────────────────────────────────────

interface AnomalyRow {
  ts_ms: number;
  anomaly_type: string;
  severity: 'critical' | 'warning';
  column: string | null;
  value: number | null;
  details: string | null;
}

interface DetectAnomaliesResponse {
  table: string;
  total: number;
  critical: number;
  warning: number;
  by_type: Record<string, number>;
  /** Populated only when page/page_size are given in the request. */
  rows?: AnomalyRow[] | null;
  /** Up-to-200-row priority sample always returned by DataService. */
  sample?: AnomalyRow[];
  report_url?: string;
  has_more?: boolean;
  page?: number;
  page_size?: number;
  error?: string;
}

interface CleanPreviewResponse {
  table: string;
  counts: {
    drop_duplicates:      number;
    fix_ohlc:             number;
    fill_zero_streaks:    number;
    delete_by_timestamps: number;
    fill_gaps:            number;
  };
  error?: string;
}

interface CleanApplyResponse {
  table: string;
  audit_id: number;
  rows_affected: Record<string, number>;
  total: number;
  error?: string;
}

interface DatasetStatusResponse {
  loaded: boolean;
  symbol?: string;
  timeframe?: string;
  table_name?: string;
  row_count?: number;
  memory_mb_on_disk?: number;
  loaded_at?: number;
  error?: string;
}

interface DbscanResponse {
  summary?: {
    total_rows:  number;
    sample_size: number;
    n_clusters:  number;
    n_anomalies: number;
    eps:         number;
    min_samples: number;
    columns:     string[];
  };
  anomaly_timestamps_ms?: number[];
  error?: string;
}

interface IForestResponse {
  summary?: {
    total_rows:    number;
    sample_size:   number;
    n_anomalies:   number;
    contamination: number;
    n_estimators:  number;
    columns:       string[];
  };
  anomaly_timestamps_ms?: number[];
  error?: string;
}

interface DistributionBin { x: number; count: number; normal: number }
interface DistributionResponse {
  column?:   string;
  n?:        number;
  mean?:     number;
  std?:      number;
  skewness?: number;
  kurtosis?: number;
  jb_stat?:  number;
  jb_p?:     number;
  verdict?:  string;
  bins?:     DistributionBin[];
  error?:    string;
}

interface AuditLogEntry {
  id: number;
  table_name: string;
  operation: string;
  params: string;
  rows_affected: number;
  applied_at_ms: number;
}
interface AuditLogResponse { entries: AuditLogEntry[]; error?: string }

// ── Numeric dtype detection (mirrors backend whitelist) ──────────────────────

const NUMERIC_TYPES = new Set([
  'numeric', 'double precision', 'real', 'integer', 'bigint', 'smallint',
]);
const isNumeric = (dtype: string) => NUMERIC_TYPES.has(dtype.toLowerCase());

function fmtNum(v: number | null | undefined, digits = 4): string {
  if (v === null || v === undefined || !isFinite(v)) return '–';
  const abs = Math.abs(v);
  if (abs === 0)     return '0';
  if (abs >= 1e6)    return v.toExponential(2);
  if (abs >= 1000)   return v.toFixed(0);
  if (abs >= 1)      return v.toFixed(Math.min(4, digits));
  return v.toPrecision(digits);
}

// ── Severity ranking for Smart Suggestions ──────────────────────────────────
//
// Map each backend anomaly_type → (rank, recommendedOp). Lower rank = higher
// priority (critical first). The recommendedOp is a Clean-section checkbox
// key that the "Apply" button will toggle on before triggering Apply.

type CleanOpKey = 'drop_duplicates' | 'fix_ohlc' | 'fill_zero_streaks'
                | 'delete_by_timestamps' | 'fill_gaps';

interface AnomalyTypeMeta {
  rank: number;          // 0 = critical, 1 = warning, 2 = info
  recommendedOp?: CleanOpKey;
}

const ANOMALY_TYPE_META: Record<string, AnomalyTypeMeta> = {
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

function metaFor(type: string): AnomalyTypeMeta {
  return ANOMALY_TYPE_META[type] ?? { rank: 2 };
}

// ── Stable order of anomaly type rows on the timeline chart ─────────────────
const TIMELINE_TYPE_ORDER: string[] = [
  'duplicate', 'ohlc_violation', 'negative_value', 'stale_price', 'return_outlier',
  'gap', 'zero_streak', 'rolling_zscore', 'rolling_iqr',
  'iqr', 'zscore', 'volume_turnover_mismatch',
];

export default function AnomalyPage() {
  const { toast } = useToast();
  const { locale } = useLocale();
  const text = useMemo(() => getAnomalyText(locale), [locale]);

  const localizeError = (message: string) => localizeAnomalyRuntimeMessage(locale, message);
  const anomalyTypeLabel = (type: string) => getAnomalyTypeLabel(locale, type);
  const anomalySeverityLabel = (severity: 'critical' | 'warning' | 'info') =>
    getAnomalySeverityLabel(locale, severity);
  const anomalyDetailsLabel = (type: string, details: string | null | undefined, column?: string | null) =>
    localizeAnomalyDetails(locale, type, details, column) ?? details ?? '';

  const [symbol,    setSymbol]    = useState<string>('BTCUSDT');
  const [timeframe, setTimeframe] = useState<string>('5m');

  const [loadingAnalyze, setLoadingAnalyze] = useState(false);
  const [stats,    setStats]    = useState<ColumnStatsResponse | null>(null);
  const [coverage, setCoverage] = useState<TableCoverage | null>(null);

  // ── Detection-parameter state (Block 1) ──────────────────────────────────
  // All four new anomaly types share a single inline params block.
  const [rollingEnabled,  setRollingEnabled]  = useState<boolean>(true);
  const [rollingWindow,   setRollingWindow]   = useState<number>(96);
  const [rollingThr,      setRollingThr]      = useState<number>(4.5);
  const [rollingMode,     setRollingMode]     = useState<'zscore' | 'iqr'>('zscore');
  const [staleEnabled,    setStaleEnabled]    = useState<boolean>(true);
  const [staleMinLen,     setStaleMinLen]     = useState<number>(5);
  const [returnEnabled,   setReturnEnabled]   = useState<boolean>(true);
  const [returnThrPct,    setReturnThrPct]    = useState<number>(15);
  const [volMismatchEnabled, setVolMismatchEnabled] = useState<boolean>(true);
  const [volTolPct,       setVolTolPct]       = useState<number>(5);

  // Restore from cache when symbol or timeframe changes
  useEffect(() => {
    async function tryRestoreCache() {
      const cached = await cacheRead<{
        stats:      ColumnStatsResponse;
        coverage:   TableCoverage | null;
        anomalies?: DetectAnomaliesResponse | null;
      }>(
        anomalyCacheKey(symbol, timeframe),
      );
      if (cached) {
        setStats(cached.stats);
        setCoverage(cached.coverage);
        const cachedAnomalies = cached.anomalies ?? null;
        setAnomalies(cachedAnomalies && !cachedAnomalies.by_type
          ? { ...cachedAnomalies, by_type: {} }
          : cachedAnomalies);
        setAnomalyPage(0);
      } else {
        setStats(null);
        setCoverage(null);
        setAnomalies(null);
        setAnomalyPage(0);
      }
    }
    void tryRestoreCache();
  }, [symbol, timeframe]);

  // Expanded column → histogram cache / loading state
  const [expandedCol,  setExpandedCol]  = useState<string | null>(null);
  const [histogram,    setHistogram]    = useState<HistogramResponse | null>(null);
  const [histogramFor, setHistogramFor] = useState<string | null>(null);
  const [loadingHist,  setLoadingHist]  = useState(false);

  // ── Browse state ─────────────────────────────────────────────────────────
  const [browsePage,      setBrowsePage]      = useState(0);
  const [browsePageSize,  setBrowsePageSize]  = useState(50);
  const [browseOrderDesc, setBrowseOrderDesc] = useState(true);
  const [browseRows,      setBrowseRows]      = useState<Record<string, unknown>[] | null>(null);
  const [browseTotalRows, setBrowseTotalRows] = useState<number | null>(null);
  // Approximate row count (pg_class.reltuples). Informational only — must
  // never drive page-count math or button availability. Kept separate so
  // the exact value, once received, is never overwritten.
  const [browseTotalEstimate, setBrowseTotalEstimate] = useState<number | null>(null);
  const [browseLoading,   setBrowseLoading]   = useState(false);
  const [browseChartCol,  setBrowseChartCol]  = useState<string | null>(null);
  const [browseChartData, setBrowseChartData] = useState<{ ts: number; val: number }[] | null>(null);
  const [browseChartLoading, setBrowseChartLoading] = useState(false);
  const [browseColumns,   setBrowseColumns]   = useState<string[]>([]);

  // ── Anomaly detection state ──────────────────────────────────────────────
  const [anomalies,         setAnomalies]         = useState<DetectAnomaliesResponse | null>(null);
  const [anomalyFilterSev,  setAnomalyFilterSev]  = useState<'all' | 'critical' | 'warning'>('all');
  const [anomalyFilterType, setAnomalyFilterType] = useState<string>('all');
  const [anomalyPage,       setAnomalyPage]       = useState(0);
  const ANOMALY_PAGE_SIZE = 50;
  const [anomalyTab, setAnomalyTab] = useState<'timeline' | 'table' | 'dbscan' | 'iforest' | 'distribution' | 'history'>('timeline');

  // ── Dataset session (analitic) state ─────────────────────────────────────
  const [session,          setSession]          = useState<DatasetStatusResponse | null>(null);
  const [sessionLoading,   setSessionLoading]   = useState(false);
  const [sessionLoadError, setSessionLoadError] = useState<string | null>(null);

  // ── DBSCAN state ─────────────────────────────────────────────────────────
  const [dbscanEps,            setDbscanEps]            = useState<number>(0.5);
  const [dbscanMinSamples,     setDbscanMinSamples]     = useState<number>(5);
  const [dbscanMaxSampleRows,  setDbscanMaxSampleRows]  = useState<number>(50_000);
  const [dbscanResult,         setDbscanResult]         = useState<DbscanResponse | null>(null);
  const [dbscanLoading,        setDbscanLoading]        = useState(false);

  // ── Isolation Forest state (Block 2) ─────────────────────────────────────
  const [iforestContamination, setIforestContamination] = useState<number>(0.01);
  const [iforestNTrees,        setIforestNTrees]        = useState<number>(100);
  const [iforestMaxRows,       setIforestMaxRows]       = useState<number>(50_000);
  const [iforestResult,        setIforestResult]        = useState<IForestResponse | null>(null);
  const [iforestLoading,       setIforestLoading]       = useState(false);

  // ── Distribution state (Block 4) ─────────────────────────────────────────
  const [distResult,  setDistResult]  = useState<DistributionResponse | null>(null);
  const [distLoading, setDistLoading] = useState(false);

  // ── History / audit log state (Block 7) ──────────────────────────────────
  const [auditLog,         setAuditLog]         = useState<AuditLogResponse | null>(null);
  const [auditLogLoading,  setAuditLogLoading]  = useState(false);

  // ── Clean state ──────────────────────────────────────────────────────────
  const [cleanOps, setCleanOps] = useState({
    drop_duplicates:      false,
    fix_ohlc:             false,
    fill_zero_streaks:    false,
    delete_by_timestamps: false,
    fill_gaps:            false,
  });
  const [cleanPreview, setCleanPreview] = useState<CleanPreviewResponse | null>(null);
  const [cleanLoading, setCleanLoading] = useState(false);
  const [cleanApplying, setCleanApplying] = useState(false);

  // ── Block 5 — inline params for clean operations ─────────────────────────
  const [interpolationMethod, setInterpolationMethod] = useState<'forward_fill' | 'linear' | 'drop_rows'>('forward_fill');
  const [streakColumns, setStreakColumns] = useState<'all' | 'volume' | 'open_interest' | 'funding_rate'>('all');
  const [dedupStrategy, setDedupStrategy] = useState<'first' | 'last' | 'none'>('first');

  // ── Export-dialog state (Block 8) ────────────────────────────────────────
  const [exportOpen,    setExportOpen]    = useState(false);
  const [exportFormat,  setExportFormat]  = useState<'csv' | 'json'>('csv');
  const [exportSubset,  setExportSubset]  = useState<'all' | 'critical' | 'dbscan' | 'iforest'>('all');

  // Persist UI parameters across reloads.
  useEffect(() => {
    try {
      localStorage.setItem(PARAMS_KEY, JSON.stringify({
        symbol, timeframe,
        cleanOps, interpolationMethod, streakColumns, dedupStrategy,
        dbscanEps, dbscanMinSamples, dbscanMaxSampleRows,
        iforestContamination, iforestNTrees, iforestMaxRows,
        rollingEnabled, rollingWindow, rollingThr, rollingMode,
        staleEnabled, staleMinLen,
        returnEnabled, returnThrPct,
        volMismatchEnabled, volTolPct,
      }));
    } catch { /* ignore */ }
  }, [symbol, timeframe, cleanOps, interpolationMethod, streakColumns, dedupStrategy,
      dbscanEps, dbscanMinSamples, dbscanMaxSampleRows,
      iforestContamination, iforestNTrees, iforestMaxRows,
      rollingEnabled, rollingWindow, rollingThr, rollingMode,
      staleEnabled, staleMinLen, returnEnabled, returnThrPct,
      volMismatchEnabled, volTolPct]);

  // Restore persisted UI parameters on first client render (avoids SSR hydration mismatch).
  useEffect(() => {
    const p = loadParams();
    if (!p) return;
    if (p.symbol              !== undefined) setSymbol(p.symbol);
    if (p.timeframe           !== undefined) setTimeframe(p.timeframe);
    if (p.rollingEnabled      !== undefined) setRollingEnabled(p.rollingEnabled);
    if (p.rollingWindow       !== undefined) setRollingWindow(p.rollingWindow);
    if (p.rollingThr          !== undefined) setRollingThr(p.rollingThr);
    if (p.rollingMode         !== undefined) setRollingMode(p.rollingMode);
    if (p.staleEnabled        !== undefined) setStaleEnabled(p.staleEnabled);
    if (p.staleMinLen         !== undefined) setStaleMinLen(p.staleMinLen);
    if (p.returnEnabled       !== undefined) setReturnEnabled(p.returnEnabled);
    if (p.returnThrPct        !== undefined) setReturnThrPct(p.returnThrPct);
    if (p.volMismatchEnabled  !== undefined) setVolMismatchEnabled(p.volMismatchEnabled);
    if (p.volTolPct           !== undefined) setVolTolPct(p.volTolPct);
    if (p.dbscanEps            !== undefined) setDbscanEps(p.dbscanEps);
    if (p.dbscanMinSamples     !== undefined) setDbscanMinSamples(p.dbscanMinSamples);
    if (p.dbscanMaxSampleRows  !== undefined) setDbscanMaxSampleRows(p.dbscanMaxSampleRows);
    if (p.iforestContamination !== undefined) setIforestContamination(p.iforestContamination);
    if (p.iforestNTrees        !== undefined) setIforestNTrees(p.iforestNTrees);
    if (p.iforestMaxRows       !== undefined) setIforestMaxRows(p.iforestMaxRows);
    if (p.cleanOps) setCleanOps(prev => ({ ...prev, ...p.cleanOps }));
    if (p.interpolationMethod !== undefined) setInterpolationMethod(p.interpolationMethod);
    if (p.streakColumns       !== undefined) setStreakColumns(p.streakColumns);
    if (p.dedupStrategy       !== undefined) setDedupStrategy(p.dedupStrategy);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Mutex — prevents two long-running ops (analyze, apply, dbscan, load) from
  // racing each other and trampling shared session/anomaly state.
  const operationLockRef = useRef(false);

  // One-shot session probe on mount.
  useEffect(() => {
    let cancelled = false;
    void kafkaCall<DatasetStatusResponse>(Topics.CMD_ANALITIC_DATASET_STATUS, {})
      .then(res => { if (!cancelled) setSession(res); })
      .catch(() => { /* ignore — service might be down */ });
    return () => { cancelled = true; };
  }, []);

  const handleAnalyze = async () => {
    if (operationLockRef.current) {
      toast(text('operationInProgress'), 'error');
      return;
    }
    operationLockRef.current = true;
    setLoadingAnalyze(true);
    setExpandedCol(null);
    setHistogram(null);
    setHistogramFor(null);
    setBrowseRows(null);
    setBrowseTotalRows(null);
    setBrowseTotalEstimate(null);
    setBrowsePage(0);
    setBrowseChartCol(null);
    setBrowseChartData(null);
    setBrowseColumns([]);
    setAnomalies(null);
    setAnomalyPage(0);
    setCleanPreview(null);
    setDbscanResult(null);
    setIforestResult(null);
    setDistResult(null);

    try {
      const table  = makeTableName(symbol, timeframe);
      const stepMs = TF_STEP_MS[timeframe] ?? 0;

      const detectPayload: Record<string, unknown> = {
        table, step_ms: stepMs,
        rolling_enabled: rollingEnabled,
        rolling_column: 'close_price',
        rolling_window: rollingWindow,
        rolling_threshold: rollingThr,
        rolling_mode: rollingMode,
        stale_enabled: staleEnabled,
        stale_column:  'close_price',
        stale_min_len: staleMinLen,
        return_enabled: returnEnabled,
        return_column: 'close_price',
        return_threshold_pct: returnThrPct,
        volmismatch_enabled: volMismatchEnabled,
        volmismatch_tolerance_pct: volTolPct,
      };

      const [statsRes, covRes, anomaliesRes, sessionRes] = await Promise.all([
        kafkaCall<ColumnStatsResponse>(Topics.CMD_DATA_DATASET_COLUMN_STATS, { table }),
        kafkaCall<TableCoverage>(Topics.CMD_DATA_DATASET_COVERAGE, { table }).catch(() => null),
        kafkaCall<DetectAnomaliesResponse>(
          Topics.CMD_DATA_DATASET_DETECT_ANOMALIES,
          detectPayload,
          { timeoutMs: 180_000 },
        ).catch((e: unknown): DetectAnomaliesResponse => ({
          table, total: 0, critical: 0, warning: 0, by_type: {}, rows: null, sample: [],
          error: e instanceof Error ? e.message : String(e),
        })),
        kafkaCall<DatasetStatusResponse>(
          Topics.CMD_ANALITIC_DATASET_STATUS, {},
        ).catch(() => ({ loaded: false } as DatasetStatusResponse)),
      ]);
      if (statsRes.error) throw new Error(statsRes.error);
      setStats(statsRes);
      setCoverage(covRes);
      setAnomalies(anomaliesRes);
      setSession(sessionRes);

      void cacheWrite(
        anomalyCacheKey(symbol, timeframe),
        { stats: statsRes, coverage: covRes, anomalies: anomaliesRes },
        ANOMALY_CACHE_TTL,
      );

      // Background load into AnalyticService session if not already loaded.
      const alreadyLoaded =
        sessionRes.loaded &&
        sessionRes.symbol === symbol &&
        sessionRes.timeframe === timeframe;
      if (!alreadyLoaded) {
        setSessionLoading(true);
        kafkaCall<DatasetStatusResponse>(
          Topics.CMD_ANALITIC_DATASET_LOAD,
          { symbol, timeframe },
          { timeoutMs: 600_000 },
        )
          .then(res => {
            if (res.error) {
              toast(text('sessionLoadPrefix', { message: localizeError(res.error) }), 'error');
              setSession({ loaded: false });
              setSessionLoadError(res.error ?? null);
            } else {
              setSession(res);
              setSessionLoadError(null);
            }
          })
          .catch((e: unknown) => {
            const msg = e instanceof Error ? e.message : String(e);
            toast(text('sessionLoadPrefix', { message: localizeError(msg) }), 'error');
            setSession({ loaded: false });
            setSessionLoadError(msg);
          })
          .finally(() => setSessionLoading(false));
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      toast(localizeError(msg), 'error');
      setStats(null);
      setCoverage(null);
    } finally {
      setLoadingAnalyze(false);
      operationLockRef.current = false;
    }
  };

  const handleUnloadSession = async () => {
    setSessionLoading(true);
    try {
      await kafkaCall(Topics.CMD_ANALITIC_DATASET_UNLOAD, {});
      setSession({ loaded: false });
      setSessionLoadError(null);
      setDbscanResult(null);
      setIforestResult(null);
      setDistResult(null);
      toast(text('sessionCleared'), 'success');
    } catch (e) {
      toast(localizeError(e instanceof Error ? e.message : String(e)), 'error');
    } finally {
      setSessionLoading(false);
    }
  };

  const handleRunDbscan = async () => {
    if (operationLockRef.current) { toast(text('operationInProgress'), 'error'); return; }
    if (!session?.loaded) { toast(text('loadSessionFirst'), 'error'); return; }
    operationLockRef.current = true;
    setDbscanLoading(true);
    try {
      const res = await kafkaCall<DbscanResponse>(
        Topics.CMD_ANALITIC_ANOMALY_DBSCAN,
        {
          eps:             dbscanEps,
          min_samples:     dbscanMinSamples,
          max_sample_rows: dbscanMaxSampleRows,
        },
        { timeoutMs: 300_000 },
      );
      if (res.error) throw new Error(res.error);
      setDbscanResult(res);
    } catch (e) {
      toast(localizeError(e instanceof Error ? e.message : String(e)), 'error');
      setDbscanResult(null);
    } finally {
      setDbscanLoading(false);
      operationLockRef.current = false;
    }
  };

  const handleRunIForest = async () => {
    if (operationLockRef.current) { toast(text('operationInProgress'), 'error'); return; }
    if (!session?.loaded) { toast(text('loadSessionFirst'), 'error'); return; }
    operationLockRef.current = true;
    setIforestLoading(true);
    try {
      const res = await kafkaCall<IForestResponse>(
        Topics.CMD_ANALITIC_ANOMALY_ISOLATION_FOREST,
        {
          contamination:   iforestContamination,
          n_estimators:    iforestNTrees,
          max_sample_rows: iforestMaxRows,
        },
        { timeoutMs: 300_000 },
      );
      if (res.error) throw new Error(res.error);
      setIforestResult(res);
    } catch (e) {
      toast(localizeError(e instanceof Error ? e.message : String(e)), 'error');
      setIforestResult(null);
    } finally {
      setIforestLoading(false);
      operationLockRef.current = false;
    }
  };

  const handleRunDistribution = async () => {
    if (operationLockRef.current) { toast(text('operationInProgress'), 'error'); return; }
    if (!session?.loaded) { toast(text('loadSessionFirst'), 'error'); return; }
    operationLockRef.current = true;
    setDistLoading(true);
    try {
      const res = await kafkaCall<DistributionResponse>(
        Topics.CMD_ANALITIC_DATASET_DISTRIBUTION,
        { column: 'close_price', bins: 50 },
        { timeoutMs: 120_000 },
      );
      if (res.error) throw new Error(res.error);
      setDistResult(res);
    } catch (e) {
      toast(localizeError(e instanceof Error ? e.message : String(e)), 'error');
      setDistResult(null);
    } finally {
      setDistLoading(false);
      operationLockRef.current = false;
    }
  };

  const handleLoadAuditLog = async () => {
    setAuditLogLoading(true);
    try {
      const res = await kafkaCall<AuditLogResponse>(
        Topics.CMD_DATA_DATASET_AUDIT_LOG,
        { table: makeTableName(symbol, timeframe), limit: 100 },
      );
      if (res.error) throw new Error(res.error);
      setAuditLog(res);
    } catch (e) {
      toast(localizeError(e instanceof Error ? e.message : String(e)), 'error');
      setAuditLog({ entries: [], error: e instanceof Error ? e.message : String(e) });
    } finally {
      setAuditLogLoading(false);
    }
  };

  // Lazy-load history when user opens the History tab.
  useEffect(() => {
    if (anomalyTab === 'history' && auditLog === null && !auditLogLoading) {
      void handleLoadAuditLog();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [anomalyTab]);

  const handleCleanPreview = async () => {
    if (operationLockRef.current) { toast(text('operationInProgress'), 'error'); return; }
    operationLockRef.current = true;
    setCleanLoading(true);
    try {
      const table  = makeTableName(symbol, timeframe);
      const stepMs = TF_STEP_MS[timeframe] ?? 0;
      const res = await kafkaCall<CleanPreviewResponse>(
        Topics.CMD_DATA_DATASET_CLEAN_PREVIEW,
        { table, step_ms: stepMs },
        { timeoutMs: 120_000 },
      );
      if (res.error) throw new Error(res.error);
      setCleanPreview(res);
    } catch (e) {
      toast(localizeError(e instanceof Error ? e.message : String(e)), 'error');
      setCleanPreview(null);
    } finally {
      setCleanLoading(false);
      operationLockRef.current = false;
    }
  };

  const runCleanApply = async (overrideOps?: typeof cleanOps) => {
    const ops = overrideOps ?? cleanOps;
    if (operationLockRef.current) { toast(text('operationInProgress'), 'error'); return; }
    const anySelected = Object.values(ops).some(Boolean);
    if (!anySelected) { toast(text('selectAtLeastOneOperation'), 'error'); return; }
    if (!window.confirm(text('confirmMutation'))) return;
    operationLockRef.current = true;
    setCleanApplying(true);
    let shouldReanalyze = false;
    try {
      const table  = makeTableName(symbol, timeframe);
      const stepMs = TF_STEP_MS[timeframe] ?? 0;
      const res = await kafkaCall<CleanApplyResponse>(
        Topics.CMD_DATA_DATASET_CLEAN_APPLY,
        {
          table,
          step_ms: stepMs,
          confirm: true,
          interpolation_method: interpolationMethod,
          dedup_strategy: dedupStrategy,
          fill_zero_streaks_columns: streakColumns,
          ...ops,
        },
        { timeoutMs: 600_000 },
      );
      if (res.error) throw new Error(res.error);
      toast(text('appliedAudit', { total: res.total, auditId: res.audit_id }), 'success');
      setCleanPreview(null);
      // Mark audit-log cache as stale so the History tab refreshes.
      setAuditLog(null);
      shouldReanalyze = true;
    } catch (e) {
      toast(localizeError(e instanceof Error ? e.message : String(e)), 'error');
    } finally {
      setCleanApplying(false);
      operationLockRef.current = false;
      if (shouldReanalyze) void handleAnalyze();
    }
  };

  const handleCleanApply = () => runCleanApply();

  /** Smart Suggestions: enable a single op and immediately apply. */
  const applySuggestion = (op: CleanOpKey) => {
    setCleanOps(prev => ({ ...prev, [op]: true }));
    void runCleanApply({
      drop_duplicates:      false,
      fix_ohlc:             false,
      fill_zero_streaks:    false,
      delete_by_timestamps: false,
      fill_gaps:            false,
      [op]: true,
    });
  };

  // ── Browse helpers ────────────────────────────────────────────────────────

  const loadBrowse = async (page = browsePage, pageSize = browsePageSize, orderDesc = browseOrderDesc) => {
    setBrowseLoading(true);
    setBrowseChartCol(null);
    setBrowseChartData(null);
    try {
      const table = makeTableName(symbol, timeframe);
      const res = await kafkaCall<BrowseResponse>(
        Topics.CMD_DATA_DATASET_BROWSE,
        { table, page, page_size: pageSize, order: orderDesc ? 'desc' : 'asc' },
      );
      if (res.error) throw new Error(res.error);
      setBrowseRows(res.rows);
      // Pin exact total once we have it; never let estimate overwrite it.
      if (typeof res.total_rows === 'number') {
        setBrowseTotalRows(res.total_rows);
      }
      if (typeof res.total_rows_estimate === 'number') {
        setBrowseTotalEstimate(res.total_rows_estimate);
      }
      setBrowsePage(res.page);
      if (res.rows.length > 0) {
        const allKeys = Object.keys(res.rows[0]);
        setBrowseColumns(allKeys);
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      toast(localizeError(msg), 'error');
    } finally {
      setBrowseLoading(false);
    }
  };

  const loadColumnChart = async (colName: string) => {
    if (browseChartCol === colName) {
      setBrowseChartCol(null);
      setBrowseChartData(null);
      return;
    }
    setBrowseChartLoading(true);
    setBrowseChartCol(colName);
    setBrowseChartData(null);
    try {
      const table = makeTableName(symbol, timeframe);
      const res = await kafkaCall<BrowseResponse>(
        Topics.CMD_DATA_DATASET_BROWSE,
        { table, page: 0, page_size: 500, order: 'asc' },
      );
      if (res.error) throw new Error(res.error);
      const data = (res.rows as Record<string, unknown>[])
        .map(row => {
          const ts = row['timestamp_utc'];
          const val = row[colName];
          if (ts == null || val == null) return null;
          const tsNum = typeof ts === 'number' ? ts : new Date(ts as string).getTime();
          const valNum = typeof val === 'number' ? val : parseFloat(String(val));
          if (isNaN(tsNum) || isNaN(valNum)) return null;
          return { ts: tsNum, val: valNum };
        })
        .filter((x): x is { ts: number; val: number } => x !== null);
      setBrowseChartData(data);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      toast(localizeError(msg), 'error');
      setBrowseChartCol(null);
    } finally {
      setBrowseChartLoading(false);
    }
  };

  // Pagination math uses ONLY the exact total. Estimate is informational.
  // When exact is unknown, page-count is null and "Next" availability is
  // derived from the actual rows returned (see hasNextPage below).
  const browseTotalPages = browseTotalRows !== null && browsePageSize > 0
    ? Math.ceil(browseTotalRows / browsePageSize)
    : null;
  // "Next" button: enabled when either (a) exact total says there's more, or
  // (b) exact unknown and the last page came back full (likely more rows).
  const hasNextPage = browseTotalPages !== null
    ? browsePage + 1 < browseTotalPages
    : (browseRows?.length ?? 0) >= browsePageSize;

  const handleToggleColumn = async (col: ColumnStat) => {
    if (!isNumeric(col.dtype)) return;
    if (expandedCol === col.name) {
      setExpandedCol(null);
      return;
    }
    setExpandedCol(col.name);
    if (histogramFor === col.name && histogram) return; // already cached
    setLoadingHist(true);
    try {
      const table = makeTableName(symbol, timeframe);
      const res = await kafkaCall<HistogramResponse>(
        Topics.CMD_DATA_DATASET_COLUMN_HISTOGRAM,
        { table, column: col.name, buckets: 30 },
      );
      if (res.error) throw new Error(res.error);
      setHistogram(res);
      setHistogramFor(col.name);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      toast(localizeError(msg), 'error');
      setHistogram(null);
      setHistogramFor(null);
    } finally {
      setLoadingHist(false);
    }
  };

  // Summary metrics
  const avgNullPct = stats && stats.columns.length > 0
    ? stats.columns.reduce((s, c) => s + c.null_pct, 0) / stats.columns.length
    : null;
  const dateFrom = formatDateFromMs(coverage?.min_ts_ms ?? null);
  const dateTo   = formatDateFromMs(coverage?.max_ts_ms ?? null);

  // ── Smart Suggestions list (Block 6) ─────────────────────────────────────
  const suggestions = useMemo(() => {
    if (!anomalies || anomalies.error) return [];
    return Object.entries(anomalies.by_type ?? {})
      .filter(([, count]) => count > 0)
      .map(([type, count]) => {
        const meta = metaFor(type);
        return { type, count, ...meta, recommendation: getAnomalySuggestion(locale, type) };
      })
      .sort((a, b) => a.rank - b.rank || b.count - a.count);
  }, [anomalies, locale]);

  // ── Timeline data (Block 3) ─────────────────────────────────────────────
  const timelineData = useMemo(() => {
    const rows = anomalies?.rows ?? anomalies?.sample ?? [];
    if (!rows.length) return [];
    return rows
      .filter(r => r.severity === 'critical' || r.severity === 'warning')
      .map(r => ({
        ts: r.ts_ms,
        type: r.anomaly_type,
        severity: r.severity,
        value: r.value,
        details: r.details,
      }));
  }, [anomalies]);

  const timelineTypes = useMemo(() => {
    if (!anomalies) return [];
    const seen = new Set(Object.keys(anomalies.by_type ?? {}));
    // Stable order — known types in their canonical position, then any unknown.
    const ordered = TIMELINE_TYPE_ORDER.filter(t => seen.has(t));
    const extras  = [...seen].filter(t => !ordered.includes(t)).sort();
    return [...ordered, ...extras];
  }, [anomalies]);

  // ── Export (Block 8) ─────────────────────────────────────────────────────
  const handleExport = () => {
    if (!anomalies) {
      toast(text('runAnalyzeFirst'), 'error');
      return;
    }
    let rows: Array<Record<string, unknown>> = [];
    const ruleRows = anomalies.rows ?? anomalies.sample ?? [];
    if (exportSubset === 'all') {
      rows = ruleRows.map(r => ({ ...r, source: 'rule' }));
    } else if (exportSubset === 'critical') {
      rows = ruleRows
        .filter(r => r.severity === 'critical')
        .map(r => ({ ...r, source: 'rule' }));
    } else if (exportSubset === 'dbscan') {
      const ts = dbscanResult?.anomaly_timestamps_ms ?? [];
      rows = ts.map(t => ({
        ts_ms: t,
        anomaly_type: 'dbscan',
        severity: 'warning',
        column: null, value: null,
        details: text('detailMultivariateOutlier'),
        source: 'dbscan',
      }));
    } else {
      const ts = iforestResult?.anomaly_timestamps_ms ?? [];
      rows = ts.map(t => ({
        ts_ms: t,
        anomaly_type: 'isolation_forest',
        severity: 'warning',
        column: null, value: null,
        details: text('detailIsolationForestOutlier'),
        source: 'iforest',
      }));
    }
    if (rows.length === 0) {
      toast(text('nothingToExport'), 'error');
      return;
    }
    const fname = buildReportFilename(symbol, timeframe, exportFormat);
    if (exportFormat === 'csv') {
      downloadCsv(
        rows,
        fname,
        ['ts_ms', 'anomaly_type', 'severity', 'column', 'value', 'details', 'source'],
      );
    } else {
      downloadJson({
        symbol, timeframe,
        generated_at_ms: Date.now(),
        subset: exportSubset,
        count: rows.length,
        rows,
      }, fname);
    }
    setExportOpen(false);
  };

  return (
    <TooltipProvider>
    <div className="flex flex-col gap-4 sm:gap-6 w-full">
      <header className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <ShieldAlert className="w-6 h-6 text-primary" />
          <h1 className="text-2xl font-bold tracking-tight">{text('pageTitle')}</h1>
        </div>
        <div className="relative">
          <Button
            variant="outline"
            size="sm"
            className="gap-2"
            onClick={() => setExportOpen(o => !o)}
            disabled={!anomalies}
          >
            <Download className="w-3.5 h-3.5" />
            {text('export')}
          </Button>
          {exportOpen && (
            <div className="absolute right-0 top-full mt-2 z-20 w-72 rounded-md border border-border bg-card shadow-lg p-3 space-y-2">
              <p className="text-xs font-semibold">{text('exportReport')}</p>
              <div className="flex flex-col gap-1.5">
                <label className="text-[11px] text-muted-foreground">{text('format')}</label>
                <Select value={exportFormat} onValueChange={v => setExportFormat(v as 'csv' | 'json')}>
                  <SelectTrigger className="h-8"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="csv">CSV</SelectItem>
                    <SelectItem value="json">JSON</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="flex flex-col gap-1.5">
                <label className="text-[11px] text-muted-foreground">{text('subset')}</label>
                <Select value={exportSubset} onValueChange={v => setExportSubset(v as typeof exportSubset)}>
                  <SelectTrigger className="h-8"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">{text('allAnomalies')}</SelectItem>
                    <SelectItem value="critical">{text('onlyCritical')}</SelectItem>
                    <SelectItem value="dbscan">{text('onlyDbscan')}</SelectItem>
                    <SelectItem value="iforest">{text('onlyIForest')}</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="flex gap-2 pt-1">
                <Button size="sm" className="flex-1" onClick={handleExport}>{text('download')}</Button>
                <Button size="sm" variant="ghost" onClick={() => setExportOpen(false)}>{text('cancel')}</Button>
              </div>
            </div>
          )}
        </div>
      </header>

      {/* ── Top control bar ── */}
      <Card>
        <CardContent className="pt-5 pb-5">
          <div className="flex flex-wrap items-end gap-3 sm:gap-4">
            <div className="flex flex-col gap-1.5 w-full xs:w-auto min-w-0 flex-1 xs:flex-initial xs:min-w-[180px]">
              <label className="text-xs text-muted-foreground flex items-center gap-0.5">{text('symbolLabel')} <InfoTip text={text('symbolInfo')} /></label>
              <Select value={symbol} onValueChange={setSymbol}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>{SYMBOLS.map(s => <SelectItem key={s} value={s}>{s}</SelectItem>)}</SelectContent>
              </Select>
            </div>
            <div className="flex flex-col gap-1.5 w-full xs:w-auto min-w-0 flex-1 xs:flex-initial xs:min-w-[140px]">
              <label className="text-xs text-muted-foreground flex items-center gap-0.5">{text('timeframeLabel')} <InfoTip text={text('timeframeInfo')} /></label>
              <Select value={timeframe} onValueChange={setTimeframe}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>{TIMEFRAMES.map(t => <SelectItem key={t} value={t}>{t}</SelectItem>)}</SelectContent>
              </Select>
            </div>
            <Button onClick={handleAnalyze} disabled={loadingAnalyze} className="gap-2 w-full xs:w-auto">
              {loadingAnalyze ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
              {text('analyze')}
            </Button>
            {stats && (
              <p className="text-xs text-muted-foreground ml-auto">
                {text('tableLabel')}: <span className="font-mono text-foreground">{stats.table}</span>
              </p>
            )}
          </div>

          {(session?.loaded || sessionLoading) && (
            <div className="mt-3 flex items-center gap-2 flex-wrap">
              <Badge
                className={cn(
                  'gap-1.5',
                  session?.loaded ? 'bg-emerald-500/15 text-emerald-600 hover:bg-emerald-500/20' : 'bg-muted',
                )}
              >
                {sessionLoading && <Loader2 className="w-3 h-3 animate-spin" />}
                {session?.loaded
                  ? text('sessionLoaded', {
                      symbol: session.symbol ?? symbol,
                      timeframe: session.timeframe ?? timeframe,
                      rows: session.row_count?.toLocaleString() ?? '0',
                      memory: session.memory_mb_on_disk?.toFixed(1) ?? '0.0',
                    })
                  : text('sessionLoading')}
              </Badge>
              {session?.loaded && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-7 text-xs"
                  onClick={handleUnloadSession}
                  disabled={sessionLoading}
                >
                  {text('unload')}
                </Button>
              )}
            </div>
          )}
          {!session?.loaded && !sessionLoading && sessionLoadError && (
            <div className="mt-3">
              <Badge className="gap-1.5 bg-destructive/15 text-destructive hover:bg-destructive/20">
                <ShieldAlert className="w-3 h-3" />
                {text('sessionLoadFailed', { message: localizeError(sessionLoadError) })}
              </Badge>
            </div>
          )}
        </CardContent>
      </Card>

      {/* ── Detection parameters (Block 1) ── */}
      <Collapsible title={text('detectionParameters')}>
        <div className="p-4 grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
          <ParamSection
            title={text('rollingSectionTitle')}
            info={text('rollingSectionInfo')}
            enabled={rollingEnabled}
            onToggle={setRollingEnabled}
          >
            <ParamRow label={text('mode')} info={text('rollingModeInfo')}>
              <Select value={rollingMode} onValueChange={v => setRollingMode(v as 'zscore' | 'iqr')}>
                <SelectTrigger className="h-8 w-32"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="zscore">{text('zscore')}</SelectItem>
                  <SelectItem value="iqr">{text('iqr')}</SelectItem>
                </SelectContent>
              </Select>
            </ParamRow>
            <ParamRow label={text('windowBars')} info={text('rollingWindowInfo')}>
              <NumInput value={rollingWindow} onChange={setRollingWindow} min={5} step={1} />
            </ParamRow>
            <ParamRow label={rollingMode === 'iqr' ? text('tukeyK') : text('thresholdSigma')} info={rollingMode === 'iqr' ? text('rollingThresholdInfoIqr') : text('rollingThresholdInfo')}>
              <NumInput value={rollingThr} onChange={setRollingThr} min={0.5} step={0.1} />
            </ParamRow>
          </ParamSection>

          <ParamSection
            title={text('staleSectionTitle')}
            info={text('staleSectionInfo')}
            enabled={staleEnabled}
            onToggle={setStaleEnabled}
          >
            <ParamRow label={text('minConsecutive')} info={text('staleMinInfo')}>
              <NumInput value={staleMinLen} onChange={setStaleMinLen} min={2} step={1} />
            </ParamRow>
          </ParamSection>

          <ParamSection
            title={text('returnSectionTitle')}
            info={text('returnSectionInfo')}
            enabled={returnEnabled}
            onToggle={setReturnEnabled}
          >
            <ParamRow label={text('thresholdPercent')} info={text('returnThresholdInfo')}>
              <NumInput value={returnThrPct} onChange={setReturnThrPct} min={0.1} step={0.5} />
            </ParamRow>
          </ParamSection>

          <ParamSection
            title={text('volumeMismatchTitle')}
            info={text('volumeMismatchInfo')}
            enabled={volMismatchEnabled}
            onToggle={setVolMismatchEnabled}
          >
            <ParamRow label={text('tolerancePercent')} info={text('volumeMismatchToleranceInfo')}>
              <NumInput value={volTolPct} onChange={setVolTolPct} min={0.1} step={0.5} />
            </ParamRow>
          </ParamSection>
        </div>
      </Collapsible>

      {/* ── Section: Inspect (open by default) ── */}
      <Collapsible title={text('inspect')} defaultOpen>
        <div className="p-4 space-y-4">
          {!stats && !loadingAnalyze && (
            <p className="text-sm text-muted-foreground">
              {text('selectAndAnalyze')}
            </p>
          )}

          {loadingAnalyze && (
            <div className="space-y-3">
              <Skeleton className="h-16 w-full" />
              <Skeleton className="h-[300px] w-full" />
            </div>
          )}

          {stats && !loadingAnalyze && (
            <>
              <div className="grid grid-cols-1 xs:grid-cols-2 md:grid-cols-4 gap-3 sm:gap-4">
                <div>
                  <p className="text-xs text-muted-foreground">{text('totalRows')}</p>
                  <p className="text-lg font-bold tabular-nums">{stats.total_rows.toLocaleString()}</p>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground">{text('columns')}</p>
                  <p className="text-lg font-bold tabular-nums">{stats.columns.length}</p>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground">{text('avgNullPct')}</p>
                  <p className={cn(
                    'text-lg font-bold tabular-nums',
                    avgNullPct !== null && avgNullPct > 20 && 'text-destructive',
                    avgNullPct !== null && avgNullPct > 5 && avgNullPct <= 20 && 'text-warning',
                  )}>
                    {avgNullPct !== null ? `${avgNullPct.toFixed(2)}%` : '–'}
                  </p>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground">{text('dateRange')}</p>
                  <p className="text-sm font-semibold">
                    {dateFrom && dateTo ? `${dateFrom} → ${dateTo}` : '–'}
                  </p>
                </div>
              </div>

              <Separator />

              <div className="rounded-md border border-border overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>{text('column')}</TableHead>
                      <TableHead>{text('dtype')}</TableHead>
                      <TableHead className="text-right">{text('nonNull')}</TableHead>
                      <TableHead className="text-right">{text('null')}</TableHead>
                      <TableHead className="text-right">{text('nullPct')}</TableHead>
                      <TableHead className="text-right">{text('min')}</TableHead>
                      <TableHead className="text-right">{text('max')}</TableHead>
                      <TableHead className="text-right">{text('mean')}</TableHead>
                      <TableHead className="text-right">{text('std')}</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {stats.columns.map(col => {
                      const numeric = isNumeric(col.dtype);
                      const isOpen  = expandedCol === col.name;
                      return (
                        <Fragment key={col.name}>
                          <TableRow
                            onClick={() => handleToggleColumn(col)}
                            className={cn(
                              numeric && 'cursor-pointer hover:bg-accent/40',
                              isOpen && 'bg-accent/30',
                            )}
                          >
                            <TableCell className="font-mono text-xs">{col.name}</TableCell>
                            <TableCell className="text-xs text-muted-foreground">{col.dtype}</TableCell>
                            <TableCell className="text-xs text-right tabular-nums">{col.non_null.toLocaleString()}</TableCell>
                            <TableCell className="text-xs text-right tabular-nums">{col.null_count.toLocaleString()}</TableCell>
                            <TableCell className="text-right">
                              {col.null_pct > 20 ? (
                                <Badge variant="destructive" className="tabular-nums">{col.null_pct.toFixed(1)}%</Badge>
                              ) : col.null_pct > 5 ? (
                                <Badge className="tabular-nums bg-warning/20 text-warning hover:bg-warning/30">{col.null_pct.toFixed(1)}%</Badge>
                              ) : (
                                <span className="text-xs tabular-nums text-muted-foreground">{col.null_pct.toFixed(1)}%</span>
                              )}
                            </TableCell>
                            <TableCell className="text-xs text-right tabular-nums">{fmtNum(col.min)}</TableCell>
                            <TableCell className="text-xs text-right tabular-nums">{fmtNum(col.max)}</TableCell>
                            <TableCell className="text-xs text-right tabular-nums">{fmtNum(col.mean)}</TableCell>
                            <TableCell className="text-xs text-right tabular-nums">{fmtNum(col.std)}</TableCell>
                          </TableRow>
                          {isOpen && (
                            <TableRow key={`${col.name}-hist`}>
                              <TableCell colSpan={9} className="p-4 bg-muted/20">
                                <div className="flex items-center justify-between mb-2">
                                  <p className="text-xs font-semibold">{text('distributionFor', { column: col.name })}</p>
                                  {histogram && histogramFor === col.name && (
                                    <p className="text-xs text-muted-foreground">
                                      {text('histogramSummary', {
                                        count: histogram.buckets.length,
                                        min: fmtNum(histogram.min),
                                        max: fmtNum(histogram.max),
                                      })}
                                    </p>
                                  )}
                                </div>
                                {loadingHist && (histogramFor !== col.name) ? (
                                  <Skeleton className="h-[240px] w-full" />
                                ) : histogram && histogramFor === col.name ? (
                                  histogram.buckets.length > 0 ? (
                                    <HistogramChart data={histogram.buckets} countLabel={text('tooltipCount')} />
                                  ) : (
                                    <p className="text-xs text-muted-foreground">{text('noNonNullValues')}</p>
                                  )
                                ) : (
                                  <Skeleton className="h-[240px] w-full" />
                                )}
                              </TableCell>
                            </TableRow>
                          )}
                        </Fragment>
                      );
                    })}
                  </TableBody>
                </Table>
              </div>
            </>
          )}
        </div>
      </Collapsible>

      {/* ── Section: Browse ── */}
      <Collapsible title={text('browse')}>
        <div className="p-4 space-y-4">
          <div className="flex flex-wrap items-end gap-3">
            <div className="flex flex-col gap-1.5">
              <label className="text-xs text-muted-foreground">{text('rowsPerPage')}</label>
              <Select
                value={String(browsePageSize)}
                onValueChange={v => { const n = parseInt(v, 10); setBrowsePageSize(n); }}
              >
                <SelectTrigger className="w-[110px]"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {[10, 25, 50, 100, 250, 500].map(n => (
                    <SelectItem key={n} value={String(n)}>{n}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="flex flex-col gap-1.5">
              <label className="text-xs text-muted-foreground">{text('order')}</label>
              <Select
                value={browseOrderDesc ? 'desc' : 'asc'}
                onValueChange={v => setBrowseOrderDesc(v === 'desc')}
              >
                <SelectTrigger className="w-[110px]"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="desc">{text('newestFirst')}</SelectItem>
                  <SelectItem value="asc">{text('oldestFirst')}</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <Button
              onClick={() => loadBrowse(0, browsePageSize, browseOrderDesc)}
              disabled={browseLoading}
              className="gap-2"
            >
              {browseLoading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
              {text('load')}
            </Button>
            {browseTotalRows !== null ? (
              <p className="text-xs text-muted-foreground ml-auto">
                {text('exactRowsTotal', { count: browseTotalRows.toLocaleString() })}
              </p>
            ) : browseTotalEstimate !== null ? (
              <p className="text-xs text-muted-foreground ml-auto" title={text('estimateTitle')}>
                {text('estimateRowsTotal', { count: browseTotalEstimate.toLocaleString() })}
              </p>
            ) : null}
          </div>

          {browseRows && browseColumns.length > 0 && (
            <>
              <div className="rounded-md border border-border overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      {browseColumns.map(col => {
                        const isActive = browseChartCol === col;
                        const sample = browseRows[0]?.[col];
                        const isNum  = col !== 'timestamp_utc' && (typeof sample === 'number' || (typeof sample === 'string' && !isNaN(parseFloat(sample))));
                        return (
                          <TableHead
                            key={col}
                            className={cn(
                              'text-xs whitespace-nowrap',
                              isNum && 'cursor-pointer select-none',
                              isActive && 'text-primary',
                            )}
                            onClick={() => isNum ? loadColumnChart(col) : undefined}
                          >
                            {col}
                            {isNum && (
                              <span className="ml-1 text-[10px] opacity-50">▲</span>
                            )}
                          </TableHead>
                        );
                      })}
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {browseRows.map((row, i) => (
                      <TableRow key={i}>
                        {browseColumns.map(col => {
                          const v = row[col];
                          return (
                            <TableCell key={col} className="text-xs font-mono whitespace-nowrap py-1.5">
                              {v == null ? <span className="text-muted-foreground/40">null</span> : String(v)}
                            </TableCell>
                          );
                        })}
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>

              {/*
                Pagination block.
                Visibility: shown whenever there's at least one extra page —
                determined by exact total when known, otherwise by whether the
                last fetched page came back full (hasNextPage).
                Total-page label: omitted while exact total is unknown so we
                never display a number that could later jump when we learn
                the truth. The estimate stays in the header as "≈ N (estimate)".
              */}
              {((browseTotalPages !== null && browseTotalPages > 1) || hasNextPage || browsePage > 0) && (
                <div className="flex items-center gap-2 justify-center flex-wrap">
                  <Button variant="outline" size="sm" disabled={browsePage === 0 || browseLoading} onClick={() => loadBrowse(0, browsePageSize, browseOrderDesc)}>«</Button>
                  <Button variant="outline" size="sm" disabled={browsePage === 0 || browseLoading} onClick={() => loadBrowse(browsePage - 1, browsePageSize, browseOrderDesc)}>‹</Button>
                  <span className="text-xs text-muted-foreground tabular-nums">
                    {browseTotalPages !== null
                      ? text('pageLabelWithTotal', { page: browsePage + 1, total: browseTotalPages })
                      : text('pageLabel', { page: browsePage + 1 })}
                  </span>
                  <Button variant="outline" size="sm" disabled={!hasNextPage || browseLoading} onClick={() => loadBrowse(browsePage + 1, browsePageSize, browseOrderDesc)}>›</Button>
                  {browseTotalPages !== null && (
                    <Button variant="outline" size="sm" disabled={browsePage >= browseTotalPages - 1 || browseLoading} onClick={() => loadBrowse(browseTotalPages - 1, browsePageSize, browseOrderDesc)}>»</Button>
                  )}
                </div>
              )}

              {browseChartCol && (
                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <p className="text-xs font-semibold">
                      {text('timeSeries', { column: browseChartCol })}
                      <span className="ml-2 text-[10px] text-muted-foreground">{text('firstRowsHint')}</span>
                    </p>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-6 text-xs"
                      onClick={() => { setBrowseChartCol(null); setBrowseChartData(null); }}
                    >
                      ✕ {text('close')}
                    </Button>
                  </div>
                  {browseChartLoading || !browseChartData ? (
                    <Skeleton className="h-[220px] w-full" />
                  ) : browseChartData.length > 0 ? (
                    <BrowseAreaChart data={browseChartData} />
                  ) : (
                    <p className="text-xs text-muted-foreground">{text('noPlottableData')}</p>
                  )}
                </div>
              )}
            </>
          )}

          {!browseRows && !browseLoading && (
            <p className="text-sm text-muted-foreground">{text('clickLoadBrowse')}</p>
          )}
          {browseLoading && (
            <div className="space-y-2">
              <Skeleton className="h-[300px] w-full" />
            </div>
          )}
        </div>
      </Collapsible>

      {/* ── Section: Anomalies ── */}
      <Collapsible title={text('anomalies')} defaultOpen>
        <div className="p-4 space-y-4">
          {!anomalies && !loadingAnalyze && (
            <p className="text-sm text-muted-foreground">{text('clickAnalyzeAnomalies')}</p>
          )}
          {loadingAnalyze && (
            <Skeleton className="h-[200px] w-full" />
          )}
          {anomalies?.error && (
            <p className="text-sm text-destructive">{text('errorPrefix')}: {localizeError(anomalies.error)}</p>
          )}
          {anomalies && !anomalies.error && (
            <>
              {/* Summary cards */}
              <div className="grid grid-cols-1 xs:grid-cols-3 gap-3">
                <Card className={cn(
                  anomalies.critical > 0 && 'bg-destructive/10 border-destructive/30',
                  anomalies.critical === 0 && 'bg-emerald-500/10 border-emerald-500/30',
                )}>
                  <CardHeader className="pb-1"><CardTitle className="text-xs">{text('critical')}</CardTitle></CardHeader>
                  <CardContent className="text-2xl font-bold tabular-nums">{anomalies.critical.toLocaleString()}</CardContent>
                </Card>
                <Card className={cn(
                  anomalies.warning > 0 && 'bg-warning/10 border-warning/30',
                  anomalies.warning === 0 && 'bg-emerald-500/10 border-emerald-500/30',
                )}>
                  <CardHeader className="pb-1"><CardTitle className="text-xs">{text('warning')}</CardTitle></CardHeader>
                  <CardContent className="text-2xl font-bold tabular-nums">{anomalies.warning.toLocaleString()}</CardContent>
                </Card>
                <Card>
                  <CardHeader className="pb-1"><CardTitle className="text-xs">{text('total')}</CardTitle></CardHeader>
                  <CardContent className="text-2xl font-bold tabular-nums">{anomalies.total.toLocaleString()}</CardContent>
                </Card>
              </div>

              {/* Smart Suggestions (Block 6) */}
              {suggestions.length > 0 && (
                <div className="rounded-md border border-border bg-muted/20 p-3 space-y-2">
                  <div className="flex items-center gap-2">
                    <Wand2 className="w-4 h-4 text-primary" />
                    <p className="text-sm font-semibold">{text('smartSuggestions')}</p>
                    <span className="text-[11px] text-muted-foreground ml-2">
                      {text('sortedBySeverity')}
                    </span>
                  </div>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                    {suggestions.map(s => (
                      <div
                        key={s.type}
                        className={cn(
                          'flex items-center gap-2 rounded-md border px-3 py-2',
                          s.rank === 0 && 'border-destructive/30 bg-destructive/5',
                          s.rank === 1 && 'border-warning/30 bg-warning/5',
                          s.rank >= 2  && 'border-border',
                        )}
                      >
                        <Badge
                          variant={s.rank === 0 ? 'destructive' : 'outline'}
                          className="text-[10px]"
                        >
                          {s.rank === 0 ? anomalySeverityLabel('critical') : s.rank === 1 ? anomalySeverityLabel('warning') : anomalySeverityLabel('info')}
                        </Badge>
                        <div className="flex-1 min-w-0">
                          <p className="text-xs font-mono truncate">{anomalyTypeLabel(s.type)}</p>
                          <p className="text-[11px] text-muted-foreground truncate">
                            {s.count.toLocaleString()} · {s.recommendation}
                          </p>
                        </div>
                        {s.recommendedOp ? (
                          <Button
                            size="sm"
                            variant="outline"
                            className="h-7 text-xs"
                            disabled={cleanApplying}
                            onClick={() => applySuggestion(s.recommendedOp!)}
                          >
                            {text('apply')}
                          </Button>
                        ) : (
                          <Badge variant="outline" className="text-[10px] text-muted-foreground">
                            {text('review')}
                          </Badge>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Type chips */}
              {Object.keys(anomalies.by_type ?? {}).length > 0 && (
                <div className="flex flex-wrap gap-2">
                  {Object.entries(anomalies.by_type ?? {}).map(([type, count]) => (
                    <Badge key={type} variant="outline" className="font-mono text-xs">
                      {anomalyTypeLabel(type)}: <span className="ml-1 font-semibold">{count.toLocaleString()}</span>
                    </Badge>
                  ))}
                </div>
              )}

              {/* Tabbed view: Timeline | Table | DBSCAN | IForest | Distribution | History */}
              <Tabs value={anomalyTab} onValueChange={v => setAnomalyTab(v as typeof anomalyTab)}>
                <TabsList className="flex flex-wrap h-auto">
                  <TabsTrigger value="timeline">{text('timeline')}</TabsTrigger>
                  <TabsTrigger value="table">{text('table')}</TabsTrigger>
                  <TabsTrigger value="dbscan">DBSCAN</TabsTrigger>
                  <TabsTrigger value="iforest">IForest</TabsTrigger>
                  <TabsTrigger value="distribution">{text('distribution')}</TabsTrigger>
                  <TabsTrigger value="history">{text('history')}</TabsTrigger>
                </TabsList>

                {/* ── Timeline tab (Block 3) ── */}
                <TabsContent value="timeline" className="space-y-3">
                  {timelineData.length === 0 ? (
                    <p className="text-sm text-muted-foreground">
                      {text('noAnomaliesToPlot')}
                    </p>
                  ) : (
                    <>
                      <p className="text-xs text-muted-foreground">
                        {text('timelineHint')}
                      </p>
                      <AnomalyTimelineChart data={timelineData} types={timelineTypes} locale={locale} />
                    </>
                  )}
                </TabsContent>

                {/* ── Table tab (existing detailed table) ── */}
                <TabsContent value="table" className="space-y-3">
                  <div className="flex flex-wrap items-end gap-3">
                    <div className="flex flex-col gap-1.5">
                      <label className="text-xs text-muted-foreground">{text('severity')}</label>
                      <Select value={anomalyFilterSev} onValueChange={v => { setAnomalyFilterSev(v as 'all' | 'critical' | 'warning'); setAnomalyPage(0); }}>
                        <SelectTrigger className="w-[130px]"><SelectValue /></SelectTrigger>
                        <SelectContent>
                          <SelectItem value="all">{text('all')}</SelectItem>
                          <SelectItem value="critical">{text('critical')}</SelectItem>
                          <SelectItem value="warning">{text('warning')}</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="flex flex-col gap-1.5">
                      <label className="text-xs text-muted-foreground">{text('type')}</label>
                      <Select value={anomalyFilterType} onValueChange={v => { setAnomalyFilterType(v); setAnomalyPage(0); }}>
                        <SelectTrigger className="w-[200px]"><SelectValue /></SelectTrigger>
                        <SelectContent>
                          <SelectItem value="all">{text('allTypes')}</SelectItem>
                          {Object.keys(anomalies.by_type ?? {}).map(t => (
                            <SelectItem key={t} value={t}>{anomalyTypeLabel(t)}</SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                  </div>

                  {(() => {
                    const filtered = (anomalies.rows ?? anomalies.sample ?? []).filter(r =>
                      (anomalyFilterSev  === 'all' || r.severity     === anomalyFilterSev) &&
                      (anomalyFilterType === 'all' || r.anomaly_type === anomalyFilterType),
                    );
                    const totalPages = Math.max(1, Math.ceil(filtered.length / ANOMALY_PAGE_SIZE));
                    const page = Math.min(anomalyPage, totalPages - 1);
                    const slice = filtered.slice(page * ANOMALY_PAGE_SIZE, (page + 1) * ANOMALY_PAGE_SIZE);
                    if (filtered.length === 0) {
                      return (
                        <p className="text-sm text-muted-foreground">
                          {text('noAnomaliesForFilters')}
                        </p>
                      );
                    }
                    return (
                      <>
                        <div className="rounded-md border border-border overflow-x-auto">
                          <Table>
                            <TableHeader>
                              <TableRow>
                                <TableHead>{text('timestamp')}</TableHead>
                                <TableHead>{text('type')}</TableHead>
                                <TableHead>{text('severity')}</TableHead>
                                <TableHead>{text('column')}</TableHead>
                                <TableHead className="text-right">{text('value')}</TableHead>
                                <TableHead>{text('details')}</TableHead>
                              </TableRow>
                            </TableHeader>
                            <TableBody>
                              {slice.map((r, i) => (
                                <TableRow
                                  key={`${r.ts_ms}-${r.anomaly_type}-${r.column ?? ''}-${i}`}
                                  className={cn(
                                    r.severity === 'critical' && 'bg-destructive/5',
                                    r.severity === 'warning' && 'bg-warning/5',
                                  )}
                                >
                                  <TableCell className="text-xs font-mono whitespace-nowrap">{formatDateFromMs(r.ts_ms) ?? r.ts_ms}</TableCell>
                                  <TableCell className="text-xs font-mono">{anomalyTypeLabel(r.anomaly_type)}</TableCell>
                                  <TableCell>
                                    <Badge variant={r.severity === 'critical' ? 'destructive' : 'outline'} className="text-xs">
                                      {anomalySeverityLabel(r.severity)}
                                    </Badge>
                                  </TableCell>
                                  <TableCell className="text-xs font-mono">{r.column ?? '–'}</TableCell>
                                  <TableCell className="text-xs text-right tabular-nums">{fmtNum(r.value)}</TableCell>
                                  <TableCell className="text-xs text-muted-foreground">{anomalyDetailsLabel(r.anomaly_type, r.details, r.column)}</TableCell>
                                </TableRow>
                              ))}
                            </TableBody>
                          </Table>
                        </div>
                        {totalPages > 1 && (
                          <div className="flex items-center gap-2 justify-center flex-wrap">
                            <Button variant="outline" size="sm" disabled={page === 0} onClick={() => setAnomalyPage(0)}>«</Button>
                            <Button variant="outline" size="sm" disabled={page === 0} onClick={() => setAnomalyPage(page - 1)}>‹</Button>
                            <span className="text-xs text-muted-foreground tabular-nums">{text('pageLabelWithTotal', { page: page + 1, total: totalPages })}</span>
                            <Button variant="outline" size="sm" disabled={page >= totalPages - 1} onClick={() => setAnomalyPage(page + 1)}>›</Button>
                            <Button variant="outline" size="sm" disabled={page >= totalPages - 1} onClick={() => setAnomalyPage(totalPages - 1)}>»</Button>
                          </div>
                        )}
                      </>
                    );
                  })()}
                </TabsContent>

                {/* ── DBSCAN tab ── */}
                <TabsContent value="dbscan" className="space-y-3">
                  <p className="text-xs text-muted-foreground">
                    {text('dbscanHint')}
                  </p>
                  <div className="flex flex-wrap items-end gap-3">
                    <NumField label={text('epsLabel')} info={text('epsInfo')} value={dbscanEps} onChange={setDbscanEps} step={0.1} min={0.01} width="6rem" />
                    <NumField label={text('minSamplesLabel')} info={text('minSamplesInfo')} value={dbscanMinSamples} onChange={setDbscanMinSamples} step={1} min={1} width="6rem" />
                    <NumField label={text('maxSampleRowsLabel')} info={text('maxSampleRowsInfo')} value={dbscanMaxSampleRows} onChange={setDbscanMaxSampleRows} step={1000} min={1000} width="8rem" />
                    <Button
                      onClick={handleRunDbscan}
                      disabled={dbscanLoading || !session?.loaded}
                      className="gap-2"
                    >
                      {dbscanLoading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
                      {text('runDbscan')}
                    </Button>
                  </div>
                  {!session?.loaded && (
                    <p className="text-xs text-muted-foreground">
                      {text('sessionRequiredAnalyze')}
                    </p>
                  )}
                  {dbscanResult?.error && (
                    <p className="text-sm text-destructive">{text('errorPrefix')}: {localizeError(dbscanResult.error)}</p>
                  )}
                  {dbscanResult?.summary && (
                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                      <Stat label={text('sampleSize')} value={dbscanResult.summary.sample_size.toLocaleString()} />
                      <Stat label={text('clusters')}    value={String(dbscanResult.summary.n_clusters)} />
                      <Stat label={text('anomaliesCount')}   value={dbscanResult.summary.n_anomalies.toLocaleString()} accent="destructive" />
                      <div>
                        <p className="text-xs text-muted-foreground">{text('columnsUsed')}</p>
                        <p className="text-xs font-mono">{dbscanResult.summary.columns.join(', ')}</p>
                      </div>
                    </div>
                  )}
                </TabsContent>

                {/* ── IForest tab (Block 2) ── */}
                <TabsContent value="iforest" className="space-y-3">
                  <p className="text-xs text-muted-foreground">
                    {text('iforestHint')}
                  </p>
                  <div className="flex flex-wrap items-end gap-3">
                    <NumField label={text('contaminationLabel')} info={text('contaminationInfo')} value={iforestContamination} onChange={setIforestContamination} step={0.005} min={0.0001} max={0.5} width="7rem" />
                    <NumField label={text('nEstimatorsLabel')}  info={text('treesInfo')} value={iforestNTrees}        onChange={setIforestNTrees}        step={10}    min={20}     max={500} width="6rem" />
                    <NumField label={text('maxSampleRowsLabel')} info={text('maxSampleRowsInfo')} value={iforestMaxRows}     onChange={setIforestMaxRows}       step={1000}  min={1000}   width="8rem" />
                    <Button
                      onClick={handleRunIForest}
                      disabled={iforestLoading || !session?.loaded}
                      className="gap-2"
                    >
                      {iforestLoading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
                      {text('runIForest')}
                    </Button>
                  </div>
                  {!session?.loaded && (
                    <p className="text-xs text-muted-foreground">
                      {text('sessionRequired')}
                    </p>
                  )}
                  {iforestResult?.error && (
                    <p className="text-sm text-destructive">{text('errorPrefix')}: {localizeError(iforestResult.error)}</p>
                  )}
                  {iforestResult?.summary && (
                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                      <Stat label={text('sampleSize')}   value={iforestResult.summary.sample_size.toLocaleString()} />
                      <Stat label={text('anomaliesCount')}     value={iforestResult.summary.n_anomalies.toLocaleString()} accent="destructive" />
                      <Stat label={text('contamination')} value={iforestResult.summary.contamination.toFixed(4)} />
                      <Stat label={text('trees')}         value={String(iforestResult.summary.n_estimators)} />
                    </div>
                  )}
                </TabsContent>

                {/* ── Distribution tab (Block 4) ── */}
                <TabsContent value="distribution" className="space-y-3">
                  <p className="text-xs text-muted-foreground">
                    {text('distributionHint')}
                  </p>
                  <div className="flex items-end gap-3">
                    <Button
                      onClick={handleRunDistribution}
                      disabled={distLoading || !session?.loaded}
                      className="gap-2"
                    >
                      {distLoading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
                      {text('computeDistribution')}
                    </Button>
                  </div>
                  {!session?.loaded && (
                    <p className="text-xs text-muted-foreground">
                      {text('sessionRequired')}
                    </p>
                  )}
                  {distResult?.error && (
                    <p className="text-sm text-destructive">{text('errorPrefix')}: {localizeError(distResult.error)}</p>
                  )}
                  {distResult && !distResult.error && distResult.bins && (
                    <>
                      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                        <Stat label={text('nLabel')}        value={(distResult.n ?? 0).toLocaleString()} />
                        <Stat label={text('skewness')} value={distResult.skewness?.toFixed(3) ?? '–'} />
                        <Stat label={text('kurtosisExcess')} value={distResult.kurtosis?.toFixed(3) ?? '–'} />
                        <Stat label={text('jbPValue')} value={distResult.jb_p !== undefined ? distResult.jb_p.toExponential(2) : '–'} />
                      </div>
                      {distResult.verdict && (
                        <p className={cn(
                          'text-xs italic',
                          (distResult.kurtosis ?? 0) > 3 ? 'text-warning' : 'text-muted-foreground',
                        )}>
                          {distResult.verdict}
                        </p>
                      )}
                      <ReturnDistributionChart data={distResult.bins} locale={locale} />
                    </>
                  )}
                </TabsContent>

                {/* ── History tab (Block 7) ── */}
                <TabsContent value="history" className="space-y-3">
                  <div className="flex items-center justify-between">
                    <p className="text-xs text-muted-foreground">
                      {text('historyHint')}
                    </p>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-7 gap-2 text-xs"
                      onClick={handleLoadAuditLog}
                      disabled={auditLogLoading}
                    >
                      {auditLogLoading
                        ? <Loader2 className="w-3 h-3 animate-spin" />
                        : <RefreshCw className="w-3 h-3" />}
                      {text('refresh')}
                    </Button>
                  </div>
                  {auditLog?.error && (
                    <p className="text-sm text-destructive">{text('errorPrefix')}: {localizeError(auditLog.error)}</p>
                  )}
                  {auditLog && !auditLog.error && (
                    auditLog.entries.length === 0 ? (
                      <p className="text-sm text-muted-foreground">{text('noAuditEntries')}</p>
                    ) : (
                      <div className="rounded-md border border-border overflow-x-auto">
                        <Table>
                          <TableHeader>
                            <TableRow>
                              <TableHead className="w-12">#</TableHead>
                              <TableHead>{text('time')}</TableHead>
                              <TableHead>{text('operation')}</TableHead>
                              <TableHead className="text-right">{text('rows')}</TableHead>
                              <TableHead>{text('params')}</TableHead>
                              <TableHead className="text-right">{text('action')}</TableHead>
                            </TableRow>
                          </TableHeader>
                          <TableBody>
                            {auditLog.entries.map(e => (
                              <TableRow key={e.id}>
                                <TableCell className="text-xs tabular-nums">{e.id}</TableCell>
                                <TableCell className="text-xs font-mono whitespace-nowrap">
                                  {formatDateFromMs(e.applied_at_ms) ?? e.applied_at_ms}
                                </TableCell>
                                <TableCell className="text-xs font-mono">{getCleanOpLabel(locale, e.operation)}</TableCell>
                                <TableCell className="text-xs text-right tabular-nums">
                                  {e.rows_affected.toLocaleString()}
                                </TableCell>
                                <TableCell className="text-[11px] font-mono text-muted-foreground max-w-[420px] truncate" title={e.params}>
                                  {e.params}
                                </TableCell>
                                <TableCell className="text-right">
                                  <Button
                                    size="sm"
                                    variant="ghost"
                                    disabled
                                    className="h-7 text-[11px]"
                                    title={text('rollbackUnavailable')}
                                  >
                                    {text('rollback')}
                                  </Button>
                                </TableCell>
                              </TableRow>
                            ))}
                          </TableBody>
                        </Table>
                      </div>
                    )
                  )}
                </TabsContent>
              </Tabs>
            </>
          )}
        </div>
      </Collapsible>

      {/* ── Section: Clean ── */}

      <Collapsible title={text('clean')} defaultOpen>
        <div className="p-4 space-y-4">
          <p className="text-xs text-muted-foreground">{text('cleanHint')}</p>

          {/* Op checkboxes with inline parameters (Block 5) */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {/* drop_duplicates + strategy */}
            <CleanOpCard
              checked={cleanOps.drop_duplicates}
              onCheck={v => setCleanOps(prev => ({ ...prev, drop_duplicates: v }))}
              label={text('dropDuplicatesLabel')}
              count={cleanPreview?.counts.drop_duplicates}
            >
              {cleanOps.drop_duplicates && (
                <ParamRow label={text('strategy')}>
                  <Select value={dedupStrategy} onValueChange={v => setDedupStrategy(v as 'first' | 'last' | 'none')}>
                    <SelectTrigger className="h-8 w-32"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="first">{text('keepFirst')}</SelectItem>
                      <SelectItem value="last">{text('keepLast')}</SelectItem>
                      <SelectItem value="none">{text('keepNone')}</SelectItem>
                    </SelectContent>
                  </Select>
                </ParamRow>
              )}
            </CleanOpCard>

            <CleanOpCard
              checked={cleanOps.fix_ohlc}
              onCheck={v => setCleanOps(prev => ({ ...prev, fix_ohlc: v }))}
              label={text('fixOhlcLabel')}
              count={cleanPreview?.counts.fix_ohlc}
            />

            <CleanOpCard
              checked={cleanOps.fill_zero_streaks}
              onCheck={v => setCleanOps(prev => ({ ...prev, fill_zero_streaks: v }))}
              label={text('fillZeroStreaksLabel')}
              count={cleanPreview?.counts.fill_zero_streaks}
            >
              {cleanOps.fill_zero_streaks && (
                <ParamRow label={text('columnsParam')}>
                  <Select value={streakColumns} onValueChange={v => setStreakColumns(v as 'all' | 'volume' | 'open_interest' | 'funding_rate')}>
                    <SelectTrigger className="h-8 w-44"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all">{text('allOiFundingRate')}</SelectItem>
                      <SelectItem value="volume">volume</SelectItem>
                      <SelectItem value="open_interest">open_interest</SelectItem>
                      <SelectItem value="funding_rate">funding_rate</SelectItem>
                    </SelectContent>
                  </Select>
                </ParamRow>
              )}
            </CleanOpCard>

            <CleanOpCard
              checked={cleanOps.delete_by_timestamps}
              onCheck={v => setCleanOps(prev => ({ ...prev, delete_by_timestamps: v }))}
              label={text('deleteByTimestampLabel')}
              count={cleanPreview?.counts.delete_by_timestamps}
            />

            <CleanOpCard
              checked={cleanOps.fill_gaps}
              onCheck={v => setCleanOps(prev => ({ ...prev, fill_gaps: v }))}
              label={text('fillTimestampGapsLabel')}
              count={cleanPreview?.counts.fill_gaps}
            >
              {cleanOps.fill_gaps && (
                <ParamRow label={text('method')}>
                  <Select value={interpolationMethod} onValueChange={v => setInterpolationMethod(v as 'forward_fill' | 'linear' | 'drop_rows')}>
                    <SelectTrigger className="h-8 w-44"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="forward_fill">{text('forwardFill')}</SelectItem>
                      <SelectItem value="linear">{text('linearInterpolation')}</SelectItem>
                      <SelectItem value="drop_rows">{text('dropRows')}</SelectItem>
                    </SelectContent>
                  </Select>
                </ParamRow>
              )}
            </CleanOpCard>
          </div>

          <div className="flex flex-wrap gap-2">
            <Button onClick={handleCleanPreview} disabled={cleanLoading} variant="outline" className="gap-2">
              {cleanLoading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
              {text('preview')}
            </Button>
            <Button
              onClick={handleCleanApply}
              disabled={cleanApplying || !Object.values(cleanOps).some(Boolean)}
              variant="destructive"
              className="gap-2"
            >
              {cleanApplying ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : null}
              {text('apply')}
            </Button>
          </div>

          {cleanPreview && !cleanPreview.error && (
            <div className="rounded-md border border-border p-3">
              <p className="text-xs font-semibold mb-2">{text('previewTotals')}</p>
              <table className="text-xs w-full">
                <tbody>
                  {Object.entries(cleanPreview.counts).map(([op, n]) => (
                    <tr key={op}>
                      <td className="text-muted-foreground py-0.5 font-mono">{getCleanOpLabel(locale, op)}</td>
                      <td className="text-right tabular-nums py-0.5">{n.toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </Collapsible>
    </div>
    </TooltipProvider>
  );
}

// ── InfoTip ─────────────────────────────────────────────────────────────────

function InfoTip({ text }: { text: string }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="inline-flex items-center cursor-help text-muted-foreground/60 hover:text-muted-foreground">
          <Info className="w-3 h-3" />
        </span>
      </TooltipTrigger>
      <TooltipContent side="top" className="max-w-xs text-xs">
        <p>{text}</p>
      </TooltipContent>
    </Tooltip>
  );
}

// ── Tiny helper components (kept inline to avoid extra files) ────────────────

function ParamSection({
  title, enabled, onToggle, children, info,
}: {
  title: string;
  enabled: boolean;
  onToggle: (v: boolean) => void;
  children: React.ReactNode;
  info?: string;
}) {
  return (
    <div className={cn(
      'rounded-md border p-3 space-y-2',
      enabled ? 'border-border' : 'border-border opacity-60',
    )}>
      <label className="flex items-center gap-2 text-sm font-semibold cursor-pointer">
        <input
          type="checkbox"
          checked={enabled}
          onChange={e => onToggle(e.target.checked)}
        />
        {title}
        {info && <InfoTip text={info} />}
      </label>
      {enabled && <div className="pl-6 space-y-1.5">{children}</div>}
    </div>
  );
}

function ParamRow({ label, children, info }: { label: string; children: React.ReactNode; info?: string }) {
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="text-muted-foreground w-32 flex-shrink-0 flex items-center gap-0.5">
        {label}{info && <InfoTip text={info} />}
      </span>
      {children}
    </div>
  );
}

function NumInput({
  value, onChange, min, max, step,
}: {
  value: number;
  onChange: (v: number) => void;
  min?: number;
  max?: number;
  step?: number;
}) {
  return (
    <input
      type="number"
      value={value}
      min={min}
      max={max}
      step={step}
      onChange={e => {
        const v = parseFloat(e.target.value);
        onChange(Number.isFinite(v) ? v : 0);
      }}
      className="h-8 w-28 rounded-md border bg-background px-2 text-xs"
    />
  );
}

function NumField({
  label, value, onChange, min, max, step, width, info,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  min?: number;
  max?: number;
  step?: number;
  width?: string;
  info?: string;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label className="text-xs text-muted-foreground flex items-center gap-0.5">{label}{info && <InfoTip text={info} />}</label>
      <input
        type="number"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={e => {
          const v = parseFloat(e.target.value);
          onChange(Number.isFinite(v) ? v : 0);
        }}
        className="h-9 rounded-md border bg-background px-2 text-sm"
        style={{ width }}
      />
    </div>
  );
}

function Stat({
  label, value, accent,
}: {
  label: string;
  value: string;
  accent?: 'destructive' | 'warning';
}) {
  return (
    <div>
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className={cn(
        'text-lg font-bold tabular-nums',
        accent === 'destructive' && 'text-destructive',
        accent === 'warning'     && 'text-warning',
      )}>
        {value}
      </p>
    </div>
  );
}

function CleanOpCard({
  checked, onCheck, label, count, children,
}: {
  checked: boolean;
  onCheck: (v: boolean) => void;
  label: string;
  count?: number;
  children?: React.ReactNode;
}) {
  return (
    <div className={cn(
      'rounded-md border p-3 space-y-2',
      checked ? 'border-primary/40 bg-primary/5' : 'border-border',
    )}>
      <label className="flex items-center gap-2 text-sm cursor-pointer">
        <input
          type="checkbox"
          checked={checked}
          onChange={e => onCheck(e.target.checked)}
        />
        <span className="flex-1">{label}</span>
        {count !== undefined && (
          <Badge variant="outline" className="tabular-nums">
            {count.toLocaleString()}
          </Badge>
        )}
      </label>
      {children}
    </div>
  );
}
