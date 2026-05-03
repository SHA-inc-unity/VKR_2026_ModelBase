'use client';
/**
 * DatasetJobsPanel — compact list of currently-active and recently-finished
 * dataset jobs running on microservice_data. Subscribes to the global
 * {@link useDatasetJobs} store, which is populated from
 * EVT_DATA_DATASET_JOB_PROGRESS / EVT_DATA_DATASET_JOB_COMPLETED via SSE
 * plus a one-shot CMD_DATA_DATASET_JOBS_LIST hydration on page mount.
 */
import { useDatasetJobs, cancelJob, dismissJob, type DatasetJobView } from '@/hooks/useDatasetJobs';

const STATUS_LABEL: Record<string, string> = {
  queued:    'в очереди',
  running:   'выполняется',
  succeeded: 'готово',
  failed:    'ошибка',
  canceled:  'отменено',
  skipped:   'пропущено',
};

const TYPE_LABEL: Record<string, string> = {
  ingest:           'Ingest',
  detect_anomalies: 'Anomalies',
  compute_features: 'Features',
  clean_apply:      'Clean',
  export:           'Export',
  import_csv:       'Import CSV',
  upsert_ohlcv:     'Upsert OHLCV',
};

const STATUS_BADGE_CLASS: Record<string, string> = {
  queued:    'border-amber-400/20 bg-amber-500/10 text-amber-200',
  running:   'border-sky-400/20 bg-sky-500/10 text-sky-200',
  succeeded: 'border-emerald-400/20 bg-emerald-500/10 text-emerald-200',
  failed:    'border-destructive/25 bg-destructive/10 text-destructive',
  canceled:  'border-border bg-muted/40 text-muted-foreground',
  skipped:   'border-border bg-muted/40 text-muted-foreground',
};

const STATUS_BAR_CLASS: Record<string, string> = {
  queued:    'bg-amber-300/75',
  running:   'bg-sky-300/80',
  succeeded: 'bg-emerald-300/80',
  failed:    'bg-destructive',
  canceled:  'bg-muted-foreground/60',
  skipped:   'bg-muted-foreground/60',
};

function getJobNote(job: DatasetJobView): string | null {
  if (job.status === 'queued') return 'Ожидает планировщика';
  if (job.status === 'running') return job.detail ?? 'Job выполняется во владельце сервиса';
  if (job.status === 'succeeded' && job.type === 'ingest') {
    const completedRows = job.completed ?? 0;
    return completedRows > 0
      ? `${completedRows.toLocaleString()} новых строк`
      : 'Новых строк не потребовалось';
  }
  return job.detail ?? null;
}

export default function DatasetJobsPanel(): JSX.Element | null {
  const jobs = useDatasetJobs();
  if (jobs.length === 0) return null;

  return (
    <section className="rounded-xl border border-border bg-card/95 px-4 py-3 shadow-sm">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div>
          <div className="text-sm font-semibold text-foreground">Активные dataset jobs</div>
          <div className="text-[11px] text-muted-foreground">{jobs.length} job(s) в live store admin-панели</div>
        </div>
        <div className="rounded-full border border-border bg-muted/40 px-2.5 py-1 text-[11px] font-medium text-muted-foreground">
          {jobs.length}
        </div>
      </div>

      <div className="space-y-2">
      {jobs.map((j, index) => {
        const isRunning = j.status === 'running' || j.status === 'queued';
        const isError = j.status === 'failed';
        const pct = Math.max(0, Math.min(100, j.progress ?? 0));
        const badgeClass = STATUS_BADGE_CLASS[j.status] ?? STATUS_BADGE_CLASS.running;
        const barClass = STATUS_BAR_CLASS[j.status] ?? STATUS_BAR_CLASS.running;
        const note = getJobNote(j);
        return (
          <div
            key={j.job_id}
            className="rounded-lg border border-border/70 bg-muted/20 px-3 py-2.5"
          >
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0 flex-1">
                <div className="flex min-w-0 items-center gap-2">
                  <span className="shrink-0 text-sm font-semibold text-foreground">{TYPE_LABEL[j.type] ?? j.type}</span>
                  {j.target_table ? (
                    <span className="truncate font-mono text-[11px] text-muted-foreground">{j.target_table}</span>
                  ) : null}
                </div>
                <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
                  <span className={`rounded-full border px-2 py-0.5 font-medium ${badgeClass}`}>
                    {STATUS_LABEL[j.status] ?? j.status}
                  </span>
                  {j.stage ? <span className="truncate">{j.stage}</span> : null}
                  <span className="tabular-nums">{pct}%</span>
                  <span className="font-mono">{j.job_id.slice(0, 8)}</span>
                </div>
              </div>
              <div className="shrink-0 text-[11px] text-muted-foreground">#{index + 1}</div>
            </div>

            <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted/60">
              <div
                className={`h-full transition-[width] duration-200 ease-linear ${barClass}`}
                style={{ width: `${pct}%` }}
              />
            </div>

            {note ? (
              <div className="mt-2 text-[11px] text-muted-foreground">{note}</div>
            ) : null}

            {isError && j.error_message ? (
              <div className="mt-1 text-[11px] text-destructive">
                {j.error_code ? `[${j.error_code}] ` : ''}{j.error_message}
              </div>
            ) : null}

            <div className="mt-3 flex gap-2 justify-end">
              {isRunning ? (
                <button
                  type="button"
                  onClick={() => { void cancelJob(j.job_id); }}
                  className="rounded-md border border-destructive/25 bg-destructive/10 px-2.5 py-1 text-[11px] font-medium text-destructive transition-colors hover:bg-destructive/15"
                >Отменить</button>
              ) : null}
              {j.finished ? (
                <button
                  type="button"
                  onClick={() => dismissJob(j.job_id)}
                  className="rounded-md border border-border bg-muted/40 px-2.5 py-1 text-[11px] font-medium text-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
                >Скрыть</button>
              ) : null}
            </div>
          </div>
        );
      })}
      </div>
    </section>
  );
}
