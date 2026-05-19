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
  completed?: number;         // rows written (populated from completed event)
  // Local fields
  finished: boolean;
  last_update_ms: number;
}

type Listener = () => void;

type DatasetJobWire = {
  job_id: string;
  type: DatasetJobType;
  status: DatasetJobStatus;
  progress?: number;
  stage?: string | null;
  detail?: string | null;
  target_table?: string | null;
  error_code?: string | null;
  error_message?: string | null;
  completed?: number | null;
  updated_at_ms?: number | null;
  finished_at_ms?: number | null;
};

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

function scheduleFinishedJobCleanup(jobId: string, status: DatasetJobStatus): void {
  if (status !== 'succeeded' && status !== 'skipped') return;
  setTimeout(() => {
    const cur = _jobs.get(jobId);
    if (cur && cur.finished && (cur.status === 'succeeded' || cur.status === 'skipped')) {
      _jobs.delete(jobId);
      rebuildSnapshot();
    }
  }, 30_000);
}

function mergeJobWire(job: DatasetJobWire, deferRebuild = false): void {
  if (!job?.job_id) return;

  const prev = _jobs.get(job.job_id);
  const finished = TERMINAL.has(job.status);
  _jobs.set(job.job_id, {
    job_id: job.job_id,
    type: job.type,
    status: job.status,
    progress: typeof job.progress === 'number'
      ? job.progress
      : job.status === 'succeeded'
        ? 100
        : (prev?.progress ?? 0),
    stage: job.stage ?? prev?.stage ?? null,
    detail: job.detail ?? prev?.detail ?? null,
    target_table: job.target_table ?? prev?.target_table ?? null,
    error_code: job.error_code ?? prev?.error_code ?? null,
    error_message: job.error_message ?? prev?.error_message ?? null,
    completed: typeof job.completed === 'number' ? job.completed : prev?.completed,
    finished,
    last_update_ms:
      typeof job.finished_at_ms === 'number' ? job.finished_at_ms :
      typeof job.updated_at_ms === 'number' ? job.updated_at_ms :
      Date.now(),
  });

  if (!prev?.finished && finished) {
    scheduleFinishedJobCleanup(job.job_id, job.status);
  }

  if (!deferRebuild) rebuildSnapshot();
}

async function fetchJobsByIds(jobIds: string[], deferRebuild = false): Promise<boolean> {
  const uniqueIds = Array.from(new Set(jobIds.filter(Boolean)));
  if (uniqueIds.length === 0) return false;

  let changed = false;
  await Promise.all(uniqueIds.map(async (jobId) => {
    try {
      const resp = await kafkaCall(
        Topics.CMD_DATA_DATASET_JOBS_GET,
        { job_id: jobId },
        { timeoutMs: 10_000 },
      ) as { job?: DatasetJobWire };
      if (!resp?.job?.job_id) return;
      mergeJobWire(resp.job, true);
      changed = true;
    } catch {
      // Best-effort refresh only.
    }
  }));

  if (changed && !deferRebuild) rebuildSnapshot();
  return changed;
}

/** Apply a progress event from EVT_DATA_DATASET_JOB_PROGRESS. */
export function applyJobProgress(e: DatasetJobProgressEvent): void {
  mergeJobWire({
    job_id: e.job_id,
    type: e.type,
    status: e.status,
    progress: e.progress,
    stage: e.stage,
    detail: e.detail,
    target_table: e.target_table,
  });
}

/** Apply a completion event from EVT_DATA_DATASET_JOB_COMPLETED. */
export function applyJobCompleted(e: DatasetJobCompletedEvent): void {
  mergeJobWire({
    job_id: e.job_id,
    type: e.type,
    status: e.status,
    target_table: e.target_table,
    error_code: e.error_code,
    error_message: e.error_message,
    completed: e.completed,
    finished_at_ms: typeof e.finished_at === 'string' ? Date.parse(e.finished_at) : null,
  });
}

/** Manually dismiss a finished job from the panel. */
export function dismissJob(jobId: string): void {
  if (_jobs.delete(jobId)) rebuildSnapshot();
}

/**
 * Seed the local store with a freshly-created queued job.
 *
 * Called immediately after JOBS_START returns a job_id so the UI can
 * honestly distinguish "queued, scheduler not yet picked up" from
 * "running" without waiting for the first SSE progress event. The
 * record is later overwritten by {@link applyJobProgress} as soon as
 * the scheduler dispatches the job.
 */
export function seedQueuedJob(args: {
  jobId: string;
  type: DatasetJobType;
  target_table?: string | null;
}): void {
  const { jobId, type, target_table = null } = args;
  if (!jobId) return;
  if (_jobs.has(jobId)) return; // don't downgrade an already-running job
  _jobs.set(jobId, {
    job_id: jobId,
    type,
    status: 'queued',
    progress: 0,
    stage: null,
    detail: null,
    target_table,
    error_code: null,
    error_message: null,
    finished: false,
    last_update_ms: Date.now(),
  });
  rebuildSnapshot();
}

/**
 * One-shot refresh: fetch active jobs from microservice_data and merge
 * them in. Called on Dataset/Download page mount so the user sees jobs
 * that are still running from a previous tab session.
 */
export async function refreshActiveJobs(): Promise<void> {
  try {
    const localActiveIds = Array.from(_jobs.values())
      .filter(job => !job.finished)
      .map(job => job.job_id);
    const resp = await kafkaCall(
      Topics.CMD_DATA_DATASET_JOBS_LIST,
      { status_group: 'active', limit: 50 },
      { timeoutMs: 10_000 },
    ) as { jobs?: DatasetJobWire[] };
    const activeJobs = resp?.jobs ?? [];
    const activeIds = new Set(activeJobs.map(job => job.job_id));

    for (const j of activeJobs) {
      mergeJobWire(j, true);
    }

    // If a locally-running job disappeared from the active list and we missed
    // its completed SSE event, reconcile it through JOBS_GET so the UI does
    // not stay stuck in a stale running/queued state until page reload.
    const disappearedIds = localActiveIds.filter(jobId => !activeIds.has(jobId));
    if (disappearedIds.length > 0) {
      await fetchJobsByIds(disappearedIds, true);
    }

    rebuildSnapshot();
  } catch {
    // Best-effort hydration; SSE will still populate live updates.
  }
}

/**
 * Poll explicit job ids through JOBS_GET. Used as a fallback when the page
 * tracks long-running ingest jobs and SSE misses a queued/running/terminal
 * transition.
 */
export async function refreshJobsByIds(jobIds: string[]): Promise<void> {
  await fetchJobsByIds(jobIds);
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
