'use client';
/**
 * DatasetJobsPanel — compact list of currently-active and recently-finished
 * dataset jobs running on microservice_data. Subscribes to the global
 * {@link useDatasetJobs} store, which is populated from
 * EVT_DATA_DATASET_JOB_PROGRESS / EVT_DATA_DATASET_JOB_COMPLETED via SSE
 * plus a one-shot CMD_DATA_DATASET_JOBS_LIST hydration on page mount.
 */
import { useDatasetJobs, cancelJob, dismissJob } from '@/hooks/useDatasetJobs';

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

export default function DatasetJobsPanel(): JSX.Element | null {
  const jobs = useDatasetJobs();
  if (jobs.length === 0) return null;

  return (
    <div style={{
      border: '1px solid #ddd', borderRadius: 8, padding: 12, margin: '12px 0',
      background: '#fafafa',
    }}>
      <div style={{ fontWeight: 600, marginBottom: 8 }}>
        Активные задания ({jobs.length})
      </div>
      {jobs.map((j) => {
        const isRunning = j.status === 'running' || j.status === 'queued';
        const isError = j.status === 'failed';
        const barColor = isError ? '#d9534f'
          : j.status === 'succeeded' ? '#5cb85c'
          : j.status === 'canceled' ? '#999'
          : '#337ab7';
        const pct = Math.max(0, Math.min(100, j.progress ?? 0));
        return (
          <div key={j.job_id} style={{
            padding: '8px 0', borderTop: '1px solid #eee',
            display: 'flex', flexDirection: 'column', gap: 4,
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
              <span>
                <strong>{TYPE_LABEL[j.type] ?? j.type}</strong>
                {j.target_table ? ` · ${j.target_table}` : ''}
                {j.stage ? ` · ${j.stage}` : ''}
              </span>
              <span style={{ fontSize: 12, color: '#666' }}>
                {STATUS_LABEL[j.status] ?? j.status} · {pct}%
              </span>
            </div>
            <div style={{
              height: 6, background: '#e5e5e5', borderRadius: 3, overflow: 'hidden',
            }}>
              <div style={{
                height: '100%', width: `${pct}%`, background: barColor,
                transition: 'width 200ms linear',
              }} />
            </div>
            {j.detail ? (
              <div style={{ fontSize: 12, color: '#666' }}>{j.detail}</div>
            ) : null}
            {isError && j.error_message ? (
              <div style={{ fontSize: 12, color: '#d9534f' }}>
                {j.error_code ? `[${j.error_code}] ` : ''}{j.error_message}
              </div>
            ) : null}
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              {isRunning ? (
                <button
                  type="button"
                  onClick={() => { void cancelJob(j.job_id); }}
                  style={{ fontSize: 12, padding: '2px 8px' }}
                >Отменить</button>
              ) : null}
              {j.finished ? (
                <button
                  type="button"
                  onClick={() => dismissJob(j.job_id)}
                  style={{ fontSize: 12, padding: '2px 8px' }}
                >Скрыть</button>
              ) : null}
            </div>
          </div>
        );
      })}
    </div>
  );
}
