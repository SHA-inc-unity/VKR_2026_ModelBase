'use client';
import dynamic from 'next/dynamic';
import { useEffect, useRef, useState } from 'react';
import { BrainCircuit, Loader2 } from 'lucide-react';
import { kafkaCall } from '@/lib/kafkaClient';
import { Topics } from '@/lib/topics';
import type { TrainStatus, TrainProgressEvent } from '@/lib/types';
import { useToast } from '@/components/Toast';
import { SYMBOLS, TIMEFRAMES } from '@/lib/constants';
import { useHistory } from '@/hooks/useHistory';
import { useEvents } from '@/hooks/useEvents';
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
import type { StepPoint } from '@/components/charts/ProgressLine';

// Dynamic import — Recharts requires browser context
const ProgressLine = dynamic(
  () => import('@/components/charts/ProgressLine').then(m => m.ProgressLine),
  { ssr: false, loading: () => <Skeleton className="h-[120px] w-full" /> },
);

const PARAMS_KEY = 'modelline:params:train';
const POLL_MS    = 3_000;

function todayStr()      { return new Date().toISOString().slice(0, 10); }
function daysAgoStr(n: number) {
  const d = new Date(); d.setDate(d.getDate() - n); return d.toISOString().slice(0, 10);
}
function loadParams() {
  if (typeof window === 'undefined') return null;
  try { const r = localStorage.getItem(PARAMS_KEY); return r ? JSON.parse(r) : null; }
  catch { return null; }
}

export default function TrainPage() {
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

  const [status,  setStatus]  = useState<TrainStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [progressHistory, setProgressHistory] = useState<StepPoint[]>([]);
  const pollRef       = useRef<ReturnType<typeof setInterval> | null>(null);
  const trainStartRef = useRef<number>(0);

  const stopPolling = () => {
    if (pollRef.current !== null) { clearInterval(pollRef.current); pollRef.current = null; }
  };
  useEffect(() => () => stopPolling(), []);

  const startPolling = (sym: string, tf: string) => {
    stopPolling();
    pollRef.current = setInterval(async () => {
      try {
        const res = await kafkaCall<TrainStatus>(Topics.CMD_ANALYTICS_TRAIN_STATUS, { symbol: sym, timeframe: tf });
        setStatus(res);
        if (res.progress !== undefined) {
          setProgressHistory(prev => [
            ...prev,
            { step: prev.length + 1, loss: Math.round(res.progress! * 100) },
          ]);
        }
        if (res.status !== 'running') {
          stopPolling(); setLoading(false);
          const dur = Date.now() - trainStartRef.current;
          addEntry({ action: 'Train', params: { symbol: sym, timeframe: tf, dateFrom, dateTo }, result: `${res.status}${res.model_id ? ` model=${res.model_id}` : ''}`, durationMs: dur });
          toast(res.message ?? `Training ${res.status}`, res.status === 'error' ? 'error' : 'success');
        }
      } catch { /* ignore poll failures */ }
    }, POLL_MS);
  };

  const handleTrain = async () => {
    setLoading(true); setStatus(null); setProgressHistory([]); trainStartRef.current = Date.now();
    try {
      const res = await kafkaCall<TrainStatus>(
        Topics.CMD_ANALYTICS_TRAIN_START,
        { symbol, timeframe, start_ms: new Date(dateFrom).getTime(), end_ms: new Date(dateTo + 'T23:59:59').getTime() },
        30_000,
      );
      setStatus(res);
      if (res.status === 'running') {
        toast('Training started - polling for status...', 'info');
        startPolling(symbol, timeframe);
      } else {
        setLoading(false);
        const dur = Date.now() - trainStartRef.current;
        addEntry({ action: 'Train', params: { symbol, timeframe, dateFrom, dateTo }, result: `${res.status}${res.model_id ? ` model=${res.model_id}` : ''}`, durationMs: dur });
        toast(res.message ?? `Training ${res.status}`, res.status === 'error' ? 'error' : 'success');
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setStatus({ status: 'error', message: msg }); setLoading(false);
      addEntry({ action: 'Train', params: { symbol, timeframe, dateFrom, dateTo }, result: `Error: ${msg}`, durationMs: Date.now() - trainStartRef.current });
      toast(msg, 'error');
    }
  };

  const progressPct  = status?.progress !== undefined ? Math.round(status.progress * 100) : null;
  const trainHistory = history.filter(h => h.action === 'Train').slice(0, 20);
  const [activeTab, setActiveTab] = useState<'new' | 'history'>('new');

  useEvents({
    EVT_ANALYTICS_TRAIN_PROGRESS: (evt: TrainProgressEvent) => {
      setProgressHistory(prev => [
        ...prev,
        { step: prev.length + 1, loss: Math.round(evt.progress * 100) },
      ]);
      setStatus(prev => ({
        ...prev,
        status: 'running',
        progress: evt.progress,
        message: evt.message,
      }));
    },
  });

  const statusVariant = (s: string) =>
    s === 'done' || s === 'ok' ? 'success' :
    s === 'error'              ? 'destructive' :
    s === 'running'            ? 'info' : 'warning';

  return (
    <div className="flex flex-col gap-4 sm:gap-6 w-full">

      {/* ── Header with inline tab switcher ── */}
      <header className="flex items-center justify-between gap-4 flex-wrap">
        <h1 className="text-2xl font-bold tracking-tight">Model Training</h1>
        <div className="flex gap-1 p-1 rounded-md bg-muted">
          {(['new', 'history'] as const).map(tab => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={cn(
                'px-3 py-1.5 text-sm font-medium rounded transition-colors',
                activeTab === tab
                  ? 'bg-card text-foreground shadow-sm'
                  : 'text-muted-foreground hover:text-foreground',
              )}
            >
              {tab === 'new' ? 'New Training' : 'History'}
            </button>
          ))}
        </div>
      </header>

      {/* ── 2-column layout on lg+ ── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 sm:gap-6 items-start">

        {/* ── Left: Config + Status ── */}
        <div className={cn('flex flex-col gap-4', activeTab !== 'new' && 'hidden lg:flex')}>
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm font-semibold">Train Configuration</CardTitle>
            </CardHeader>
            <Separator />
            <CardContent className="pt-4 space-y-4">
              <div className="flex flex-wrap gap-4 items-end">
                <div className="flex flex-col gap-1.5 w-44">
                  <label className="text-xs text-muted-foreground">Symbol</label>
                  <Select value={symbol} onValueChange={setSymbol}>
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>{SYMBOLS.map(s => <SelectItem key={s} value={s}>{s}</SelectItem>)}</SelectContent>
                  </Select>
                </div>
                <div className="flex flex-col gap-1.5 w-32">
                  <label className="text-xs text-muted-foreground">Timeframe</label>
                  <Select value={timeframe} onValueChange={setTimeframe}>
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>{TIMEFRAMES.map(t => <SelectItem key={t} value={t}>{t}</SelectItem>)}</SelectContent>
                  </Select>
                </div>
                <div className="flex flex-col gap-1.5">
                  <label className="text-xs text-muted-foreground">Date From</label>
                  <Input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)} className="w-40" style={{ colorScheme: 'dark' }} />
                </div>
                <div className="flex flex-col gap-1.5">
                  <label className="text-xs text-muted-foreground">Date To</label>
                  <Input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)} className="w-40" style={{ colorScheme: 'dark' }} />
                </div>
                <Button onClick={handleTrain} disabled={loading} className="gap-2 self-end">
                  {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <BrainCircuit className="w-3.5 h-3.5" />}
                  {loading ? 'Training...' : 'Start Training'}
                </Button>
              </div>
            </CardContent>
          </Card>

          {/* Status card */}
          {status && (
            <Card>
              <CardContent className="pt-5 space-y-4">
                <div className="flex items-center gap-3">
                  <Badge variant={statusVariant(status.status) as Parameters<typeof Badge>[0]['variant']}>{status.status}</Badge>
                  {status.message && <span className="text-sm text-muted-foreground">{status.message}</span>}
                </div>

                {progressPct !== null && (
                  <div className="space-y-2">
                    {progressHistory.length >= 2 ? (
                      <ProgressLine points={progressHistory} height={120} />
                    ) : (
                      <Progress value={progressPct} className="h-2" />
                    )}
                    <div className="flex justify-between text-xs text-muted-foreground">
                      <span>Progress</span>
                      <span>{progressPct}%</span>
                    </div>
                  </div>
                )}

                {status.model_id && (
                  <div className="space-y-1">
                    <div className="text-xs text-muted-foreground">Model ID</div>
                    <code className="block text-xs px-3 py-2 rounded-md bg-muted font-mono">{status.model_id}</code>
                  </div>
                )}
              </CardContent>
            </Card>
          )}
        </div>

        {/* ── Right: Training history ── */}
        <div className={cn(activeTab !== 'history' && 'hidden lg:block')}>
          <Card>
            <CardHeader className="pb-0">
              <div className="flex items-center justify-between">
                <CardTitle className="text-sm font-semibold">Training History</CardTitle>
                <span className="text-xs text-muted-foreground">Last 20 runs</span>
              </div>
            </CardHeader>
            <Separator className="mt-4" />
            <CardContent className="p-0">
              {trainHistory.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-12 gap-2">
                  <p className="text-sm font-medium">No training runs yet</p>
                  <p className="text-xs text-muted-foreground">Start a training session to see results here</p>
                </div>
              ) : (
                <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Time</TableHead>
                      <TableHead>Symbol</TableHead>
                      <TableHead>TF</TableHead>
                      <TableHead>Dates</TableHead>
                      <TableHead>Result</TableHead>
                      <TableHead className="text-right">ms</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {trainHistory.map(h => (
                      <TableRow key={h.id}>
                        <TableCell className="font-mono text-xs">{h.time}</TableCell>
                        <TableCell className="text-xs">{h.params.symbol ?? '--'}</TableCell>
                        <TableCell className="text-xs">{h.params.timeframe ?? '--'}</TableCell>
                        <TableCell className="text-xs">
                          {h.params.dateFrom && h.params.dateTo
                            ? `${h.params.dateFrom} > ${h.params.dateTo}`
                            : '--'}
                        </TableCell>
                        <TableCell className="text-xs max-w-xs truncate">{h.result}</TableCell>
                        <TableCell className="text-xs text-right text-muted-foreground">{h.durationMs}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
