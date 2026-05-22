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
  progress: number;          // overall job progress
  stage_progress?: number | null;
  stage?: string | null;
  detail?: string | null;
  target_table?: string | null;
  stage_total?: number | null;
  stage_completed?: number | null;
  stage_failed?: number | null;
  stage_skipped?: number | null;
  total?: number | null;
  error_code?: string | null;
  error_message?: string | null;
  completed?: number;         // rows written (populated from completed event)
  failed?: number | null;
  skipped?: number | null;
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
  overall_progress?: number;
  stage_progress?: number | null;
  stage?: string | null;
  detail?: string | null;
  target_table?: string | null;
  stage_total?: number | null;
  stage_completed?: number | null;
  stage_failed?: number | null;
  stage_skipped?: number | null;
  total?: number | null;
  error_code?: string | null;
  error_message?: string | null;
  completed?: number | null;
  failed?: number | null;
  skipped?: number | null;
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

const SHORT_LIVED_TERMINAL: ReadonlySet<DatasetJobStatus> = new Set(['failed', 'canceled']);
const LONG_LIVED_TERMINAL: ReadonlySet<DatasetJobStatus> = new Set(['succeeded', 'skipped']);
const TERMINAL_HISTORY_ENDPOINT = `${process.env.NEXT_PUBLIC_BASE_PATH ?? ''}/api/queue/history`;

function hasOwn<T extends object, K extends PropertyKey>(value: T, key: K): boolean {
  return Object.prototype.hasOwnProperty.call(value, key);
}

function scheduleFinishedJobCleanup(jobId: string, status: DatasetJobStatus): void {
  const delayMs = SHORT_LIVED_TERMINAL.has(status)
    ? 10_000
    : LONG_LIVED_TERMINAL.has(status)
      ? 30_000
      : null;
  if (delayMs === null) return;

  setTimeout(() => {
    const cur = _jobs.get(jobId);
    if (cur && cur.finished && cur.status === status) {
      _jobs.delete(jobId);
      rebuildSnapshot();
    }
  }, delayMs);
}

async function recordTerminalJobHistory(job: DatasetJobView): Promise<void> {
  try {
    const response = await fetch(TERMINAL_HISTORY_ENDPOINT, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        id: `dataset-job:${job.job_id}`,
        ts: new Date(job.last_update_ms).toISOString(),
        topic: Topics.EVT_DATA_DATASET_JOB_COMPLETED,
        level: job.status === 'failed' || job.status === 'canceled' ? 'error' : 'success',
        durationMs: 0,
        payloadSummary: {
          job_id: job.job_id,
          type: job.type,
          target_table: job.target_table,
        },
        responseSummary: {
          status: job.status,
          completed: job.completed ?? 0,
        },
        message: job.status === 'failed' || job.status === 'canceled'
          ? (job.error_message ?? job.detail ?? 'Dataset job failed')
          : (job.detail ?? 'Dataset job finished'),
        code: job.error_code,
        detail: job.error_message ?? job.detail,
        correlationId: job.job_id,
      }),
    });
    if (!response.ok) {
      throw new Error(`queue history write failed: ${response.status}`);
    }
  } catch {
    // Queue history enrichment is best-effort only.
  }
}

function mergeJobWire(job: DatasetJobWire, deferRebuild = false): void {
  if (!job?.job_id) return;

  const prev = _jobs.get(job.job_id);
  const finished = TERMINAL.has(job.status);
  const overallProgress = typeof job.overall_progress === 'number'
    ? job.overall_progress
    : typeof job.progress === 'number'
      ? job.progress
      : job.status === 'succeeded'
        ? 100
        : (prev?.progress ?? 0);
  const stageProgress = hasOwn(job, 'stage_progress')
    ? (typeof job.stage_progress === 'number'
      ? job.stage_progress
      : job.status === 'succeeded'
        ? 100
        : null)
    : (prev?.stage_progress ?? null);

  _jobs.set(job.job_id, {
    job_id: job.job_id,
    type: job.type,
    status: job.status,
    progress: overallProgress,
    stage_progress: stageProgress,
    stage: hasOwn(job, 'stage') ? (job.stage ?? null) : (prev?.stage ?? null),
    detail: hasOwn(job, 'detail') ? (job.detail ?? null) : (prev?.detail ?? null),
    target_table: hasOwn(job, 'target_table') ? (job.target_table ?? null) : (prev?.target_table ?? null),
    stage_total: hasOwn(job, 'stage_total')
      ? (typeof job.stage_total === 'number' ? job.stage_total : null)
      : (prev?.stage_total ?? null),
    stage_completed: hasOwn(job, 'stage_completed')
      ? (typeof job.stage_completed === 'number' ? job.stage_completed : null)
      : (prev?.stage_completed ?? null),
    stage_failed: hasOwn(job, 'stage_failed')
      ? (typeof job.stage_failed === 'number' ? job.stage_failed : null)
      : (prev?.stage_failed ?? null),
    stage_skipped: hasOwn(job, 'stage_skipped')
      ? (typeof job.stage_skipped === 'number' ? job.stage_skipped : null)
      : (prev?.stage_skipped ?? null),
    total: hasOwn(job, 'total')
      ? (typeof job.total === 'number' ? job.total : null)
      : (prev?.total ?? null),
    error_code: hasOwn(job, 'error_code') ? (job.error_code ?? null) : (prev?.error_code ?? null),
    error_message: hasOwn(job, 'error_message') ? (job.error_message ?? null) : (prev?.error_message ?? null),
    completed: hasOwn(job, 'completed')
      ? (typeof job.completed === 'number' ? job.completed : undefined)
      : prev?.completed,
    failed: hasOwn(job, 'failed')
      ? (typeof job.failed === 'number' ? job.failed : null)
      : (prev?.failed ?? null),
    skipped: hasOwn(job, 'skipped')
      ? (typeof job.skipped === 'number' ? job.skipped : null)
      : (prev?.skipped ?? null),
    finished,
    last_update_ms:
      typeof job.finished_at_ms === 'number' ? job.finished_at_ms :
      typeof job.updated_at_ms === 'number' ? job.updated_at_ms :
      Date.now(),
  });

  if (!prev?.finished && finished) {
    scheduleFinishedJobCleanup(job.job_id, job.status);
    void recordTerminalJobHistory(_jobs.get(job.job_id)!);
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
    overall_progress: e.overall_progress,
    stage_progress: e.stage_progress,
    stage: e.stage,
    detail: e.detail,
    stage_total: e.stage_total,
    stage_completed: e.stage_completed,
    stage_failed: e.stage_failed,
    stage_skipped: e.stage_skipped,
    target_table: e.target_table,
    total: e.total,
    completed: e.completed,
    failed: e.failed,
    skipped: e.skipped,
  });
}

/** Apply a completion event from EVT_DATA_DATASET_JOB_COMPLETED. */
export function applyJobCompleted(e: DatasetJobCompletedEvent): void {
  mergeJobWire({
    job_id: e.job_id,
    type: e.type,
    status: e.status,
    progress: e.progress,
    overall_progress: e.overall_progress,
    stage_progress: e.stage_progress,
    stage: e.stage,
    detail: e.detail,
    stage_total: e.stage_total,
    stage_completed: e.stage_completed,
    stage_failed: e.stage_failed,
    stage_skipped: e.stage_skipped,
    target_table: e.target_table,
    total: e.total,
    error_code: e.error_code,
    error_message: e.error_message,
    completed: e.completed,
    failed: e.failed,
    skipped: e.skipped,
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
