'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Activity, Clock3, RefreshCw, RotateCw, Trash2 } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Topics } from '@/lib/topics';
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

const CHECK_TIMEOUT_MS = 8_000;

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

async function postKafkaDiagnostic(topic: string) {
  const base = process.env.NEXT_PUBLIC_BASE_PATH ?? '';
  await fetch(`${base}/api/kafka`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ topic, payload: {}, timeoutMs: CHECK_TIMEOUT_MS }),
  });
}

async function fetchHealthDiagnostic() {
  const base = process.env.NEXT_PUBLIC_BASE_PATH ?? '';
  await fetch(`${base}/api/health`, { cache: 'no-store' });
}

export default function LogsPage() {
  const { t } = useLocale();
  const [logs, setLogs] = useState<RuntimeLogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [checking, setChecking] = useState(false);

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

  const runCheck = useCallback(async () => {
    setChecking(true);
    try {
      await Promise.allSettled([
        fetchHealthDiagnostic(),
        postKafkaDiagnostic(Topics.CMD_DATA_HEALTH),
        postKafkaDiagnostic(Topics.CMD_ANALYTICS_HEALTH),
      ]);
      await loadLogs();
    } finally {
      setChecking(false);
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
    const id = window.setInterval(() => { void loadLogs(); }, 5_000);
    return () => window.clearInterval(id);
  }, [loadLogs]);

  const errors = useMemo(() => logs.filter(item => item.level === 'error').length, [logs]);
  const warnings = useMemo(() => logs.filter(item => item.level === 'warn').length, [logs]);
  const lastLog = logs[0];

  return (
    <div className="space-y-4 lg:space-y-5">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">{t('logs.title')}</h1>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span className="font-mono">admin-runtime</span>
            <span>·</span>
            <span>{lastLog ? formatTime(lastLog.ts) : t('logs.noEvents')}</span>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={refreshLogs} disabled={loading || checking}>
            <RefreshCw className={cn('h-4 w-4', loading && 'animate-spin')} />
            {t('common.refresh')}
          </Button>
          <Button size="sm" onClick={runCheck} disabled={checking}>
            <RotateCw className={cn('h-4 w-4', checking && 'animate-spin')} />
            {t('logs.runCheck')}
          </Button>
          <Button variant="ghost" size="sm" onClick={clearLogs} disabled={checking || logs.length === 0}>
            <Trash2 className="h-4 w-4" />
            {t('logs.clear')}
          </Button>
        </div>
      </header>

      <section className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <Card className="border-l-4 border-l-primary">
          <CardHeader className="px-5 pb-2 pt-5">
            <CardTitle className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
              <Activity className="h-3.5 w-3.5" />
              {t('logs.totalEvents')}
            </CardTitle>
          </CardHeader>
          <CardContent className="px-5 pb-5">
            {loading ? <Skeleton className="h-8 w-20" /> : <div className="text-3xl font-bold">{logs.length}</div>}
          </CardContent>
        </Card>
        <Card className="border-l-4 border-l-warning">
          <CardHeader className="px-5 pb-2 pt-5">
            <CardTitle className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
              <Clock3 className="h-3.5 w-3.5" />
              {t('logs.warnings')}
            </CardTitle>
          </CardHeader>
          <CardContent className="px-5 pb-5">
            {loading ? <Skeleton className="h-8 w-20" /> : <div className="text-3xl font-bold">{warnings}</div>}
          </CardContent>
        </Card>
        <Card className="border-l-4 border-l-destructive">
          <CardHeader className="px-5 pb-2 pt-5">
            <CardTitle className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
              <Activity className="h-3.5 w-3.5" />
              {t('logs.errors')}
            </CardTitle>
          </CardHeader>
          <CardContent className="px-5 pb-5">
            {loading ? <Skeleton className="h-8 w-20" /> : <div className="text-3xl font-bold">{errors}</div>}
          </CardContent>
        </Card>
      </section>

      <Card>
        <CardHeader className="px-5 pb-3 pt-5">
          <CardTitle className="text-base">{t('logs.events')}</CardTitle>
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
                      {t('logs.noEvents')}
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