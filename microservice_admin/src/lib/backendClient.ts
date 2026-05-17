/**
 * server-only — never import from client bundles.
 *
 * HTTP client for the gateway admin facade (split-deployment mode).
 *
 * When ADMIN_BACKEND_BASE_URL is set, every kafkaCall() is routed through
 * this client to the gateway's /api/admin/* endpoints instead of Kafka
 * directly.  The shared secret is sent in Authorization: Bearer <token>.
 *
 * Topic → HTTP path mapping mirrors AdminController.cs routes exactly.
 */

import { Topics } from './topics';

// ── Config ────────────────────────────────────────────────────────────────────

/** Backend base URL, e.g. https://backend-host:8443  (no trailing slash) */
export const ADMIN_BACKEND_BASE_URL =
  (process.env.ADMIN_BACKEND_BASE_URL ?? '').replace(/\/$/, '');

export const ADMIN_BACKEND_SHARED_TOKEN =
  process.env.ADMIN_BACKEND_SHARED_TOKEN ?? '';

/** True when the admin should use the HTTP facade instead of direct Kafka. */
export const isSplitMode = ADMIN_BACKEND_BASE_URL.length > 0;

// ── Topic → path mapping ──────────────────────────────────────────────────────

/**
 * Maps every Kafka topic constant to its /api/admin/* HTTP path.
 * All paths are POST; the gateway AdminController accepts `JsonElement?` body.
 */
const TOPIC_PATH: Readonly<Record<string, string>> = {
  // Health
  [Topics.CMD_DATA_HEALTH]:       'health/data',
  [Topics.CMD_ANALYTICS_HEALTH]:  'health/analytics',

  // Dataset
  [Topics.CMD_DATA_DATASET_LIST_TABLES]:      'dataset/list-tables',
  [Topics.CMD_DATA_DATASET_COVERAGE]:         'dataset/coverage',
  [Topics.CMD_DATA_DATASET_ROWS]:             'dataset/rows',
  [Topics.CMD_DATA_DATASET_EXPORT]:           'dataset/export',
  [Topics.CMD_DATA_DATASET_INGEST]:           'dataset/ingest',
  [Topics.CMD_DATA_DATASET_NORMALIZE_TF]:     'dataset/normalize-timeframe',
  [Topics.CMD_DATA_DATASET_MAKE_TABLE]:       'dataset/make-table-name',
  [Topics.CMD_DATA_DATASET_INSTRUMENT]:       'dataset/instrument-details',
  [Topics.CMD_DATA_DATASET_SCHEMA]:           'dataset/schema',
  [Topics.CMD_DATA_DATASET_MISSING]:          'dataset/find-missing',
  [Topics.CMD_DATA_DATASET_TIMESTAMPS]:       'dataset/timestamps',
  [Topics.CMD_DATA_DATASET_CONSTANTS]:        'dataset/constants',
  [Topics.CMD_DATA_DATASET_DELETE_ROWS]:      'dataset/delete-rows',
  [Topics.CMD_DATA_DATASET_IMPORT_CSV]:       'dataset/import-csv',
  [Topics.CMD_DATA_DATASET_UPSERT_OHLCV]:     'dataset/upsert-ohlcv',

  // Anomaly / inspection
  [Topics.CMD_DATA_DATASET_COLUMN_STATS]:      'dataset/column-stats',
  [Topics.CMD_DATA_DATASET_COLUMN_HISTOGRAM]:  'dataset/column-histogram',
  [Topics.CMD_DATA_DATASET_BROWSE]:            'dataset/browse',
  [Topics.CMD_DATA_DATASET_COMPUTE_FEATURES]:  'dataset/compute-features',
  [Topics.CMD_DATA_DATASET_DETECT_ANOMALIES]:  'dataset/detect-anomalies',
  [Topics.CMD_DATA_DATASET_CLEAN_PREVIEW]:     'dataset/clean-preview',
  [Topics.CMD_DATA_DATASET_CLEAN_APPLY]:       'dataset/clean-apply',
  [Topics.CMD_DATA_DATASET_AUDIT_LOG]:         'dataset/audit-log',

  // Background jobs
  [Topics.CMD_DATA_DATASET_JOBS_START]:   'dataset/jobs/start',
  [Topics.CMD_DATA_DATASET_JOBS_CANCEL]:  'dataset/jobs/cancel',
  [Topics.CMD_DATA_DATASET_JOBS_GET]:     'dataset/jobs/get',
  [Topics.CMD_DATA_DATASET_JOBS_LIST]:    'dataset/jobs/list',

  // DB
  [Topics.CMD_DATA_DB_PING]:  'dataset/db-ping',

  // Analitic
  [Topics.CMD_ANALITIC_DATASET_LOAD]:               'analytic/dataset/load',
  [Topics.CMD_ANALITIC_DATASET_UNLOAD]:             'analytic/dataset/unload',
  [Topics.CMD_ANALITIC_DATASET_STATUS]:             'analytic/dataset/status',
  [Topics.CMD_ANALITIC_ANOMALY_DBSCAN]:             'analytic/anomaly/dbscan',
  [Topics.CMD_ANALITIC_ANOMALY_ISOLATION_FOREST]:   'analytic/anomaly/isolation-forest',
  [Topics.CMD_ANALITIC_DATASET_DISTRIBUTION]:       'analytic/dataset/distribution',
  [Topics.CMD_ANALITIC_DATASET_QUALITY_CHECK]:      'analytic/dataset/quality-check',
  [Topics.CMD_ANALITIC_DATASET_LOAD_OHLCV]:         'analytic/dataset/load-ohlcv',
  [Topics.CMD_ANALITIC_DATASET_RECOMPUTE_FEATURES]: 'analytic/dataset/recompute-features',

  // Analytics
  [Topics.CMD_ANALYTICS_TRAIN_START]:   'analytics/train/start',
  [Topics.CMD_ANALYTICS_TRAIN_STATUS]:  'analytics/train/status',
  [Topics.CMD_ANALYTICS_MODEL_LIST]:    'analytics/model/list',
  [Topics.CMD_ANALYTICS_MODEL_LOAD]:    'analytics/model/load',
  [Topics.CMD_ANALYTICS_PREDICT]:       'analytics/predict',
};

// ── Core call ─────────────────────────────────────────────────────────────────

export class BackendClientError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = 'BackendClientError';
  }
}

/**
 * Calls the gateway admin facade for the given Kafka topic.
 * Returns the parsed `data` field (mirrors kafkaRequest() return shape).
 *
 * @throws BackendClientError on HTTP 4xx/5xx
 * @throws Error on network / JSON failures
 */
export async function backendCall(
  topic: string,
  payload: Record<string, unknown> | null = null,
  options?: { timeoutMs?: number; signal?: AbortSignal },
): Promise<Record<string, unknown>> {
  const path = TOPIC_PATH[topic];
  if (!path) {
    throw new Error(
      `backendClient: no HTTP path mapped for topic "${topic}". ` +
      `Add it to TOPIC_PATH in backendClient.ts.`,
    );
  }

  const url = `${ADMIN_BACKEND_BASE_URL}/api/admin/${path}`;

  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  };
  if (ADMIN_BACKEND_SHARED_TOKEN) {
    headers['Authorization'] = `Bearer ${ADMIN_BACKEND_SHARED_TOKEN}`;
  }

  const timeoutMs = options?.timeoutMs ?? 30_000;
  const localAbort = AbortSignal.timeout(timeoutMs);
  const signal = options?.signal
    ? AbortSignal.any([options.signal, localAbort])
    : localAbort;

  const res = await fetch(url, {
    method: 'POST',
    headers,
    body: JSON.stringify(payload ?? {}),
    signal,
  });

  if (!res.ok) {
    let detail = '';
    try { detail = await res.text(); } catch { /* ignore */ }
    throw new BackendClientError(
      res.status,
      `Admin facade ${path} returned HTTP ${res.status}: ${detail}`,
    );
  }

  // Gateway wraps responses as plain JSON (JsonElement from .RequestAsync).
  // We return it as-is to match the shape kafkaRequest() callers expect.
  const data = (await res.json()) as Record<string, unknown>;
  return data;
}
