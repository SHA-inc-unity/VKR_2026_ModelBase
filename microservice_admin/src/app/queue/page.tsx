'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Activity, Clock3, RefreshCw, Trash2 } from 'lucide-react';
import DatasetJobsPanel from '@/components/DatasetJobsPanel';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { useDatasetJobs } from '@/hooks/useDatasetJobs';
import { useDatasetJobsFeed } from '@/hooks/useDatasetJobsFeed';
import { cn } from '@/lib/utils';
import { useLocale } from '@/lib/i18nContext';

type RuntimeLogLevel = 'info' | 'success' | 'warn' | 'error';

interface RuntimeLogEntry {
  id: number;
  ts: string;
  level: RuntimeLogLevel;
  source: string;
  event: string;
  message?: string;
  fields?: Record<string, unknown>;
}

interface RuntimeLogResponse {
  logs: RuntimeLogEntry[];
}

const LEVEL_BADGE: Record<RuntimeLogLevel, 'default' | 'secondary' | 'destructive' | 'outline' | 'success' | 'warning' | 'info'> = {
  info: 'info',
  success: 'success',
  warn: 'warning',
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

export default function QueuePage() {
  const { t } = useLocale();
  const jobs = useDatasetJobs();
  useDatasetJobsFeed(5_000);

  const [logs, setLogs] = useState<RuntimeLogEntry[]>([]);
  const [loading, setLoading] = useState(true);

  const loadLogs = useCallback(async () => {
    const base = process.env.NEXT_PUBLIC_BASE_PATH ?? '';
    const res = await fetch(`${base}/api/logs?limit=250`, { cache: 'no-store' });
    const data = await res.json() as RuntimeLogResponse;
    setLogs(data.logs ?? []);
  }, []);

  const refreshLogs = useCallback(async () => {
    setLoading(true);
    try {
      await loadLogs();
    } finally {
      setLoading(false);
    }
  }, [loadLogs]);

  const clearLogs = useCallback(async () => {
    const base = process.env.NEXT_PUBLIC_BASE_PATH ?? '';
    await fetch(`${base}/api/logs`, { method: 'DELETE' });
    setLogs([]);
  }, []);

  useEffect(() => {
    void refreshLogs();
  }, [refreshLogs]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      void loadLogs();
    }, 5_000);
    return () => window.clearInterval(timer);
  }, [loadLogs]);

  const activeJobs = useMemo(() => jobs.filter(job => !job.finished).length, [jobs]);
  const runningJobs = useMemo(() => jobs.filter(job => job.status === 'running').length, [jobs]);
  const queuedJobs = useMemo(() => jobs.filter(job => job.status === 'queued').length, [jobs]);
  const finishedJobs = useMemo(() => jobs.filter(job => job.finished).length, [jobs]);
  const errors = useMemo(() => logs.filter(item => item.level === 'error').length, [logs]);
  const lastLog = logs[0];

  return (
    <div className="space-y-4 lg:space-y-5">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">{t('queue.title')}</h1>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span className="font-mono">admin-runtime + dataset jobs</span>
            <span>·</span>
            <span>{lastLog ? formatTime(lastLog.ts) : t('queue.noRequests')}</span>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={refreshLogs} disabled={loading}>
            <RefreshCw className={cn('h-4 w-4', loading && 'animate-spin')} />
            {t('common.refresh')}
          </Button>
          <Button variant="ghost" size="sm" onClick={clearLogs} disabled={logs.length === 0}>
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
                  <TableHead className="w-[92px]">{t('logs.level')}</TableHead>
                  <TableHead className="w-[150px]">{t('logs.source')}</TableHead>
                  <TableHead className="w-[210px]">{t('logs.event')}</TableHead>
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
                ) : logs.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={5} className="h-28 text-center text-muted-foreground">
                      {t('queue.noRequests')}
                    </TableCell>
                  </TableRow>
                ) : (
                  logs.map(item => (
                    <TableRow key={item.id} className="align-top">
                      <TableCell className="font-mono text-xs text-muted-foreground">{formatTime(item.ts)}</TableCell>
                      <TableCell>
                        <Badge variant={LEVEL_BADGE[item.level]} className="uppercase">{item.level}</Badge>
                      </TableCell>
                      <TableCell className="font-mono text-xs">{item.source}</TableCell>
                      <TableCell className="font-mono text-xs">{item.event}</TableCell>
                      <TableCell>
                        <div className="max-w-[980px] space-y-1">
                          {item.message && <div className="text-sm text-foreground">{item.message}</div>}
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