'use client';
import { useEffect, useState } from 'react';
import { CheckCircle2, XCircle } from 'lucide-react';
import { SmoothProgress } from '@/components/ui/smooth-progress';
import { cn } from '@/lib/utils';
import type { DatasetJobView } from '@/hooks/useDatasetJobs';
import {
  INGEST_EXECUTION_SLOT_COUNT,
  formatIngestScopeLabel,
  humanizeJobStage,
  shortenMessage,
  type TfMeta,
  type TfStatus,
} from '../_lib/datasetHelpers';

export function AllIngestProgress({
  statuses,
  meta,
  jobs,
  jobIds,
}: {
  statuses: Record<string, TfStatus>;
  meta:     Record<string, TfMeta>;
  jobs:     DatasetJobView[];
  jobIds:   Record<string, string>;
}) {
  const [, setTick] = useState(0);
  const hasActive = Object.values(statuses).some(s => s === 'running' || s === 'queued');
  useEffect(() => {
    if (!hasActive) return;
    const id = setInterval(() => setTick(t => t + 1), 1_000);
    return () => clearInterval(id);
  }, [hasActive]);

  function fmtDur(ms: number): string {
    if (ms < 1_000) return '<1с';
    if (ms < 60_000) return `${Math.round(ms / 1_000)}с`;
    const m = Math.floor(ms / 60_000);
    const s = Math.round((ms % 60_000) / 1_000);
    return `${m}м${s}с`;
  }

  const jobsById = new Map(jobs.map(job => [job.job_id, job]));
  const rows = Object.keys(statuses).map(scopeKey => {
    const jobId = jobIds[scopeKey];
    const job = jobId ? jobsById.get(jobId) : undefined;
    const m = meta[scopeKey];
    return {
      scopeKey,
      label: formatIngestScopeLabel(scopeKey),
      status: statuses[scopeKey],
      meta: m,
      job,
      jobId,
    };
  });

  const runningRows = rows
    .filter(row => row.status === 'running')
    .sort((a, b) => (a.meta?.runningAt ?? a.meta?.startedAt ?? 0) - (b.meta?.runningAt ?? b.meta?.startedAt ?? 0));
  const queuedRows = rows
    .filter(row => row.status === 'queued')
    .sort((a, b) => (a.meta?.startedAt ?? 0) - (b.meta?.startedAt ?? 0));
  const doneRows = rows.filter(row => row.status === 'done');
  const errorRows = rows.filter(row => row.status === 'error');
  const recentRows = [...doneRows, ...errorRows]
    .sort((a, b) => (b.meta?.endedAt ?? 0) - (a.meta?.endedAt ?? 0))
    .slice(0, 6);

  const stalledMs = queuedRows.length > 0 && runningRows.length === 0
    ? Date.now() - Math.min(...queuedRows.map(row => row.meta?.startedAt ?? Date.now()))
    : null;
  const isStalled = stalledMs != null && stalledMs >= 15_000;

  return (
    <div className="pt-2 space-y-3">
      <div className="grid grid-cols-2 gap-2 text-[10px] sm:grid-cols-4">
        <div className="rounded-md border border-border bg-muted/30 px-2.5 py-2">
          <div className="text-muted-foreground">Execution slots</div>
          <div className="mt-0.5 font-semibold tabular-nums text-foreground">{runningRows.length} / {INGEST_EXECUTION_SLOT_COUNT}</div>
        </div>
        <div className="rounded-md border border-border bg-muted/30 px-2.5 py-2">
          <div className="text-muted-foreground">Queue</div>
          <div className="mt-0.5 font-semibold tabular-nums text-foreground">{queuedRows.length}</div>
        </div>
        <div className="rounded-md border border-border bg-muted/30 px-2.5 py-2">
          <div className="text-muted-foreground">Done</div>
          <div className="mt-0.5 font-semibold tabular-nums text-foreground">{doneRows.length}</div>
        </div>
        <div className="rounded-md border border-border bg-muted/30 px-2.5 py-2">
          <div className="text-muted-foreground">Errors</div>
          <div className="mt-0.5 font-semibold tabular-nums text-destructive">{errorRows.length}</div>
        </div>
      </div>

      {isStalled && (
        <div className="rounded-md border border-yellow-500/30 bg-yellow-500/10 px-3 py-2 text-[11px] text-yellow-200">
          Очередь есть, но ни один execution slot не активен уже {fmtDur(stalledMs ?? 0)}. Это похоже на stalled-state между queue и scheduler.
        </div>
      )}

      <div className="space-y-2">
        <div className="text-[11px] font-medium text-foreground">Execution slots</div>
        <div className="grid gap-2">
          {Array.from({ length: Math.max(INGEST_EXECUTION_SLOT_COUNT, runningRows.length) }, (_, slotIdx) => {
            const row = runningRows[slotIdx];
            if (!row) {
              return (
                <div key={slotIdx} className="rounded-md border border-dashed border-border bg-muted/20 px-3 py-2.5">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-[10px] uppercase tracking-wide text-muted-foreground">Slot {slotIdx + 1}</span>
                    <span className="text-[10px] text-muted-foreground">idle</span>
                  </div>
                  <div className="mt-1 text-[11px] text-muted-foreground">
                    {queuedRows.length > 0 ? 'Ожидает следующую queued job' : 'Нет активных ingest jobs'}
                  </div>
                </div>
              );
            }

            const stage = humanizeJobStage(row.meta?.stage ?? row.job?.stage);
            const detail = row.job?.detail ?? row.meta?.detail ?? 'Job исполняется в microservice_data';
            const pct = row.job?.progress ?? row.meta?.pct ?? 0;
            const elapsed = Date.now() - (row.meta?.runningAt ?? row.meta?.startedAt ?? Date.now());

            return (
              <div key={row.scopeKey} className="rounded-md border border-primary/20 bg-primary/5 px-3 py-2.5 space-y-2">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-[10px] uppercase tracking-wide text-muted-foreground">Slot {slotIdx + 1}</span>
                      <span className="text-xs font-mono text-foreground">{row.label}</span>
                      <span className="text-[10px] text-muted-foreground">{row.jobId ? `job ${row.jobId.slice(0, 8)}` : 'job'}</span>
                    </div>
                    <div className="mt-1 text-[11px] font-medium text-foreground">{stage}</div>
                    <div className="break-words text-[10px] text-muted-foreground">{shortenMessage(detail)}</div>
                  </div>
                  <div className="text-right shrink-0">
                    <div className="text-xs font-semibold tabular-nums text-primary">{pct}%</div>
                    <div className="text-[10px] tabular-nums text-muted-foreground">{fmtDur(elapsed)}</div>
                  </div>
                </div>
                <SmoothProgress value={pct} running className="h-1" />
              </div>
            );
          })}
        </div>
      </div>

      {queuedRows.length > 0 && (
        <div className="space-y-2">
          <div className="flex items-center justify-between text-[11px]">
            <span className="font-medium text-foreground">Queue</span>
            <span className="tabular-nums text-muted-foreground">{queuedRows.length} jobs</span>
          </div>
          <div className="rounded-md border border-border bg-muted/20 px-3 py-2.5">
            <div className="flex flex-col gap-1.5">
              {queuedRows.slice(0, 6).map(row => {
                const waitMs = Date.now() - (row.meta?.startedAt ?? Date.now());
                return (
                  <div key={row.scopeKey} className="flex items-center justify-between gap-2 text-[11px]">
                    <div className="min-w-0 flex items-center gap-2">
                      <div className="w-3 h-3 rounded-full border-2 border-muted-foreground/60 bg-muted-foreground/20" />
                      <span className="font-mono text-foreground">{row.label}</span>
                      <span className="truncate text-muted-foreground">ждёт планировщика</span>
                    </div>
                    <span className="shrink-0 tabular-nums text-muted-foreground">{fmtDur(waitMs)}</span>
                  </div>
                );
              })}
              {queuedRows.length > 6 && (
                <div className="text-[10px] text-muted-foreground">+{queuedRows.length - 6} ещё в очереди</div>
              )}
            </div>
          </div>
        </div>
      )}

      {recentRows.length > 0 && (
        <div className="space-y-2">
          <div className="text-[11px] font-medium text-foreground">Recent results</div>
          <div className="rounded-md border border-border bg-muted/20 px-3 py-2.5">
            <div className="flex flex-col gap-1.5">
              {recentRows.map(row => {
                const elapsed = row.meta?.endedAt != null
                  ? row.meta.endedAt - (row.meta.runningAt ?? row.meta.startedAt)
                  : undefined;
                const isError = row.status === 'error';
                const completedRows = row.meta?.rows ?? 0;
                const recentMessage = isError
                  ? shortenMessage(row.meta?.error ?? 'Job failed')
                  : shortenMessage(row.meta?.detail ?? (completedRows > 0 ? 'Job завершена' : 'Дозагрузка не потребовалась'));
                return (
                  <div key={row.scopeKey} className="flex items-start justify-between gap-3 text-[11px]">
                    <div className="min-w-0 flex items-start gap-2">
                      {isError
                        ? <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-destructive" />
                        : <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-success" />}
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <span className={cn('font-mono', isError ? 'text-destructive' : 'text-foreground')}>{row.label}</span>
                          {row.meta?.rows !== undefined && !isError && (
                            <span className="tabular-nums text-muted-foreground">
                              {completedRows > 0 ? `${completedRows.toLocaleString()} новых строк` : 'без новых строк'}
                            </span>
                          )}
                        </div>
                        <div className={cn('break-words text-[10px]', isError ? 'text-destructive/80' : 'text-muted-foreground')}>
                          {recentMessage}
                        </div>
                      </div>
                    </div>
                    {elapsed !== undefined && (
                      <span className="shrink-0 tabular-nums text-[10px] text-muted-foreground">{fmtDur(elapsed)}</span>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}

      {!hasActive && recentRows.length === 0 && (
        <div className="rounded-md border border-dashed border-border bg-muted/20 px-3 py-2.5 text-[11px] text-muted-foreground">
          Ingest jobs ещё не стартовали для выбранного диапазона.
        </div>
      )}
    </div>
  );
}
