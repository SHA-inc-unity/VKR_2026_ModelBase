'use client';
import dynamic from 'next/dynamic';
import { Fragment, useEffect, useRef, useState } from 'react';
import { cacheRead, cacheWrite } from '@/lib/cacheClient';
import { Loader2, RefreshCw, ShieldAlert } from 'lucide-react';
import { kafkaCall } from '@/lib/kafkaClient';
import { Topics } from '@/lib/topics';
import { useToast } from '@/components/Toast';
import { SYMBOLS, TIMEFRAMES, makeTableName, formatDateFromMs } from '@/lib/constants';
import type { TableCoverage } from '@/lib/types';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Separator } from '@/components/ui/separator';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Collapsible } from '@/components/ui/collapsible';
import { cn } from '@/lib/utils';

// Dynamic import — avoids Recharts SSR errors
const HistogramChart = dynamic(
  () => import('@/components/charts/HistogramChart').then(m => m.HistogramChart),
  { ssr: false, loading: () => <Skeleton className="h-[240px] w-full" /> },
);

const BrowseAreaChart = dynamic(
  () => import('@/components/charts/BrowseAreaChart').then(m => m.BrowseAreaChart),
  { ssr: false, loading: () => <Skeleton className="h-[220px] w-full" /> },
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
  total_rows: number;
  rows: Record<string, unknown>[];
  error?: string;
}

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

export default function AnomalyPage() {
  const { toast } = useToast();

  const saved = useRef(loadParams());
  const [symbol,    setSymbol]    = useState<string>(saved.current?.symbol    ?? 'BTCUSDT');
  const [timeframe, setTimeframe] = useState<string>(saved.current?.timeframe ?? '5m');

  useEffect(() => {
    try { localStorage.setItem(PARAMS_KEY, JSON.stringify({ symbol, timeframe })); }
    catch { /* ignore */ }
  }, [symbol, timeframe]);

  const [loadingAnalyze, setLoadingAnalyze] = useState(false);
  const [stats,    setStats]    = useState<ColumnStatsResponse | null>(null);
  const [coverage, setCoverage] = useState<TableCoverage | null>(null);

  // Restore from cache when symbol or timeframe changes
  const isFirstRender = useRef(true);
  useEffect(() => {
    if (isFirstRender.current) {
      isFirstRender.current = false;
      // On first render, restore from cache for the initial symbol/timeframe
    }
    async function tryRestoreCache() {
      const cached = await cacheRead<{ stats: ColumnStatsResponse; coverage: TableCoverage | null }>(
        anomalyCacheKey(symbol, timeframe),
      );
      if (cached) {
        setStats(cached.stats);
        setCoverage(cached.coverage);
      } else {
        setStats(null);
        setCoverage(null);
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
  const [browseLoading,   setBrowseLoading]   = useState(false);
  const [browseChartCol,  setBrowseChartCol]  = useState<string | null>(null);
  const [browseChartData, setBrowseChartData] = useState<{ ts: number; val: number }[] | null>(null);
  const [browseChartLoading, setBrowseChartLoading] = useState(false);
  const [browseColumns,   setBrowseColumns]   = useState<string[]>([]);

  const handleAnalyze = async () => {
    setLoadingAnalyze(true);
    setExpandedCol(null);
    setHistogram(null);
    setHistogramFor(null);
    // reset browse state when switching tables
    setBrowseRows(null);
    setBrowseTotalRows(null);
    setBrowsePage(0);
    setBrowseChartCol(null);
    setBrowseChartData(null);
    setBrowseColumns([]);
    try {
      const table = makeTableName(symbol, timeframe);
      const [statsRes, covRes] = await Promise.all([
        kafkaCall<ColumnStatsResponse>(Topics.CMD_DATA_DATASET_COLUMN_STATS, { table }),
        kafkaCall<TableCoverage>(Topics.CMD_DATA_DATASET_COVERAGE, { table }).catch(() => null),
      ]);
      if (statsRes.error) throw new Error(statsRes.error);
      setStats(statsRes);
      setCoverage(covRes);
      // Save to cache (fire and forget)
      void cacheWrite(
        anomalyCacheKey(symbol, timeframe),
        { stats: statsRes, coverage: covRes },
        ANOMALY_CACHE_TTL,
      );
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      toast(msg, 'error');
      setStats(null);
      setCoverage(null);
    } finally {
      setLoadingAnalyze(false);
    }
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
      setBrowseTotalRows(res.total_rows);
      setBrowsePage(res.page);
      if (res.rows.length > 0) {
        const allKeys = Object.keys(res.rows[0]);
        setBrowseColumns(allKeys);
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      toast(msg, 'error');
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
      // Fetch up to 500 rows ordered asc for time-series chart
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
      toast(msg, 'error');
      setBrowseChartCol(null);
    } finally {
      setBrowseChartLoading(false);
    }
  };

  const browseTotalPages = browseTotalRows !== null && browsePageSize > 0
    ? Math.ceil(browseTotalRows / browsePageSize)
    : null;

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
      toast(msg, 'error');
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

  return (
    <div className="flex flex-col gap-4 sm:gap-6 w-full">
      <header className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <ShieldAlert className="w-6 h-6 text-primary" />
          <h1 className="text-2xl font-bold tracking-tight">Anomaly</h1>
        </div>
      </header>

      {/* ── Top control bar ── */}
      <Card>
        <CardContent className="pt-5 pb-5">
          <div className="flex flex-wrap items-end gap-3 sm:gap-4">
            <div className="flex flex-col gap-1.5 w-full xs:w-auto min-w-0 flex-1 xs:flex-initial xs:min-w-[180px]">
              <label className="text-xs text-muted-foreground">Symbol</label>
              <Select value={symbol} onValueChange={setSymbol}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>{SYMBOLS.map(s => <SelectItem key={s} value={s}>{s}</SelectItem>)}</SelectContent>
              </Select>
            </div>
            <div className="flex flex-col gap-1.5 w-full xs:w-auto min-w-0 flex-1 xs:flex-initial xs:min-w-[140px]">
              <label className="text-xs text-muted-foreground">Timeframe</label>
              <Select value={timeframe} onValueChange={setTimeframe}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>{TIMEFRAMES.map(t => <SelectItem key={t} value={t}>{t}</SelectItem>)}</SelectContent>
              </Select>
            </div>
            <Button onClick={handleAnalyze} disabled={loadingAnalyze} className="gap-2 w-full xs:w-auto">
              {loadingAnalyze ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
              Analyze
            </Button>
            {stats && (
              <p className="text-xs text-muted-foreground ml-auto">
                Table: <span className="font-mono text-foreground">{stats.table}</span>
              </p>
            )}
          </div>
        </CardContent>
      </Card>

      {/* ── Section: Inspect (open by default) ── */}
      <Collapsible title="Inspect" defaultOpen>
        <div className="p-4 space-y-4">
          {!stats && !loadingAnalyze && (
            <p className="text-sm text-muted-foreground">
              Select a symbol and timeframe, then click <span className="font-semibold">Analyze</span> to load column statistics.
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
              {/* Summary bar */}
              <div className="grid grid-cols-1 xs:grid-cols-2 md:grid-cols-4 gap-3 sm:gap-4">
                <div>
                  <p className="text-xs text-muted-foreground">Total Rows</p>
                  <p className="text-lg font-bold tabular-nums">{stats.total_rows.toLocaleString()}</p>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground">Columns</p>
                  <p className="text-lg font-bold tabular-nums">{stats.columns.length}</p>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground">Avg Null %</p>
                  <p className={cn(
                    'text-lg font-bold tabular-nums',
                    avgNullPct !== null && avgNullPct > 20 && 'text-destructive',
                    avgNullPct !== null && avgNullPct > 5 && avgNullPct <= 20 && 'text-warning',
                  )}>
                    {avgNullPct !== null ? `${avgNullPct.toFixed(2)}%` : '–'}
                  </p>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground">Date Range</p>
                  <p className="text-sm font-semibold">
                    {dateFrom && dateTo ? `${dateFrom} → ${dateTo}` : '–'}
                  </p>
                </div>
              </div>

              <Separator />

              {/* df.info()-style table */}
              <div className="rounded-md border border-border overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Column</TableHead>
                      <TableHead>Dtype</TableHead>
                      <TableHead className="text-right">Non-Null</TableHead>
                      <TableHead className="text-right">Null</TableHead>
                      <TableHead className="text-right">Null %</TableHead>
                      <TableHead className="text-right">Min</TableHead>
                      <TableHead className="text-right">Max</TableHead>
                      <TableHead className="text-right">Mean</TableHead>
                      <TableHead className="text-right">Std</TableHead>
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
                                  <p className="text-xs font-semibold">Distribution — {col.name}</p>
                                  {histogram && histogramFor === col.name && (
                                    <p className="text-xs text-muted-foreground">
                                      {histogram.buckets.length} buckets, range [{fmtNum(histogram.min)}, {fmtNum(histogram.max)}]
                                    </p>
                                  )}
                                </div>
                                {loadingHist && (histogramFor !== col.name) ? (
                                  <Skeleton className="h-[240px] w-full" />
                                ) : histogram && histogramFor === col.name ? (
                                  histogram.buckets.length > 0 ? (
                                    <HistogramChart data={histogram.buckets} />
                                  ) : (
                                    <p className="text-xs text-muted-foreground">No non-null values to plot.</p>
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
      <Collapsible title="Browse">
        <div className="p-4 space-y-4">
          {/* Controls row */}
          <div className="flex flex-wrap items-end gap-3">
            <div className="flex flex-col gap-1.5">
              <label className="text-xs text-muted-foreground">Rows per page</label>
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
              <label className="text-xs text-muted-foreground">Order</label>
              <Select
                value={browseOrderDesc ? 'desc' : 'asc'}
                onValueChange={v => setBrowseOrderDesc(v === 'desc')}
              >
                <SelectTrigger className="w-[110px]"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="desc">Newest first</SelectItem>
                  <SelectItem value="asc">Oldest first</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <Button
              onClick={() => loadBrowse(0, browsePageSize, browseOrderDesc)}
              disabled={browseLoading}
              className="gap-2"
            >
              {browseLoading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
              Load
            </Button>
            {browseTotalRows !== null && (
              <p className="text-xs text-muted-foreground ml-auto">
                {browseTotalRows.toLocaleString()} rows total
              </p>
            )}
          </div>

          {/* Table */}
          {browseRows && browseColumns.length > 0 && (
            <>
              <div className="rounded-md border border-border overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      {browseColumns.map(col => {
                        const isActive = browseChartCol === col;
                        // Detect if this is a numeric-ish column (not timestamp_utc)
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

              {/* Pagination controls */}
              {browseTotalPages !== null && browseTotalPages > 1 && (
                <div className="flex items-center gap-2 justify-center flex-wrap">
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={browsePage === 0 || browseLoading}
                    onClick={() => loadBrowse(0, browsePageSize, browseOrderDesc)}
                  >
                    «
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={browsePage === 0 || browseLoading}
                    onClick={() => loadBrowse(browsePage - 1, browsePageSize, browseOrderDesc)}
                  >
                    ‹
                  </Button>
                  <span className="text-xs text-muted-foreground tabular-nums">
                    Page {browsePage + 1} / {browseTotalPages}
                  </span>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={browsePage >= browseTotalPages - 1 || browseLoading}
                    onClick={() => loadBrowse(browsePage + 1, browsePageSize, browseOrderDesc)}
                  >
                    ›
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={browsePage >= browseTotalPages - 1 || browseLoading}
                    onClick={() => loadBrowse(browseTotalPages - 1, browsePageSize, browseOrderDesc)}
                  >
                    »
                  </Button>
                </div>
              )}

              {/* Column chart */}
              {browseChartCol && (
                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <p className="text-xs font-semibold">
                      Time series — {browseChartCol}
                      <span className="ml-2 text-[10px] text-muted-foreground">(first 500 rows, asc)</span>
                    </p>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-6 text-xs"
                      onClick={() => { setBrowseChartCol(null); setBrowseChartData(null); }}
                    >
                      ✕ Close
                    </Button>
                  </div>
                  {browseChartLoading || !browseChartData ? (
                    <Skeleton className="h-[220px] w-full" />
                  ) : browseChartData.length > 0 ? (
                    <BrowseAreaChart data={browseChartData} />
                  ) : (
                    <p className="text-xs text-muted-foreground">No plottable data.</p>
                  )}
                </div>
              )}
            </>
          )}

          {!browseRows && !browseLoading && (
            <p className="text-sm text-muted-foreground">
              Click <span className="font-semibold">Load</span> to fetch rows for the selected table.
            </p>
          )}
          {browseLoading && (
            <div className="space-y-2">
              <Skeleton className="h-[300px] w-full" />
            </div>
          )}
        </div>
      </Collapsible>

      {/* ── Section: Anomalies (placeholder) ── */}
      <Collapsible title="Anomalies">
        <div className="p-4">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-semibold">Coming soon</CardTitle>
            </CardHeader>
            <CardContent className="text-xs text-muted-foreground space-y-2">
              <p>
                Детектор аномалий уровня датасета: выбросы по IQR / Z-score / DBSCAN,
                временны́е разрывы (gaps), дубликаты timestamp, нулевые серии в
                <code className="ml-1 text-foreground">open_interest</code> / <code className="text-foreground">funding_rate</code>.
              </p>
              <p>
                Топик: <code className="text-foreground">cmd.data.dataset.detect_anomalies</code>.
                Визуализация: scatter с подсветкой аномальных точек на временно́й
                оси + таблица найденных аномалий с экспортом.
              </p>
            </CardContent>
          </Card>
        </div>
      </Collapsible>

      {/* ── Section: Clean (placeholder) ── */}
      <Collapsible title="Clean">
        <div className="p-4">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-semibold">Coming soon</CardTitle>
            </CardHeader>
            <CardContent className="text-xs text-muted-foreground space-y-2">
              <p>
                Устранение найденных аномалий: удаление строк-выбросов, линейная /
                forward-fill интерполяция пропусков, удаление дубликатов, обрезка
                временно́го диапазона.
              </p>
              <p>
                Топик: <code className="text-foreground">cmd.data.dataset.clean</code>.
                Все операции — <span className="font-semibold">preview-режим</span>
                (показать что изменится) + подтверждение перед записью в БД.
                Обязательный аудит-лог (кто / когда / что удалено).
              </p>
            </CardContent>
          </Card>
        </div>
      </Collapsible>

      {/* ── Section: Process (placeholder) ── */}
      <Collapsible title="Process">
        <div className="p-4">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-semibold">Coming soon</CardTitle>
            </CardHeader>
            <CardContent className="text-xs text-muted-foreground space-y-2">
              <p>
                Добавление производных признаков и нормализация: rolling stats
                (mean, std окна), z-score нормализация, min-max scaling, lag-фичи.
              </p>
              <p>
                Топик: <code className="text-foreground">cmd.data.dataset.process</code>.
                Dry-run обязателен. Результат может сохраняться в отдельную
                «производную» таблицу, не трогая исходник.
              </p>
            </CardContent>
          </Card>
        </div>
      </Collapsible>
    </div>
  );
}
