'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Activity, CheckCircle2, Clock3, RefreshCw, Trash2, XCircle } from 'lucide-react';
import DatasetJobsPanel from '@/components/DatasetJobsPanel';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { useDatasetJobs } from '@/hooks/useDatasetJobs';
import { useDatasetJobsFeed } from '@/hooks/useDatasetJobsFeed';
import { Topics } from '@/lib/topics';
import { cn } from '@/lib/utils';
import { useLocale } from '@/lib/i18nContext';

type QueueHistoryLevel = 'success' | 'error';

interface QueueHistoryEntry {
  id: string;
  ts: string;
  topic: string;
  level: QueueHistoryLevel;
  durationMs: number;
  splitMode: boolean;
  payloadSummary?: Record<string, unknown> | null;
  responseSummary?: Record<string, unknown> | null;
  message?: string | null;
  code?: string | null;
  detail?: string | null;
  correlationId?: string | null;
}

interface QueueHistoryResponse {
  items: QueueHistoryEntry[];
}

const QUEUE_HISTORY_TOPICS = new Set<string>([
  Topics.CMD_DATA_DATASET_JOBS_START,
  Topics.CMD_DATA_DATASET_JOBS_CANCEL,
  Topics.CMD_DATA_DATASET_DELETE_ROWS,
  Topics.CMD_DATA_DATASET_CLEAN_APPLY,
  Topics.CMD_DATA_DATASET_EXPORT,
  Topics.CMD_DATA_DATASET_IMPORT_CSV,
  Topics.CMD_DATA_DATASET_UPSERT_OHLCV,
  Topics.CMD_ANALITIC_DATASET_LOAD,
  Topics.CMD_ANALITIC_DATASET_LOAD_OHLCV,
  Topics.CMD_ANALITIC_DATASET_RECOMPUTE_FEATURES,
  Topics.CMD_ANALITIC_ANOMALY_DBSCAN,
  Topics.CMD_ANALYTICS_TRAIN_START,
]);

const LEVEL_BADGE: Record<QueueHistoryLevel, 'default' | 'secondary' | 'destructive' | 'outline' | 'success' | 'warning' | 'info'> = {
  success: 'success',
  error: 'destructive',
};

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleTimeString();
}

function formatFields(fields?: Record<string, unknown>): string {
  if (!fields || Object.keys(fields).length === 0) return '';
  return JSON.stringify(fields, null, 2);
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function formatRange(startMs?: unknown, endMs?: unknown): string | null {
  if (typeof startMs !== 'number' || typeof endMs !== 'number') return null;
  const start = new Date(startMs);
  const end = new Date(endMs);
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return null;
  const days = Math.max(1, Math.round((endMs - startMs) / 86_400_000));
  return `${start.toLocaleDateString()} - ${end.toLocaleDateString()} (${days}d)`;
}

function humanizeTopic(topic: string): string {
  switch (topic) {
    case Topics.CMD_DATA_DATASET_JOBS_START: return 'Dataset job start';
    case Topics.CMD_DATA_DATASET_JOBS_CANCEL: return 'Dataset job cancel';
    case Topics.CMD_DATA_DATASET_DELETE_ROWS: return 'Delete dataset rows';
    case Topics.CMD_DATA_DATASET_CLEAN_APPLY: return 'Apply dataset clean';
    case Topics.CMD_DATA_DATASET_EXPORT: return 'Export dataset';
    case Topics.CMD_DATA_DATASET_IMPORT_CSV: return 'Import CSV';
    case Topics.CMD_DATA_DATASET_UPSERT_OHLCV: return 'Upsert OHLCV';
    case Topics.CMD_ANALITIC_DATASET_LOAD: return 'Load analitic dataset session';
    case Topics.CMD_ANALITIC_DATASET_LOAD_OHLCV: return 'Load missing OHLCV';
    case Topics.CMD_ANALITIC_DATASET_RECOMPUTE_FEATURES: return 'Recompute features';
    case Topics.CMD_ANALITIC_ANOMALY_DBSCAN: return 'Run DBSCAN';
    case Topics.CMD_ANALYTICS_TRAIN_START: return 'Start training';
    default: return topic;
  }
}

function buildHistorySummary(item: QueueHistoryEntry): { request: string; result: string } {
  const topic = item.topic;
  const payloadSummary = asRecord(item.payloadSummary);
  const responseSummary = asRecord(item.responseSummary);

  const targetTable = (payloadSummary?.target_table ?? payloadSummary?.table ?? payloadSummary?.target_symbol) as string | undefined;
  const timeframe = (payloadSummary?.target_timeframe ?? payloadSummary?.timeframe) as string | undefined;
  const type = payloadSummary?.type as string | undefined;
  const range = formatRange(
    payloadSummary?.target_start_ms ?? payloadSummary?.start_ms,
    payloadSummary?.target_end_ms ?? payloadSummary?.end_ms,
  );

  const requestParts = [humanizeTopic(topic)];
  if (type) requestParts.push(type);
  if (targetTable) requestParts.push(String(targetTable));
  if (timeframe && targetTable !== timeframe) requestParts.push(String(timeframe));
  if (range) requestParts.push(range);

  const resultParts: string[] = [];
  if (item.level === 'error') {
    resultParts.push(item.message ?? 'Request failed');
    if (item.code) resultParts.push(item.code);
    if (item.detail && item.detail !== item.message) resultParts.push(item.detail);
  } else {
    if (responseSummary?.status) resultParts.push(String(responseSummary.status));
    if (responseSummary?.job_id) resultParts.push(`job ${String(responseSummary.job_id).slice(0, 8)}`);
    if (responseSummary?.deduped === true) resultParts.push('deduped');
    if (typeof responseSummary?.rows_written === 'number') resultParts.push(`${responseSummary.rows_written} rows written`);
    if (typeof responseSummary?.rows_updated === 'number') resultParts.push(`${responseSummary.rows_updated} rows updated`);
    if (typeof responseSummary?.rows_deleted === 'number') resultParts.push(`${responseSummary.rows_deleted} rows deleted`);
    if (typeof responseSummary?.rows_affected === 'number') resultParts.push(`${responseSummary.rows_affected} rows affected`);
    if (typeof responseSummary?.audit_id === 'number') resultParts.push(`audit #${responseSummary.audit_id}`);
    if (resultParts.length === 0) resultParts.push('Accepted');
  }

  return {
    request: requestParts.join(' · '),
    result: resultParts.join(' · '),
  };
}

export default function QueuePage() {
  const { t } = useLocale();
  const jobs = useDatasetJobs();
  useDatasetJobsFeed(1_500);

  const [history, setHistory] = useState<QueueHistoryEntry[]>([]);
  const [loading, setLoading] = useState(true);

  const loadHistory = useCallback(async () => {
    const base = process.env.NEXT_PUBLIC_BASE_PATH ?? '';
    const res = await fetch(`${base}/api/queue/history?limit=250`, { cache: 'no-store' });
    const data = await res.json() as QueueHistoryResponse;
    const items = Array.isArray(data.items)
      ? data.items.filter((item) => item?.topic && QUEUE_HISTORY_TOPICS.has(item.topic))
      : [];
    setHistory(items);
  }, []);

  const refreshQueue = useCallback(async () => {
    setLoading(true);
    try {
      await loadHistory();
    } finally {
      setLoading(false);
    }
  }, [loadHistory]);

  const clearQueue = useCallback(async () => {
    const base = process.env.NEXT_PUBLIC_BASE_PATH ?? '';
    await fetch(`${base}/api/queue/history`, { method: 'DELETE' });
    setHistory([]);
  }, []);

  useEffect(() => {
    void refreshQueue();
  }, [refreshQueue]);

  useEffect(() => {
    const refreshNow = () => {
      if (typeof document !== 'undefined' && document.hidden) return;
      void loadHistory();
    };

    const timer = window.setInterval(() => {
      refreshNow();
    }, 2_000);

    window.addEventListener('focus', refreshNow);
    document.addEventListener('visibilitychange', refreshNow);

    return () => {
      window.clearInterval(timer);
      window.removeEventListener('focus', refreshNow);
      document.removeEventListener('visibilitychange', refreshNow);
    };
  }, [loadHistory]);

  const activeJobs = useMemo(() => jobs.filter(job => !job.finished).length, [jobs]);
  const runningJobs = useMemo(() => jobs.filter(job => job.status === 'running').length, [jobs]);
  const queuedJobs = useMemo(() => jobs.filter(job => job.status === 'queued').length, [jobs]);
  const finishedJobs = useMemo(() => jobs.filter(job => job.finished).length, [jobs]);
  const errors = useMemo(() => history.filter(item => item.level === 'error').length, [history]);
  const lastHistoryItem = history[0];

  return (
    <div className="space-y-4 lg:space-y-5">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">{t('queue.title')}</h1>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span className="font-mono">dataset jobs + queue history</span>
            <span>·</span>
            <span>{lastHistoryItem ? formatTime(lastHistoryItem.ts) : t('queue.noRequests')}</span>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={refreshQueue} disabled={loading}>
            <RefreshCw className={cn('h-4 w-4', loading && 'animate-spin')} />
            {t('common.refresh')}
          </Button>
          <Button variant="ghost" size="sm" onClick={clearQueue} disabled={history.length === 0}>
            <Trash2 className="h-4 w-4" />
            {t('queue.clear')}
          </Button>
        </div>
      </header>

      <section className="grid grid-cols-1 gap-3 md:grid-cols-5">
        <Card className="border-l-4 border-l-primary">
          <CardHeader className="px-5 pb-2 pt-5">
            <CardTitle className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
              <Activity className="h-3.5 w-3.5" />
              {t('queue.activeJobs')}
            </CardTitle>
          </CardHeader>
          <CardContent className="px-5 pb-5">
            <div className="text-3xl font-bold">{activeJobs}</div>
          </CardContent>
        </Card>
        <Card className="border-l-4 border-l-sky-400">
          <CardHeader className="px-5 pb-2 pt-5">
            <CardTitle className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
              <Activity className="h-3.5 w-3.5" />
              {t('queue.runningJobs')}
            </CardTitle>
          </CardHeader>
          <CardContent className="px-5 pb-5">
            <div className="text-3xl font-bold">{runningJobs}</div>
          </CardContent>
        </Card>
        <Card className="border-l-4 border-l-amber-400">
          <CardHeader className="px-5 pb-2 pt-5">
            <CardTitle className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
              <Clock3 className="h-3.5 w-3.5" />
              {t('queue.queuedJobs')}
            </CardTitle>
          </CardHeader>
          <CardContent className="px-5 pb-5">
            <div className="text-3xl font-bold">{queuedJobs}</div>
          </CardContent>
        </Card>
        <Card className="border-l-4 border-l-emerald-400">
          <CardHeader className="px-5 pb-2 pt-5">
            <CardTitle className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
              <Activity className="h-3.5 w-3.5" />
              {t('queue.finishedJobs')}
            </CardTitle>
          </CardHeader>
          <CardContent className="px-5 pb-5">
            <div className="text-3xl font-bold">{finishedJobs}</div>
          </CardContent>
        </Card>
        <Card className="border-l-4 border-l-destructive">
          <CardHeader className="px-5 pb-2 pt-5">
            <CardTitle className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
              <Activity className="h-3.5 w-3.5" />
              {t('queue.requestErrors')}
            </CardTitle>
          </CardHeader>
          <CardContent className="px-5 pb-5">
            <div className="text-3xl font-bold">{errors}</div>
          </CardContent>
        </Card>
      </section>

      <DatasetJobsPanel />

      <Card>
        <CardHeader className="px-5 pb-3 pt-5">
          <CardTitle className="text-base">{t('queue.requests')}</CardTitle>
        </CardHeader>
        <CardContent className="px-5 pb-5">
          <div className="overflow-x-auto rounded-md border border-border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-[92px]">{t('common.time')}</TableHead>
                  <TableHead className="w-[110px]">{t('common.status')}</TableHead>
                  <TableHead className="w-[120px]">Duration</TableHead>
                  <TableHead className="w-[240px]">Operation</TableHead>
                  <TableHead>{t('logs.details')}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {loading ? (
                  Array.from({ length: 6 }).map((_, idx) => (
                    <TableRow key={idx}>
                      <TableCell><Skeleton className="h-4 w-14" /></TableCell>
                      <TableCell><Skeleton className="h-5 w-16" /></TableCell>
                      <TableCell><Skeleton className="h-4 w-24" /></TableCell>
                      <TableCell><Skeleton className="h-4 w-32" /></TableCell>
                      <TableCell><Skeleton className="h-4 w-full" /></TableCell>
                    </TableRow>
                  ))
                ) : history.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={5} className="h-28 text-center text-muted-foreground">
                      {t('queue.noRequests')}
                    </TableCell>
                  </TableRow>
                ) : (
                  history.map(item => {
                    const summary = buildHistorySummary(item);
                    return (
                      <TableRow key={item.id} className="align-top">
                        <TableCell className="font-mono text-xs text-muted-foreground">{formatTime(item.ts)}</TableCell>
                        <TableCell>
                          <Badge variant={LEVEL_BADGE[item.level]} className="inline-flex items-center gap-1 uppercase">
                            {item.level === 'success' ? <CheckCircle2 className="h-3 w-3" /> : <XCircle className="h-3 w-3" />}
                            {item.level}
                          </Badge>
                        </TableCell>
                        <TableCell className="font-mono text-xs text-muted-foreground">{item.durationMs} ms</TableCell>
                        <TableCell className="font-mono text-xs">{item.topic}</TableCell>
                        <TableCell>
                          <div className="max-w-[980px] space-y-1">
                            <div className="text-sm text-foreground">{summary.request}</div>
                            <div className={cn('text-xs', item.level === 'error' ? 'text-destructive' : 'text-muted-foreground')}>
                              {summary.result}
                            </div>
                            {(item.payloadSummary || item.responseSummary || item.code || item.correlationId) && (
                              <pre className="max-h-36 overflow-auto whitespace-pre-wrap break-words rounded bg-background/40 p-2 font-mono text-[11px] leading-relaxed text-muted-foreground">
                                {formatFields({
                                  splitMode: item.splitMode,
                                  correlationId: item.correlationId,
                                  code: item.code,
                                  payloadSummary: item.payloadSummary,
                                  responseSummary: item.responseSummary,
                                })}
                              </pre>
                            )}
                          </div>
                        </TableCell>
                      </TableRow>
                    );
                  })
                )}
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}