'use client';
/**
 * useDatasetJobs — process-local store for active and recently-finished
 * dataset jobs from microservice_data (Phase G of the dataset-jobs
 * redesign).
 *
 * Architecture:
 * - One module-level Map keyed by ``job_id`` is updated by
 *   {@link applyJobProgress} / {@link applyJobCompleted}.
 * - React components subscribe via {@link useDatasetJobs}, which uses
 *   {@link useSyncExternalStore} so the snapshot is referentially stable
 *   between unrelated updates.
 * - Initial state is hydrated by calling {@link refreshActiveJobs} on
 *   mount; that issues a single ``CMD_DATA_DATASET_JOBS_LIST`` Kafka
 *   request through the existing ``/api/kafka`` proxy.
 *
 * Inter-service rule: still strictly Kafka. The HTTP call is browser →
 * Admin Next.js API route only; the Admin route forwards to Kafka.
 */
import { useSyncExternalStore } from 'react';
import { Topics } from '@/lib/topics';
import { kafkaCall } from '@/lib/kafkaClient';
import type {
  DatasetJobProgressEvent,
  DatasetJobCompletedEvent,
  DatasetJobStatus,
  DatasetJobType,
} from '@/lib/types';

export interface DatasetJobView {
  job_id: string;
  type: DatasetJobType;
  status: DatasetJobStatus;
  progress: number;
  stage?: string | null;
  detail?: string | null;
  target_table?: string | null;
  error_code?: string | null;
  error_message?: string | null;
  // Local fields
  finished: boolean;
  last_update_ms: number;
}

type Listener = () => void;

const _jobs = new Map<string, DatasetJobView>();
const _listeners = new Set<Listener>();
let _snapshot: DatasetJobView[] = [];

function rebuildSnapshot(): void {
  _snapshot = Array.from(_jobs.values()).sort(
    (a, b) => b.last_update_ms - a.last_update_ms,
  );
  _listeners.forEach((l) => {
    try { l(); } catch { /* ignore */ }
  });
}

function subscribe(listener: Listener): () => void {
  _listeners.add(listener);
  return () => { _listeners.delete(listener); };
}

function getSnapshot(): DatasetJobView[] {
  return _snapshot;
}

const TERMINAL: ReadonlySet<DatasetJobStatus> = new Set([
  'succeeded', 'failed', 'canceled', 'skipped',
]);

/** Apply a progress event from EVT_DATA_DATASET_JOB_PROGRESS. */
export function applyJobProgress(e: DatasetJobProgressEvent): void {
  if (!e?.job_id) return;
  const prev = _jobs.get(e.job_id);
  _jobs.set(e.job_id, {
    job_id: e.job_id,
    type: e.type,
    status: e.status,
    progress: typeof e.progress === 'number' ? e.progress : (prev?.progress ?? 0),
    stage: e.stage ?? prev?.stage ?? null,
    detail: e.detail ?? prev?.detail ?? null,
    target_table: e.target_table ?? prev?.target_table ?? null,
    error_code: prev?.error_code ?? null,
    error_message: prev?.error_message ?? null,
    finished: TERMINAL.has(e.status),
    last_update_ms: Date.now(),
  });
  rebuildSnapshot();
}

/** Apply a completion event from EVT_DATA_DATASET_JOB_COMPLETED. */
export function applyJobCompleted(e: DatasetJobCompletedEvent): void {
  if (!e?.job_id) return;
  const prev = _jobs.get(e.job_id);
  _jobs.set(e.job_id, {
    job_id: e.job_id,
    type: e.type,
    status: e.status,
    progress: e.status === 'succeeded' ? 100 : (prev?.progress ?? 0),
    stage: prev?.stage ?? null,
    detail: prev?.detail ?? null,
    target_table: e.target_table ?? prev?.target_table ?? null,
    error_code: e.error_code ?? null,
    error_message: e.error_message ?? null,
    finished: true,
    last_update_ms: Date.now(),
  });
  rebuildSnapshot();
  // Auto-evict succeeded jobs after 30s to keep panel tidy. Keep failed
  // visible until explicitly dismissed.
  if (e.status === 'succeeded' || e.status === 'skipped') {
    setTimeout(() => {
      const cur = _jobs.get(e.job_id);
      if (cur && cur.finished) {
        _jobs.delete(e.job_id);
        rebuildSnapshot();
      }
    }, 30_000);
  }
}

/** Manually dismiss a finished job from the panel. */
export function dismissJob(jobId: string): void {
  if (_jobs.delete(jobId)) rebuildSnapshot();
}

/**
 * One-shot refresh: fetch active jobs from microservice_data and merge
 * them in. Called on Dataset/Download page mount so the user sees jobs
 * that are still running from a previous tab session.
 */
export async function refreshActiveJobs(): Promise<void> {
  try {
    const resp = await kafkaCall(
      Topics.CMD_DATA_DATASET_JOBS_LIST,
      { status_group: 'active', limit: 50 },
      { timeoutMs: 10_000 },
    ) as { jobs?: Array<DatasetJobProgressEvent & { error_code?: string; error_message?: string }> };
    if (!resp?.jobs) return;
    for (const j of resp.jobs) {
      const prev = _jobs.get(j.job_id);
      _jobs.set(j.job_id, {
        job_id: j.job_id,
        type: j.type,
        status: j.status,
        progress: typeof j.progress === 'number' ? j.progress : 0,
        stage: j.stage ?? null,
        detail: j.detail ?? null,
        target_table: j.target_table ?? null,
        error_code: j.error_code ?? null,
        error_message: j.error_message ?? null,
        finished: TERMINAL.has(j.status),
        last_update_ms: prev?.last_update_ms ?? Date.now(),
      });
    }
    rebuildSnapshot();
  } catch {
    // Best-effort hydration; SSE will still populate live updates.
  }
}

/** Cancel a job by id. Returns true if the cancel command was accepted. */
export async function cancelJob(jobId: string): Promise<boolean> {
  try {
    const resp = await kafkaCall(
      Topics.CMD_DATA_DATASET_JOBS_CANCEL,
      { job_id: jobId },
      { timeoutMs: 10_000 },
    ) as { ok?: boolean; error?: string };
    return Boolean(resp?.ok);
  } catch {
    return false;
  }
}

/** Subscribe React component to the live job list. */
export function useDatasetJobs(): DatasetJobView[] {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}
