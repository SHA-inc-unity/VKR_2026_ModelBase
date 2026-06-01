'use client';

import { useCallback, useDeferredValue, useEffect, useMemo, useState } from 'react';
import { Activity, AlertTriangle, Clock3, PauseCircle, PlayCircle, RefreshCw, Waves } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Collapsible } from '@/components/ui/collapsible';
import { Skeleton } from '@/components/ui/skeleton';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { cacheRead, cacheWrite } from '@/lib/cacheClient';
import { useLocale } from '@/lib/i18nContext';
import { kafkaCall } from '@/lib/kafkaClient';
import { Topics } from '@/lib/topics';
import { cn } from '@/lib/utils';

type WatcherState = 'starting' | 'running' | 'degraded' | 'error' | 'stopped';
type WatcherLogLevel = 'info' | 'success' | 'warning' | 'error';

interface WatcherStatus {
  desiredEnabled: boolean;
  effectiveEnabled: boolean;
  status: WatcherState;
  message?: string | null;
  startedAtMs?: number | null;
  lastHeartbeatAtMs?: number | null;
  lastFlushAtMs?: number | null;
  lastTickAtMs?: number | null;
  configuredPairs?: number;
  trackedSymbols: number;
  liveRows: number;
  perExchange?: { exchange: string; symbols: number }[];
  avgLagMs?: number | null;
  maxLagMs?: number | null;
  ticksInLastWindow: number;
  lastFlushRows: number;
  exchanges: string[];
  timeframes: string[];
  lastError?: string | null;
  lastErrorAtMs?: number | null;
}

interface WatcherStatusResponse {
  watcher: WatcherStatus;
}

interface WatcherRow {
  exchange: string;
  symbol: string;
  realtime_symbol?: string | null;
  last_price: number;
  last_price_ts: string;
  updated_at: string;
  lag_ms: number;
  candles_json?: Record<string, unknown>;
}

interface WatcherRowsResponse {
  items: WatcherRow[];
  total: number;
  limit: number;
  offset: number;
}

interface WatcherLogEntry {
  id: number;
  ts: string;
  level: WatcherLogLevel;
  evt: string;
  message: string;
  fields?: Record<string, unknown>;
}

interface WatcherLogsResponse {
  logs: WatcherLogEntry[];
}

interface WatcherLagStats {
  averageLagMs: number | null;
  maxLagMs: number | null;
}

interface WatcherVariantGroup extends WatcherLagStats {
  key: string;
  symbol: string;
  quote: string | null;
  rows: WatcherRow[];
}

interface WatcherAssetGroup extends WatcherLagStats {
  key: string;
  asset: string;
  exchanges: string[];
  totalRows: number;
  variants: WatcherVariantGroup[];
}

interface MarketWatcherPageCache {
  watcher: WatcherStatus | null;
  rows: WatcherRow[];
  totalRows: number;
  logs: WatcherLogEntry[];
}

const POLL_MS = 1_000;
const ROW_FETCH_LIMIT = 500;
const GROUP_PAGE_SIZE = 24;
const MARKET_WATCHER_CACHE_TTL = 15;
const MARKET_WATCHER_CACHE_PREFIX = 'modelline:market-watcher:v2';
const KNOWN_QUOTES = ['USDT', 'USDC', 'USD', 'BTC', 'ETH', 'EUR', 'TRY'] as const;

const STATUS_BADGE: Record<WatcherState, 'default' | 'secondary' | 'destructive' | 'outline' | 'success' | 'warning' | 'info'> = {
  starting: 'info',
  running: 'success',
  degraded: 'warning',
  error: 'destructive',
  stopped: 'secondary',
};

const LOG_BADGE: Record<WatcherLogLevel, 'default' | 'secondary' | 'destructive' | 'outline' | 'success' | 'warning' | 'info'> = {
  info: 'info',
  success: 'success',
  warning: 'warning',
  error: 'destructive',
};

function formatDateTime(value?: number | string | null): string {
  if (value == null) return '–';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return `${date.toLocaleDateString()} ${date.toLocaleTimeString()}`;
}

function formatPrice(value: number): string {
  if (!Number.isFinite(value)) return '–';
  if (Math.abs(value) >= 1000) return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
  if (Math.abs(value) >= 1) return value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 6 });
  return value.toLocaleString(undefined, { minimumFractionDigits: 4, maximumFractionDigits: 8 });
}

function formatLag(ms?: number | null): string {
  if (ms == null || !Number.isFinite(ms)) return '–';
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const totalSeconds = Math.round(ms / 1000);
  if (totalSeconds < 60) return `${totalSeconds}s`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes < 60) return `${minutes}m ${seconds}s`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
}

function lagClass(ms: number): string {
  if (ms >= 60_000) return 'text-destructive';
  if (ms >= 10_000) return 'text-warning';
  return 'text-foreground';
}

function lagBadgeVariant(ms: number): 'success' | 'warning' | 'destructive' {
  if (ms >= 60_000) return 'destructive';
  if (ms >= 10_000) return 'warning';
  return 'success';
}

function formatFields(fields?: Record<string, unknown>): string {
  if (!fields || Object.keys(fields).length === 0) return '';
  return JSON.stringify(fields, null, 2);
}

function timeframeSummary(candles?: Record<string, unknown>): string {
  if (!candles) return '–';
  const keys = Object.keys(candles);
  return keys.length > 0 ? keys.join(', ') : '–';
}

function buildMarketWatcherCacheKey(exchange: string, search: string): string {
  return `${MARKET_WATCHER_CACHE_PREFIX}:${exchange || 'all'}:${search || 'all'}`;
}

function splitSymbolVariant(symbol: string): { asset: string; quote: string | null } {
  const upper = symbol.toUpperCase();
  for (const quote of KNOWN_QUOTES) {
    if (upper.endsWith(quote) && upper.length > quote.length) {
      return {
        asset: upper.slice(0, upper.length - quote.length),
        quote,
      };
    }
  }

  return { asset: upper, quote: null };
}

function computeLagStats(rows: WatcherRow[]): WatcherLagStats {
  if (rows.length === 0) {
    return { averageLagMs: null, maxLagMs: null };
  }

  const totalLag = rows.reduce((sum, row) => sum + (row.lag_ms || 0), 0);
  const maxLagMs = rows.reduce((max, row) => Math.max(max, row.lag_ms || 0), 0);
  return {
    averageLagMs: Math.round(totalLag / rows.length),
    maxLagMs,
  };
}

function buildRowGroups(rows: WatcherRow[]): WatcherAssetGroup[] {
  const orderedRows = [...rows].sort((left, right) => {
    const assetCompare = splitSymbolVariant(left.symbol).asset.localeCompare(splitSymbolVariant(right.symbol).asset);
    if (assetCompare !== 0) return assetCompare;
    const symbolCompare = left.symbol.localeCompare(right.symbol);
    if (symbolCompare !== 0) return symbolCompare;
    return left.exchange.localeCompare(right.exchange);
  });

  const assets = new Map<string, Map<string, WatcherRow[]>>();
  for (const row of orderedRows) {
    const { asset } = splitSymbolVariant(row.symbol);
    const variants = assets.get(asset) ?? new Map<string, WatcherRow[]>();
    const variantRows = variants.get(row.symbol) ?? [];
    variantRows.push(row);
    variants.set(row.symbol, variantRows);
    assets.set(asset, variants);
  }

  return Array.from(assets.entries()).map(([asset, variants]) => {
    const variantGroups = Array.from(variants.entries()).map(([symbol, variantRows]) => {
      const quote = splitSymbolVariant(symbol).quote;
      return {
        key: `${asset}:${symbol}`,
        symbol,
        quote,
        rows: [...variantRows].sort((left, right) => left.exchange.localeCompare(right.exchange)),
        ...computeLagStats(variantRows),
      } satisfies WatcherVariantGroup;
    });

    const allRows = variantGroups.flatMap((variant) => variant.rows);
    return {
      key: asset,
      asset,
      exchanges: Array.from(new Set(allRows.map((row) => row.exchange))).sort((left, right) => left.localeCompare(right)),
      totalRows: allRows.length,
      variants: variantGroups,
      ...computeLagStats(allRows),
    } satisfies WatcherAssetGroup;
  });
}

export default function MarketWatcherPage() {
  const { t } = useLocale();
  const [watcher, setWatcher] = useState<WatcherStatus | null>(null);
  const [rows, setRows] = useState<WatcherRow[]>([]);
  const [logs, setLogs] = useState<WatcherLogEntry[]>([]);
  const [totalRows, setTotalRows] = useState(0);
  const [page, setPage] = useState(0);
  const [exchange, setExchange] = useState('');
  const [search, setSearch] = useState('');
  const deferredSearch = useDeferredValue(search);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [toggling, setToggling] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const cacheKey = useMemo(() => buildMarketWatcherCacheKey(exchange, deferredSearch), [deferredSearch, exchange]);

  const loadPage = useCallback(async (showSpinner: boolean) => {
    if (showSpinner) setLoading(true); else setRefreshing(true);
    setError(null);
    try {
      const [statusData, rowData, logData] = await Promise.all([
        kafkaCall<WatcherStatusResponse>(Topics.CMD_DATA_MARKET_WATCHER_STATUS, {}),
        kafkaCall<WatcherRowsResponse>(Topics.CMD_DATA_MARKET_WATCHER_ROWS, {
          exchange: exchange || undefined,
          search: deferredSearch || undefined,
          limit: ROW_FETCH_LIMIT,
          offset: 0,
        }),
        kafkaCall<WatcherLogsResponse>(Topics.CMD_DATA_MARKET_WATCHER_LOGS, { limit: 120 }),
      ]);

      setWatcher(statusData.watcher);
      setRows(rowData.items ?? []);
      setTotalRows(rowData.total ?? 0);
      setLogs(logData.logs ?? []);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load market watcher');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [deferredSearch, exchange]);

  const refresh = useCallback(async () => {
    await loadPage(false);
  }, [loadPage]);

  const toggleWatcher = useCallback(async () => {
    if (!watcher) return;
    setToggling(true);
    setError(null);
    try {
      await kafkaCall(Topics.CMD_DATA_MARKET_WATCHER_SET_ENABLED, { enabled: !watcher.desiredEnabled });
      await loadPage(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update watcher state');
    } finally {
      setToggling(false);
    }
  }, [loadPage, watcher]);

  useEffect(() => {
    void loadPage(true);
  }, [loadPage]);

  useEffect(() => {
    let cancelled = false;
    void cacheRead<MarketWatcherPageCache>(cacheKey).then((cached) => {
      if (cancelled || !cached) return;
      setWatcher(cached.watcher);
      setRows(cached.rows ?? []);
      setTotalRows(cached.totalRows ?? 0);
      setLogs(cached.logs ?? []);
      setLoading(false);
    });

    return () => {
      cancelled = true;
    };
  }, [cacheKey]);

  useEffect(() => {
    if (!watcher) return;
    void cacheWrite(cacheKey, {
      watcher,
      rows,
      totalRows,
      logs,
    } satisfies MarketWatcherPageCache, MARKET_WATCHER_CACHE_TTL);
  }, [cacheKey, logs, rows, totalRows, watcher]);

  useEffect(() => {
    const id = window.setInterval(() => {
      // Don't hammer the backend (STATUS + 500-row ROWS + LOGS every second)
      // while the tab is backgrounded — resume on the next focus/poll tick.
      if (typeof document !== 'undefined' && document.hidden) return;
      void loadPage(false);
    }, POLL_MS);
    return () => window.clearInterval(id);
  }, [loadPage]);

  useEffect(() => {
    setPage(0);
  }, [exchange, deferredSearch]);

  const groupedRows = useMemo(() => buildRowGroups(rows), [rows]);
  const pageCount = Math.max(1, Math.ceil(groupedRows.length / GROUP_PAGE_SIZE));
  const pagedGroups = useMemo(
    () => groupedRows.slice(page * GROUP_PAGE_SIZE, (page + 1) * GROUP_PAGE_SIZE),
    [groupedRows, page],
  );
  const avgLag = watcher?.avgLagMs ?? null;
  const maxLag = watcher?.maxLagMs ?? null;
  const exchanges = useMemo(() => watcher?.exchanges ?? [], [watcher]);

  useEffect(() => {
    if (page < pageCount) return;
    setPage(Math.max(0, pageCount - 1));
  }, [page, pageCount]);

  return (
    <div className="space-y-4 lg:space-y-5">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">{t('marketWatcher.title')}</h1>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span>{t('marketWatcher.subtitle')}</span>
            {watcher?.message && (
              <>
                <span>·</span>
                <span>{watcher.message}</span>
              </>
            )}
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={refresh} disabled={loading || refreshing || toggling}>
            <RefreshCw className={cn('h-4 w-4', (refreshing || loading) && 'animate-spin')} />
            {t('common.refresh')}
          </Button>
          <Button size="sm" variant={watcher?.desiredEnabled ? 'destructive' : 'default'} onClick={toggleWatcher} disabled={loading || toggling}>
            {watcher?.desiredEnabled ? <PauseCircle className="h-4 w-4" /> : <PlayCircle className="h-4 w-4" />}
            {watcher?.desiredEnabled ? t('marketWatcher.disable') : t('marketWatcher.enable')}
          </Button>
        </div>
      </header>

      {error && (
        <Card className="border-destructive/40">
          <CardContent className="flex items-start gap-2 px-5 py-4 text-sm text-destructive">
            <AlertTriangle className="mt-0.5 h-4 w-4" />
            <span>{error}</span>
          </CardContent>
        </Card>
      )}

      <section className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
        <Card className="border-l-4 border-l-primary">
          <CardHeader className="px-5 pb-2 pt-5">
            <CardTitle className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
              <Activity className="h-3.5 w-3.5" />
              {t('marketWatcher.status')}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 px-5 pb-5">
            {loading || !watcher ? <Skeleton className="h-10 w-full" /> : (
              <>
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant={STATUS_BADGE[watcher.status]} className="uppercase">{watcher.status}</Badge>
                  <Badge variant={watcher.desiredEnabled ? 'success' : 'secondary'}>{t('marketWatcher.desired')}: {watcher.desiredEnabled ? 'on' : 'off'}</Badge>
                  <Badge variant={watcher.effectiveEnabled ? 'success' : 'secondary'}>{t('marketWatcher.effective')}: {watcher.effectiveEnabled ? 'on' : 'off'}</Badge>
                </div>
                <div className="grid grid-cols-1 gap-2 text-sm text-muted-foreground">
                  <div>{t('marketWatcher.lastHeartbeat')}: <span className="text-foreground">{formatDateTime(watcher.lastHeartbeatAtMs)}</span></div>
                  <div>{t('marketWatcher.lastFlush')}: <span className="text-foreground">{formatDateTime(watcher.lastFlushAtMs)}</span></div>
                  <div>{t('marketWatcher.lastTick')}: <span className="text-foreground">{formatDateTime(watcher.lastTickAtMs)}</span></div>
                </div>
              </>
            )}
          </CardContent>
        </Card>

        <Card className="border-l-4 border-l-success">
          <CardHeader className="px-5 pb-2 pt-5">
            <CardTitle className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
              <Waves className="h-3.5 w-3.5" />
              {t('marketWatcher.operator')}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 px-5 pb-5 text-sm">
            {loading || !watcher ? <Skeleton className="h-16 w-full" /> : (
              <>
                <div>{t('marketWatcher.configuredPairs')}: <span className="font-semibold">{watcher.configuredPairs ?? '–'}</span></div>
                <div>{t('marketWatcher.trackedSymbols')}: <span className="font-semibold">{watcher.trackedSymbols}</span>{typeof watcher.configuredPairs === 'number' ? <span className="text-muted-foreground"> / {watcher.configuredPairs}</span> : null}</div>
                <div>{t('marketWatcher.liveRows')}: <span className="font-semibold">{watcher.liveRows}</span>{watcher.perExchange && watcher.perExchange.length > 0 ? <span className="text-muted-foreground"> ({watcher.perExchange.map(e => `${e.exchange} ${e.symbols}`).join(' · ')})</span> : null}</div>
                <div>{t('marketWatcher.ticksWindow')}: <span className="font-semibold">{watcher.ticksInLastWindow}</span></div>
                <div className="text-muted-foreground">{watcher.exchanges.join(', ') || '–'} / {watcher.timeframes.join(', ') || '–'}</div>
              </>
            )}
          </CardContent>
        </Card>

        <Card className="border-l-4 border-l-warning">
          <CardHeader className="px-5 pb-2 pt-5">
            <CardTitle className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
              <Clock3 className="h-3.5 w-3.5" />
              {t('marketWatcher.avgLag')}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 px-5 pb-5">
            {loading ? <Skeleton className="h-8 w-24" /> : (
              <>
                <div className="text-3xl font-bold">{formatLag(avgLag)}</div>
                <div className="text-sm text-muted-foreground">{t('marketWatcher.maxLag')}: <span className={cn('font-medium', maxLag != null && lagClass(maxLag))}>{formatLag(maxLag)}</span></div>
              </>
            )}
          </CardContent>
        </Card>

        <Card className="border-l-4 border-l-destructive">
          <CardHeader className="px-5 pb-2 pt-5">
            <CardTitle className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
              <AlertTriangle className="h-3.5 w-3.5" />
              {t('marketWatcher.lastError')}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 px-5 pb-5 text-sm">
            {loading || !watcher ? <Skeleton className="h-12 w-full" /> : (
              <>
                <div className="line-clamp-3 min-h-[3rem] text-foreground">{watcher.lastError || '–'}</div>
                <div className="text-muted-foreground">{formatDateTime(watcher.lastErrorAtMs)}</div>
              </>
            )}
          </CardContent>
        </Card>
      </section>

      <Card>
        <CardHeader className="flex flex-col gap-3 px-5 pb-3 pt-5 sm:flex-row sm:items-center sm:justify-between">
          <CardTitle className="text-base">{t('marketWatcher.realtime')}</CardTitle>
          <div className="flex w-full flex-col gap-2 sm:w-auto sm:flex-row">
            <select
              value={exchange}
              onChange={(event) => setExchange(event.target.value)}
              className="h-9 rounded-md border border-input bg-background px-3 text-sm"
            >
              <option value="">{t('marketWatcher.allExchanges')}</option>
              {exchanges.map(item => (
                <option key={item} value={item}>{item}</option>
              ))}
            </select>
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder={t('marketWatcher.search')}
              className="h-9 rounded-md border border-input bg-background px-3 text-sm"
            />
          </div>
        </CardHeader>
        <CardContent className="space-y-3 px-5 pb-5">
          {loading ? (
            <div className="space-y-3 rounded-md border border-border p-3">
              {Array.from({ length: 6 }).map((_, idx) => (
                <Skeleton key={idx} className="h-12 w-full" />
              ))}
            </div>
          ) : pagedGroups.length === 0 ? (
            <div className="rounded-md border border-border px-4 py-10 text-center text-muted-foreground">
              {t('marketWatcher.noRows')}
            </div>
          ) : (
            <div className="space-y-3">
              {pagedGroups.map((group, groupIndex) => (
                <Collapsible
                  key={group.key}
                  defaultOpen={groupIndex === 0}
                  title={(
                    <div className="flex w-full flex-wrap items-center gap-2 pr-3">
                      <Badge variant={group.maxLagMs != null ? lagBadgeVariant(group.maxLagMs) : 'secondary'}>{group.asset}</Badge>
                      <span className="text-xs text-muted-foreground">{group.variants.length} pairs</span>
                      <span className="text-xs text-muted-foreground">{group.totalRows} rows</span>
                      <span className="text-xs text-muted-foreground">{group.exchanges.join(', ') || '–'}</span>
                      <span className={cn('text-xs font-mono', group.maxLagMs != null && lagClass(group.maxLagMs))}>
                        {t('marketWatcher.lag')}: {formatLag(group.averageLagMs)} / {formatLag(group.maxLagMs)}
                      </span>
                    </div>
                  )}
                >
                  <div className="space-y-3 p-3">
                    {group.variants.map((variant, variantIndex) => (
                      <Collapsible
                        key={variant.key}
                        defaultOpen={groupIndex === 0 && variantIndex === 0}
                        className="border-dashed"
                        title={(
                          <div className="flex w-full flex-wrap items-center gap-2 pr-3">
                            <span className="font-mono text-xs font-semibold">{variant.symbol}</span>
                            <Badge variant={variant.maxLagMs != null ? lagBadgeVariant(variant.maxLagMs) : 'secondary'}>
                              {variant.quote ?? 'spot'}
                            </Badge>
                            <span className="text-xs text-muted-foreground">{variant.rows.length} exchanges</span>
                            <span className={cn('text-xs font-mono', variant.maxLagMs != null && lagClass(variant.maxLagMs))}>
                              {t('marketWatcher.lag')}: {formatLag(variant.averageLagMs)} / {formatLag(variant.maxLagMs)}
                            </span>
                          </div>
                        )}
                      >
                        <div className="overflow-x-auto p-3 pt-0">
                          <Table>
                            <TableHeader>
                              <TableRow>
                                <TableHead className="w-[110px]">{t('common.status')}</TableHead>
                                <TableHead>{t('common.symbol')}</TableHead>
                                <TableHead className="w-[140px]">{t('marketWatcher.realtimeSymbol')}</TableHead>
                                <TableHead className="w-[140px]">Price</TableHead>
                                <TableHead className="w-[140px]">{t('marketWatcher.lag')}</TableHead>
                                <TableHead className="w-[180px]">{t('marketWatcher.lastTick')}</TableHead>
                                <TableHead>{t('marketWatcher.frames')}</TableHead>
                              </TableRow>
                            </TableHeader>
                            <TableBody>
                              {variant.rows.map((row) => (
                                <TableRow key={`${row.exchange}:${row.symbol}`} className="align-top">
                                  <TableCell>
                                    <Badge variant={lagBadgeVariant(row.lag_ms)}>{row.exchange}</Badge>
                                  </TableCell>
                                  <TableCell className="font-mono text-xs">{row.symbol}</TableCell>
                                  <TableCell className="font-mono text-xs text-muted-foreground">{row.realtime_symbol || '–'}</TableCell>
                                  <TableCell className="font-mono text-sm font-medium">{formatPrice(row.last_price)}</TableCell>
                                  <TableCell className={cn('font-mono text-xs', lagClass(row.lag_ms))}>{formatLag(row.lag_ms)}</TableCell>
                                  <TableCell className="font-mono text-xs text-muted-foreground">{formatDateTime(row.last_price_ts)}</TableCell>
                                  <TableCell className="font-mono text-xs text-muted-foreground">{timeframeSummary(row.candles_json)}</TableCell>
                                </TableRow>
                              ))}
                            </TableBody>
                          </Table>
                        </div>
                      </Collapsible>
                    ))}
                  </div>
                </Collapsible>
              ))}
            </div>
          )}
          <div className="flex flex-col gap-2 text-sm text-muted-foreground sm:flex-row sm:items-center sm:justify-between">
            <div>{t('marketWatcher.page')}: {page + 1} / {pageCount} · {groupedRows.length} assets · {rows.length} / {totalRows} rows</div>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" onClick={() => setPage(prev => Math.max(0, prev - 1))} disabled={page === 0 || loading}>Prev</Button>
              <Button variant="outline" size="sm" onClick={() => setPage(prev => (prev + 1 < pageCount ? prev + 1 : prev))} disabled={page + 1 >= pageCount || loading}>Next</Button>
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="px-5 pb-3 pt-5">
          <CardTitle className="text-base">{t('marketWatcher.logs')}</CardTitle>
        </CardHeader>
        <CardContent className="px-5 pb-5">
          <div className="overflow-x-auto rounded-md border border-border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-[180px]">{t('common.time')}</TableHead>
                  <TableHead className="w-[110px]">{t('logs.level')}</TableHead>
                  <TableHead className="w-[220px]">{t('logs.event')}</TableHead>
                  <TableHead>{t('logs.details')}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {loading ? (
                  Array.from({ length: 5 }).map((_, idx) => (
                    <TableRow key={idx}>
                      <TableCell><Skeleton className="h-4 w-28" /></TableCell>
                      <TableCell><Skeleton className="h-5 w-16" /></TableCell>
                      <TableCell><Skeleton className="h-4 w-32" /></TableCell>
                      <TableCell><Skeleton className="h-4 w-full" /></TableCell>
                    </TableRow>
                  ))
                ) : logs.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={4} className="h-24 text-center text-muted-foreground">
                      {t('marketWatcher.noLogs')}
                    </TableCell>
                  </TableRow>
                ) : (
                  logs.map(item => (
                    <TableRow key={item.id} className="align-top">
                      <TableCell className="font-mono text-xs text-muted-foreground">{formatDateTime(item.ts)}</TableCell>
                      <TableCell><Badge variant={LOG_BADGE[item.level]} className="uppercase">{item.level}</Badge></TableCell>
                      <TableCell className="font-mono text-xs">{item.evt}</TableCell>
                      <TableCell>
                        <div className="max-w-[980px] space-y-1">
                          <div className="text-sm text-foreground">{item.message}</div>
                          {item.fields && (
                            <pre className="max-h-36 overflow-auto whitespace-pre-wrap break-words rounded bg-background/40 p-2 font-mono text-[11px] leading-relaxed text-muted-foreground">
                              {formatFields(item.fields)}
                            </pre>
                          )}
                        </div>
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}