'use client';
import dynamic from 'next/dynamic';
import { useEffect, useRef, useState, type ChangeEvent } from 'react';
import { CheckCircle2, Database, DownloadCloud, Loader2, RefreshCw, Trash2, UploadCloud, XCircle } from 'lucide-react';
import { kafkaCall, newCorrelationId } from '@/lib/kafkaClient';
import { Topics } from '@/lib/topics';
import { useToast } from '@/components/Toast';
import { useEvents } from '@/hooks/useEvents';
import {
  SYMBOLS,
  TIMEFRAMES,
  TIMEFRAMES_ALL,
  TF_STEP_MS,
  makeTableName,
  getCoveragePct,
  formatDateFromMs,
} from '@/lib/constants';
import type { TableCoverage, IngestStage } from '@/lib/types';
import { useHistory } from '@/hooks/useHistory';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Progress } from '@/components/ui/progress';
import { Separator } from '@/components/ui/separator';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Input } from '@/components/ui/input';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { cn } from '@/lib/utils';
import type { BarDatum } from '@/components/charts/CoverageBar';

// Dynamic import — avoids Recharts SSR errors
const CoverageBar = dynamic(
  () => import('@/components/charts/CoverageBar').then(m => m.CoverageBar),
  { ssr: false, loading: () => <Skeleton className="h-[100px] w-full" /> },
);

const PARAMS_KEY = 'modelline:params:dataset';

function todayStr() { return new Date().toISOString().slice(0, 10); }
function daysAgoStr(n: number) {
  const d = new Date(); d.setDate(d.getDate() - n); return d.toISOString().slice(0, 10);
}
function loadParams() {
  if (typeof window === 'undefined') return null;
  try { const r = localStorage.getItem(PARAMS_KEY); return r ? JSON.parse(r) : null; }
  catch { return null; }
}

interface DataTableInfo {
  table_name: string;
  rows: number;
  coverage_pct: number;
  date_from?: string;
  date_to?: string;
}

interface CoverageResult {
  table_name: string;
  rows: number;
  expected: number;
  coverage_pct: number;
  gaps: number;
}

interface AllCoverageItem {
  tf: string;
  rows: number;
  coverage_pct: number;
  date_from?: string;
  date_to?: string;
}

type TfStatus = 'pending' | 'running' | 'done' | 'error';

interface CsvProgress {
  batch: number;
  batches: number;
  imported: number;
}

const INITIAL_STAGES: IngestStage[] = [
  { id: 'prepare',       label: 'Подготовка таблицы',    status: 'pending', progress: 0 },
  { id: 'fetch_klines',  label: 'Загрузка свечей',       status: 'pending', progress: 0 },
  { id: 'fetch_funding', label: 'Загрузка funding rate', status: 'pending', progress: 0 },
  { id: 'fetch_oi',      label: 'Загрузка open interest',status: 'pending', progress: 0 },
  { id: 'compute_rsi',   label: 'Вычисление RSI',        status: 'pending', progress: 0 },
  { id: 'upsert',        label: 'Запись в базу',         status: 'pending', progress: 0 },
];

function IngestProgress({ stages }: { stages: IngestStage[] }) {
  return (
    <div className="flex flex-col gap-2 pt-2">
      {stages.map(s => (
        <div key={s.id} className="flex flex-col gap-1">
          <div className="flex items-center gap-3">
            <div className="flex-shrink-0 w-3.5 h-3.5 flex items-center justify-center">
              {s.status === 'pending' && (
                <div className="w-3 h-3 rounded-full border-2 border-muted-foreground/30" />
              )}
              {s.status === 'running' && (
                <Loader2 className="w-3.5 h-3.5 animate-spin text-primary" />
              )}
              {s.status === 'done' && (
                <CheckCircle2 className="w-3.5 h-3.5 text-success" />
              )}
              {s.status === 'error' && (
                <XCircle className="w-3.5 h-3.5 text-destructive" />
              )}
            </div>
            <div className="flex-1 min-w-0">
              <span className={cn(
                'text-xs',
                s.status === 'pending' && 'text-muted-foreground',
                s.status === 'error' && 'text-destructive',
              )}>
                {s.label}
              </span>
              {s.detail && s.status !== 'pending' && (
                <div className="text-[10px] text-muted-foreground truncate">{s.detail}</div>
              )}
            </div>
            {s.status === 'running' && (
              <span className="text-[10px] text-muted-foreground tabular-nums flex-shrink-0">
                {s.progress}%
              </span>
            )}
          </div>
          {s.status === 'running' && (
            <Progress value={s.progress} className="h-0.5 w-full ml-6" />
          )}
        </div>
      ))}
    </div>
  );
}

function AllIngestProgress({ statuses }: { statuses: Record<string, TfStatus> }) {
  const tfs   = Object.keys(statuses);
  const total = tfs.length;
  const done  = tfs.filter(tf => statuses[tf] === 'done' || statuses[tf] === 'error').length;
  const pct   = total > 0 ? (done / total) * 100 : 0;

  return (
    <div className="pt-2 space-y-2">
      <div className="space-y-1">
        <Progress value={pct} className="h-1.5 w-full" />
        <p className="text-[10px] text-muted-foreground tabular-nums text-right">
          {done} / {total} таймфреймов
        </p>
      </div>
      <div className="flex flex-col gap-1">
        {tfs.map(tf => {
          const st = statuses[tf];
          return (
            <div key={tf} className="flex items-center gap-3">
              <div className="flex-shrink-0 w-3.5 h-3.5 flex items-center justify-center">
                {st === 'pending' && (
                  <div className="w-3 h-3 rounded-full border-2 border-muted-foreground/30" />
                )}
                {st === 'running' && (
                  <Loader2 className="w-3.5 h-3.5 animate-spin text-primary" />
                )}
                {st === 'done' && (
                  <CheckCircle2 className="w-3.5 h-3.5 text-success" />
                )}
                {st === 'error' && (
                  <XCircle className="w-3.5 h-3.5 text-destructive" />
                )}
              </div>
              <span className={cn(
                'text-xs font-mono',
                st === 'pending' && 'text-muted-foreground',
                st === 'error' && 'text-destructive',
              )}>
                {tf}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default function DatasetPage() {
  const { toast } = useToast();
  const { history, addEntry } = useHistory();

  const saved = useRef(loadParams());
  const [symbol,    setSymbol]    = useState<string>(saved.current?.symbol    ?? 'BTCUSDT');
  const [timeframe, setTimeframe] = useState<string>(saved.current?.timeframe ?? '5m');
  const [dateFrom,  setDateFrom]  = useState<string>(saved.current?.dateFrom  ?? daysAgoStr(90));
  const [dateTo,    setDateTo]    = useState<string>(saved.current?.dateTo    ?? todayStr());

  useEffect(() => {
    try { localStorage.setItem(PARAMS_KEY, JSON.stringify({ symbol, timeframe, dateFrom, dateTo })); }
    catch { /* ignore */ }
  }, [symbol, timeframe, dateFrom, dateTo]);

  const [tables,        setTables]        = useState<DataTableInfo[] | null>(null);
  const [coverage,      setCoverage]      = useState<CoverageResult | null>(null);
  const [loadingList,   setLoadingList]   = useState(false);
  const [loadingIngest, setLoadingIngest] = useState(false);
  const [loadingCov,    setLoadingCov]    = useState(false);
  const [loadingDelete, setLoadingDelete] = useState(false);
  const [allCoverages, setAllCoverages] = useState<AllCoverageItem[] | null>(null);
  const [allIngestStatuses, setAllIngestStatuses] = useState<Record<string, TfStatus> | null>(null);
  const [loadingCsv, setLoadingCsv] = useState(false);
  const [csvProgress, setCsvProgress] = useState<CsvProgress | null>(null);
  const csvInputRef = useRef<HTMLInputElement>(null);

  // Ingest progress (staged, driven by EVT_DATA_INGEST_PROGRESS events).
  const [ingestStages, setIngestStages] = useState<IngestStage[] | null>(null);
  const ingestCidRef = useRef<string | null>(null);

  useEvents({
    EVT_DATA_INGEST_PROGRESS: (ev) => {
      if (!ingestCidRef.current || ev.correlation_id !== ingestCidRef.current) return;
      setIngestStages(prev => (prev ?? INITIAL_STAGES).map(s => {
        if (s.id !== ev.stage) return s;
        const status: IngestStage['status'] =
          ev.status === 'done' ? 'done' : ev.status === 'error' ? 'error' : 'running';
        return {
          ...s,
          status,
          progress: ev.status === 'done' ? 100 : ev.progress,
          detail: ev.detail ?? s.detail,
        };
      }));
    },
  });

  const handleListTables = async () => {
    setLoadingList(true);
    const t0 = Date.now();
    try {
      // Backend may return either a legacy string[] or rich objects — accept both
      // and fall back to the Dashboard pattern: names first, then per-table coverage
      // in parallel so the client always derives coverage_pct/dates via the same
      // shared helpers.
      const res = await kafkaCall<{ tables: Array<string | { table_name: string }> }>(
        Topics.CMD_DATA_DATASET_LIST_TABLES,
        {},
      );
      const names: string[] = (res.tables ?? []).map(x =>
        typeof x === 'string' ? x : x.table_name,
      );

      const infos: DataTableInfo[] = await Promise.all(
        names.map(async name => {
          try {
            const cv = await kafkaCall<TableCoverage>(
              Topics.CMD_DATA_DATASET_COVERAGE,
              { table: name },
            );
            return {
              table_name:   name,
              rows:         cv?.rows ?? 0,
              coverage_pct: getCoveragePct(name, cv) ?? 0,
              date_from:    formatDateFromMs(cv?.min_ts_ms),
              date_to:      formatDateFromMs(cv?.max_ts_ms),
            };
          } catch {
            return { table_name: name, rows: 0, coverage_pct: 0 };
          }
        }),
      );
      setTables(infos);
      addEntry({ action: 'Check', params: { symbol, timeframe }, result: `${infos.length} tables`, durationMs: Date.now() - t0 });
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      toast(msg, 'error');
      addEntry({ action: 'Check', params: { symbol, timeframe }, result: `Error: ${msg}`, durationMs: Date.now() - t0 });
    } finally {
      setLoadingList(false);
    }
  };

  const handleCheckCoverage = async () => {
    setLoadingCov(true);
    const t0 = Date.now();
    try {
      if (timeframe === 'ALL') {
        setCoverage(null);
        const results = await Promise.all(
          TIMEFRAMES.map(async tf => {
            try {
              const table = makeTableName(symbol, tf);
              const cv = await kafkaCall<TableCoverage>(
                Topics.CMD_DATA_DATASET_COVERAGE,
                { table },
              );
              return {
                tf,
                rows:         cv?.rows ?? 0,
                coverage_pct: getCoveragePct(table, cv) ?? 0,
                date_from:    formatDateFromMs(cv?.min_ts_ms),
                date_to:      formatDateFromMs(cv?.max_ts_ms),
              } satisfies AllCoverageItem;
            } catch {
              return { tf, rows: 0, coverage_pct: 0 } satisfies AllCoverageItem;
            }
          }),
        );
        setAllCoverages(results);
        addEntry({ action: 'Check', params: { symbol, timeframe: 'ALL', dateFrom, dateTo }, result: `${results.length} timeframes`, durationMs: Date.now() - t0 });
      } else {
        setAllCoverages(null);
        const table   = makeTableName(symbol, timeframe);
        const startMs = new Date(dateFrom).getTime();
        const endMs   = new Date(dateTo + 'T23:59:59').getTime();

        const cv = await kafkaCall<TableCoverage>(
          Topics.CMD_DATA_DATASET_COVERAGE,
          { table },
        );

        const stepMs = TF_STEP_MS[timeframe];
        const expected = stepMs && endMs > startMs
          ? Math.max(0, Math.floor((endMs - startMs) / stepMs) + 1)
          : 0;
        const rows = cv?.rows ?? 0;
        const coveragePct = getCoveragePct(table, cv) ?? 0;
        const gaps = Math.max(0, expected - rows);

        const result: CoverageResult = {
          table_name:   table,
          rows,
          expected,
          coverage_pct: coveragePct,
          gaps,
        };
        setCoverage(result);
        addEntry({ action: 'Check', params: { symbol, timeframe, dateFrom, dateTo }, result: `${coveragePct.toFixed(1)}% coverage`, durationMs: Date.now() - t0 });
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      toast(msg, 'error');
      addEntry({ action: 'Check', params: { symbol, timeframe, dateFrom, dateTo }, result: `Error: ${msg}`, durationMs: Date.now() - t0 });
    } finally {
      setLoadingCov(false);
    }
  };

  const handleIngest = async () => {
    setLoadingIngest(true);
    const t0 = Date.now();
    try {
      if (timeframe === 'ALL') {
        const tfs = [...TIMEFRAMES] as string[];

        // Initialize per-TF status dictionary.
        const initialStatuses: Record<string, TfStatus> = {};
        for (const tf of tfs) initialStatuses[tf] = 'pending';
        setAllIngestStatuses(initialStatuses);

        // Seed allCoverages skeleton so the right-hand Coverage table renders
        // immediately; each successful ingest replaces one row in place.
        setCoverage(null);
        setAllCoverages(tfs.map(tf => ({
          tf,
          rows: 0,
          coverage_pct: 0,
          date_from: undefined,
          date_to: undefined,
        })));

        ingestCidRef.current = null;
        setIngestStages(null);
        let totalRows = 0;
        let successes = 0;
        const startMs = new Date(dateFrom).getTime();
        const endMs   = new Date(dateTo + 'T23:59:59').getTime();
        for (const tf of tfs) {
          setAllIngestStatuses(prev => ({ ...(prev ?? {}), [tf]: 'running' }));
          try {
            const res = await kafkaCall<{ rows_ingested: number; message?: string }>(
              Topics.CMD_DATA_DATASET_INGEST,
              { symbol, timeframe: tf, start_ms: startMs, end_ms: endMs },
              { timeoutMs: 60_000 },
            );
            totalRows += res.rows_ingested ?? 0;
            successes++;
            setAllIngestStatuses(prev => ({ ...(prev ?? {}), [tf]: 'done' }));

            // Refresh just this row of the Coverage table.
            try {
              const table = makeTableName(symbol, tf);
              const cv = await kafkaCall<TableCoverage>(
                Topics.CMD_DATA_DATASET_COVERAGE,
                { table },
              );
              const fresh = {
                rows:         cv?.rows ?? 0,
                coverage_pct: getCoveragePct(table, cv) ?? 0,
                date_from:    formatDateFromMs(cv?.min_ts_ms),
                date_to:      formatDateFromMs(cv?.max_ts_ms),
              };
              setAllCoverages(prev =>
                prev?.map(r => (r.tf === tf ? { ...r, ...fresh } : r)) ?? null,
              );
            } catch {
              // Non-fatal — leave skeleton row as-is.
            }
          } catch (e) {
            const msg = e instanceof Error ? e.message : String(e);
            toast(`${tf}: ${msg}`, 'info');
            setAllIngestStatuses(prev => ({ ...(prev ?? {}), [tf]: 'error' }));
          }
        }
        const msg = `Ingested ${totalRows.toLocaleString()} rows across ${successes} timeframes`;
        toast(msg, 'success');
        addEntry({ action: 'Download', params: { symbol, timeframe: 'ALL', dateFrom, dateTo }, result: msg, durationMs: Date.now() - t0 });
        handleListTables();
      } else {
        const cid = newCorrelationId();
        ingestCidRef.current = cid;
        setIngestStages(INITIAL_STAGES);
        const res = await kafkaCall<{ rows_ingested: number; message?: string }>(
          Topics.CMD_DATA_DATASET_INGEST,
          { symbol, timeframe, start_ms: new Date(dateFrom).getTime(), end_ms: new Date(dateTo + 'T23:59:59').getTime() },
          { timeoutMs: 60_000, correlationId: cid },
        );
        const msg = res.message ?? `Ingested ${res.rows_ingested ?? 0} rows`;
        toast(msg, 'success');
        addEntry({ action: 'Download', params: { symbol, timeframe, dateFrom, dateTo }, result: msg, durationMs: Date.now() - t0 });
        handleListTables();
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      toast(msg, 'error');
      addEntry({ action: 'Download', params: { symbol, timeframe, dateFrom, dateTo }, result: `Error: ${msg}`, durationMs: Date.now() - t0 });
    } finally {
      setLoadingIngest(false);
    }
  };

  const handleDeleteRows = async () => {
    if (timeframe === 'ALL') {
      const confirmed = typeof window !== 'undefined' && window.confirm(
        `Удалить все строки по ВСЕМ таймфреймам для ${symbol}?\nЭто удалит данные из ${TIMEFRAMES.length} таблиц и не может быть отменено.`,
      );
      if (!confirmed) return;

      setLoadingDelete(true);
      const t0 = Date.now();
      try {
        let totalDeleted = 0;
        let successes = 0;
        for (const tf of TIMEFRAMES) {
          const table = makeTableName(symbol, tf);
          try {
            const res = await kafkaCall<{ rows_deleted?: number; error?: string }>(
              Topics.CMD_DATA_DATASET_DELETE_ROWS,
              { table },
            );
            if (res.error) throw new Error(res.error);
            totalDeleted += res.rows_deleted ?? 0;
            successes++;
          } catch (e) {
            const msg = e instanceof Error ? e.message : String(e);
            toast(`${tf}: ${msg}`, 'info');
          }
        }
        const msg = `Deleted ${totalDeleted.toLocaleString()} rows across ${successes} timeframes`;
        toast(msg, 'success');
        addEntry({ action: 'Check', params: { symbol, timeframe: 'ALL' }, result: msg, durationMs: Date.now() - t0 });
        handleListTables();
      } finally {
        setLoadingDelete(false);
      }
    } else {
      const table = makeTableName(symbol, timeframe);
      const confirmed = typeof window !== 'undefined' && window.confirm(
        `Удалить все строки из таблицы ${table}? Это действие нельзя отменить.`,
      );
      if (!confirmed) return;

      setLoadingDelete(true);
      const t0 = Date.now();
      try {
        const res = await kafkaCall<{ rows_deleted?: number; error?: string }>(
          Topics.CMD_DATA_DATASET_DELETE_ROWS,
          { table },
        );
        if (res.error) throw new Error(res.error);
        const count = res.rows_deleted ?? 0;
        const msg = `Deleted ${count.toLocaleString()} rows from ${table}`;
        toast(msg, 'success');
        addEntry({ action: 'Check', params: { symbol, timeframe }, result: msg, durationMs: Date.now() - t0 });
        handleListTables();
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        toast(msg, 'error');
        addEntry({ action: 'Check', params: { symbol, timeframe }, result: `Error: ${msg}`, durationMs: Date.now() - t0 });
      } finally {
        setLoadingDelete(false);
      }
    }
  };

  const handleUploadCsvClick = () => {
    if (timeframe === 'ALL') {
      toast('Выберите конкретный таймфрейм перед загрузкой CSV', 'info');
      return;
    }
    csvInputRef.current?.click();
  };

  const handleCsvFileChange = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    // Reset so the user can re-select the same file if needed.
    e.target.value = '';
    if (!file) return;

    const table = makeTableName(symbol, timeframe);
    const form  = new FormData();
    form.append('file',  file);
    form.append('table', table);

    setLoadingCsv(true);
    setCsvProgress({ batch: 0, batches: 0, imported: 0 });
    const t0 = Date.now();
    try {
      const base = process.env.NEXT_PUBLIC_BASE_PATH ?? '';
      const res = await fetch(`${base}/api/upload/csv`, { method: 'POST', body: form });
      if (!res.ok || !res.body) {
        const j = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
        throw new Error(j.error ?? `HTTP ${res.status}`);
      }

      // Server streams NDJSON progress events — one JSON object per line.
      const reader  = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      let finalImported = 0;
      let sawError: string | null = null;

      // eslint-disable-next-line no-constant-condition
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split('\n');
        buf = lines.pop() ?? '';
        for (const line of lines) {
          if (!line.trim()) continue;
          const msg = JSON.parse(line) as
            | { type: 'start'; total: number; batch_size: number }
            | { type: 'batch'; batch: number; batches: number; imported: number }
            | { type: 'done';  imported: number; batches: number }
            | { type: 'error'; error: string };
          if (msg.type === 'start') {
            setCsvProgress({
              batch: 0,
              batches: Math.max(1, Math.ceil(msg.total / Math.max(1, msg.batch_size))),
              imported: 0,
            });
          } else if (msg.type === 'batch') {
            setCsvProgress({ batch: msg.batch, batches: msg.batches, imported: msg.imported });
          } else if (msg.type === 'done') {
            finalImported = msg.imported;
          } else if (msg.type === 'error') {
            sawError = msg.error;
          }
        }
      }
      if (sawError) throw new Error(sawError);

      const msg = `Imported ${finalImported.toLocaleString()} rows into ${table}`;
      toast(msg, 'success');
      addEntry({ action: 'Download', params: { symbol, timeframe }, result: msg, durationMs: Date.now() - t0 });
      handleListTables();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      toast(msg, 'error');
      addEntry({ action: 'Download', params: { symbol, timeframe }, result: `Error: ${msg}`, durationMs: Date.now() - t0 });
    } finally {
      setLoadingCsv(false);
      setCsvProgress(null);
    }
  };

  const isBusy = loadingList || loadingIngest || loadingCov || loadingDelete || loadingCsv;
  const datasetHistory = history.filter(h => h.action === 'Check' || h.action === 'Download').slice(0, 20);

  return (
    <div className="flex flex-col gap-6 w-full">
      <header className="flex items-center justify-between">
        <h1 className="text-2xl font-bold tracking-tight">Dataset</h1>
      </header>

      {/* ── 2-column: Config left | Coverage right ── */}
      <div className="grid grid-cols-1 lg:grid-cols-[380px,1fr] gap-6 items-start">

        {/* Left — fixed config card */}
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-semibold">Dataset Configuration</CardTitle>
          </CardHeader>
          <Separator />
          <CardContent className="pt-4 space-y-4">
            <div className="flex flex-col gap-3">
              <div className="flex flex-col gap-1.5">
                <label className="text-xs text-muted-foreground">Symbol</label>
                <Select value={symbol} onValueChange={setSymbol}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>{SYMBOLS.map(s => <SelectItem key={s} value={s}>{s}</SelectItem>)}</SelectContent>
                </Select>
              </div>
              <div className="flex flex-col gap-1.5">
                <label className="text-xs text-muted-foreground">Timeframe</label>
                <Select value={timeframe} onValueChange={setTimeframe}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>{TIMEFRAMES_ALL.map(t => <SelectItem key={t} value={t}>{t}</SelectItem>)}</SelectContent>
                </Select>
              </div>
              <div className="flex flex-col gap-1.5">
                <label className="text-xs text-muted-foreground">Date From</label>
                <Input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)} className="w-full" style={{ colorScheme: 'dark' }} />
              </div>
              <div className="flex flex-col gap-1.5">
                <label className="text-xs text-muted-foreground">Date To</label>
                <Input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)} className="w-full" style={{ colorScheme: 'dark' }} />
              </div>
            </div>
            <div className="flex flex-col gap-2">
              <Button onClick={handleCheckCoverage} disabled={isBusy} variant="outline" className="w-full gap-2">
                {loadingCov ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
                Check Coverage
              </Button>
              <Button onClick={handleIngest} disabled={isBusy} className="w-full gap-2">
                <DownloadCloud className="w-3.5 h-3.5" />
                Ingest from Bybit
              </Button>
              <Button onClick={handleListTables} disabled={isBusy} variant="secondary" className="w-full gap-2">
                {loadingList ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Database className="w-3.5 h-3.5" />}
                List Tables
              </Button>
              <Button onClick={handleUploadCsvClick} disabled={isBusy || timeframe === 'ALL'} variant="outline" className="w-full gap-2">
                {loadingCsv ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <UploadCloud className="w-3.5 h-3.5" />}
                Upload CSV
                {csvProgress && csvProgress.batches > 0 && (
                  <span className="text-[10px] text-muted-foreground tabular-nums">
                    {csvProgress.batch}/{csvProgress.batches}
                  </span>
                )}
              </Button>
              <input
                ref={csvInputRef}
                type="file"
                accept=".csv"
                className="hidden"
                onChange={handleCsvFileChange}
              />
              <Button onClick={handleDeleteRows} disabled={isBusy} variant="destructive" className="w-full gap-2">
                {loadingDelete ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
                Очистить таблицу
              </Button>
            </div>
            {timeframe === 'ALL' && allIngestStatuses !== null && (
              <AllIngestProgress statuses={allIngestStatuses} />
            )}
            {loadingCsv && csvProgress !== null && csvProgress.batches > 0 && (
              <div className="pt-2 space-y-1">
                <Progress
                  value={(csvProgress.batch / csvProgress.batches) * 100}
                  className="h-1.5 w-full"
                />
                <p className="text-[10px] text-muted-foreground tabular-nums">
                  Батч {csvProgress.batch} / {csvProgress.batches} — {csvProgress.imported.toLocaleString()} строк
                </p>
              </div>
            )}
            {timeframe !== 'ALL' && ingestStages !== null && <IngestProgress stages={ingestStages} />}
          </CardContent>
        </Card>

        {/* Right — coverage result */}
        {timeframe === 'ALL' && allCoverages !== null ? (
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm font-semibold">Coverage: {symbol} — all timeframes</CardTitle>
            </CardHeader>
            <Separator />
            <CardContent className="p-0">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Timeframe</TableHead>
                    <TableHead className="text-right">Rows</TableHead>
                    <TableHead className="w-36">Coverage</TableHead>
                    <TableHead>From</TableHead>
                    <TableHead>To</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {allCoverages.map(row => (
                    <TableRow key={row.tf}>
                      <TableCell className="font-mono text-xs">{row.tf}</TableCell>
                      <TableCell className="text-xs text-right">{row.rows.toLocaleString()}</TableCell>
                      <TableCell>
                        <div className="flex items-center gap-2">
                          <Progress value={row.coverage_pct ?? 0} className="h-1.5 flex-1" />
                          <span className={cn(
                            'text-xs w-10 text-right tabular-nums',
                            (row.coverage_pct ?? 0) >= 95 ? 'text-success' :
                            (row.coverage_pct ?? 0) >= 70 ? 'text-warning' : 'text-destructive',
                          )}>
                            {(row.coverage_pct ?? 0).toFixed(1)}%
                          </span>
                        </div>
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">{row.date_from ?? '--'}</TableCell>
                      <TableCell className="text-xs text-muted-foreground">{row.date_to ?? '--'}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        ) : timeframe !== 'ALL' && coverage ? (
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm font-semibold">Coverage: {symbol} {timeframe}</CardTitle>
            </CardHeader>
            <Separator />
            <CardContent className="pt-4 space-y-4">
              <CoverageBar
                data={[{ name: `${symbol} ${timeframe}`, pct: coverage.coverage_pct ?? 0 }] satisfies BarDatum[]}
                height={100}
              />
              <div className="grid grid-cols-3 gap-4">
                <div className="space-y-1">
                  <p className="text-xs text-muted-foreground">Rows</p>
                  <p className="text-lg font-bold">{coverage.rows?.toLocaleString()}</p>
                </div>
                <div className="space-y-1">
                  <p className="text-xs text-muted-foreground">Expected</p>
                  <p className="text-lg font-bold">{coverage.expected?.toLocaleString()}</p>
                </div>
                <div className="space-y-1">
                  <p className="text-xs text-muted-foreground">Gaps</p>
                  <p className="text-lg font-bold">{coverage.gaps}</p>
                </div>
              </div>
            </CardContent>
          </Card>
        ) : (
          <div className="hidden lg:flex items-center justify-center rounded-lg border border-dashed border-border h-44 text-sm text-muted-foreground">
            Run "Check Coverage" to see chart
          </div>
        )}
      </div>

      {/* ── Available Tables (full width) ── */}
      {tables === null && loadingList && (
        <Card>
          <CardContent className="pt-5 space-y-3">
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-3/4" />
            <Skeleton className="h-4 w-1/2" />
          </CardContent>
        </Card>
      )}
      {tables !== null && (
        <Card>
          <CardHeader className="pb-0">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm font-semibold">Available Tables</CardTitle>
              <span className="text-xs text-muted-foreground">{tables.length} tables</span>
            </div>
          </CardHeader>
          <Separator className="mt-4" />
          <CardContent className="p-0">
            {tables.length === 0 ? (
              <div className="flex items-center justify-center py-12">
                <p className="text-sm text-muted-foreground">No tables found</p>
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Table</TableHead>
                    <TableHead className="text-right">Rows</TableHead>
                    <TableHead className="w-44">Coverage</TableHead>
                    <TableHead>From</TableHead>
                    <TableHead>To</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {tables.map(row => (
                    <TableRow key={row.table_name}>
                      <TableCell className="font-mono text-xs">{row.table_name}</TableCell>
                      <TableCell className="text-xs text-right">{row.rows?.toLocaleString()}</TableCell>
                      <TableCell>
                        <div className="flex items-center gap-2">
                          <Progress value={row.coverage_pct ?? 0} className="h-1.5 flex-1" />
                          <span className={cn(
                            'text-xs w-10 text-right tabular-nums',
                            (row.coverage_pct ?? 0) >= 95 ? 'text-success' :
                            (row.coverage_pct ?? 0) >= 70 ? 'text-warning' : 'text-destructive',
                          )}>
                            {(row.coverage_pct ?? 0).toFixed(1)}%
                          </span>
                        </div>
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">{row.date_from ?? '--'}</TableCell>
                      <TableCell className="text-xs text-muted-foreground">{row.date_to ?? '--'}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      )}

      {/* ── Action history (full width) ── */}
      <Card>
        <CardHeader className="pb-0">
          <div className="flex items-center justify-between">
            <CardTitle className="text-sm font-semibold">Action History</CardTitle>
            <span className="text-xs text-muted-foreground">Last 20</span>
          </div>
        </CardHeader>
        <Separator className="mt-4" />
        <CardContent className="p-0">
          {datasetHistory.length === 0 ? (
            <div className="flex items-center justify-center py-10">
              <p className="text-sm text-muted-foreground">No actions yet</p>
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Time</TableHead>
                  <TableHead>Action</TableHead>
                  <TableHead>Params</TableHead>
                  <TableHead>Result</TableHead>
                  <TableHead className="text-right">ms</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {datasetHistory.map(h => (
                  <TableRow key={h.id}>
                    <TableCell className="font-mono text-xs">{h.time}</TableCell>
                    <TableCell>
                      <Badge variant={h.action === 'Download' ? 'success' : 'info'} className="text-xs">
                        {h.action}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {h.params.symbol} {h.params.timeframe}
                    </TableCell>
                    <TableCell className="text-xs max-w-xs truncate">{h.result}</TableCell>
                    <TableCell className="text-xs text-right text-muted-foreground">{h.durationMs}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
