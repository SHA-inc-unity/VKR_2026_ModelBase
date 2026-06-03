/**
 * server-only — never import from client bundles.
 *
 * HTTP client for the gateway admin facade (split-deployment mode).
 *
 * When ADMIN_BACKEND_BASE_URL is set, every kafkaCall() is routed through
 * this client to the gateway's /api/admin/* endpoints instead of Kafka
 * directly. The logged-in admin user's Account Service JWT is forwarded in
 * Authorization: Bearer <token>.
 *
 * Topic → HTTP path mapping mirrors AdminController.cs routes exactly.
 */

import { Topics } from './topics';
import { randomUUID } from 'crypto';
import { Agent as HttpsAgent, request as httpsRequest } from 'https';
import { writeAdminRuntimeLog } from './adminRuntimeLog';

// ── Config ────────────────────────────────────────────────────────────────────

/** Backend base URL, e.g. https://backend-host:8443  (no trailing slash) */
export const ADMIN_BACKEND_BASE_URL =
  (process.env.ADMIN_BACKEND_BASE_URL ?? '').replace(/\/$/, '');

export const ADMIN_BACKEND_TLS_INSECURE =
  /^(1|true|yes|on)$/i.test(process.env.ADMIN_BACKEND_TLS_INSECURE ?? '');

/** True when the admin should use the HTTP facade instead of direct Kafka. */
export const isSplitMode = ADMIN_BACKEND_BASE_URL.length > 0;

/**
 * Whether the backend-facade request(s) should skip TLS certificate
 * verification. This stays SCOPED to the backend call below via a dedicated
 * https.Agent — we must never disable TLS verification process-wide (that
 * would weaken every other outbound TLS connection in this Node process).
 */
const useInsecureBackendTls =
  isSplitMode &&
  ADMIN_BACKEND_TLS_INSECURE &&
  ADMIN_BACKEND_BASE_URL.startsWith('https://');

/**
 * Per-request agent that accepts the backend's self-signed certificate.
 * Created lazily and reused across calls; scoped to backendCall() only.
 */
let insecureBackendAgent: HttpsAgent | undefined;
function getInsecureBackendAgent(): HttpsAgent {
  if (!insecureBackendAgent) {
    insecureBackendAgent = new HttpsAgent({ rejectUnauthorized: false });
  }
  return insecureBackendAgent;
}

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
  [Topics.CMD_DATA_DATASET_SERIES]:            'dataset/series',
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

  // Dedicated market watcher
  [Topics.CMD_DATA_MARKET_WATCHER_STATUS]:       'market-watcher/status',
  [Topics.CMD_DATA_MARKET_WATCHER_SET_ENABLED]:  'market-watcher/set-enabled',
  [Topics.CMD_DATA_MARKET_WATCHER_ROWS]:         'market-watcher/rows',
  [Topics.CMD_DATA_MARKET_WATCHER_LOGS]:         'market-watcher/logs',

  // Currency pairs center (single source of truth)
  [Topics.CMD_DATA_PAIRS_LIST]:       'pairs/list',
  [Topics.CMD_DATA_PAIRS_ADD]:        'pairs/add',
  [Topics.CMD_DATA_PAIRS_REMOVE]:     'pairs/remove',
  [Topics.CMD_DATA_PAIRS_SET_ACTIVE]: 'pairs/set-active',

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

/**
 * Performs the backend-facade POST.
 *
 * In the normal case this is just the global `fetch`. When the operator has
 * opted into the self-signed split-deploy mode (ADMIN_BACKEND_TLS_INSECURE),
 * the request is sent through node:https with a dedicated, cert-skipping
 * Agent so that the relaxed TLS policy is confined to this single backend
 * call — the rest of the process keeps full certificate verification.
 *
 * The returned value is a standard `Response`, so the caller can treat both
 * paths identically (`ok`/`status`/`headers.get`/`text`/`json`). Network/abort
 * failures are surfaced with the same `name`/`cause.code` shape that global
 * fetch produces, so the existing error handling keeps working.
 */
function backendFetch(
  url: string,
  init: { method: string; headers: Record<string, string>; body: string; signal: AbortSignal },
): Promise<Response> {
  if (!useInsecureBackendTls || !url.startsWith('https://')) {
    return fetch(url, init);
  }

  return new Promise<Response>((resolve, reject) => {
    const req = httpsRequest(
      url,
      {
        method: init.method,
        headers: init.headers,
        agent: getInsecureBackendAgent(),
        signal: init.signal,
      },
      (res) => {
        const chunks: Buffer[] = [];
        res.on('data', (chunk) => chunks.push(chunk as Buffer));
        res.on('end', () => {
          const headers = new Headers();
          for (const [key, value] of Object.entries(res.headers)) {
            if (Array.isArray(value)) {
              for (const v of value) headers.append(key, v);
            } else if (value !== undefined) {
              headers.set(key, value);
            }
          }
          resolve(
            new Response(chunks.length ? Buffer.concat(chunks) : null, {
              status: res.statusCode ?? 502,
              statusText: res.statusMessage ?? '',
              headers,
            }),
          );
        });
        res.on('error', reject);
      },
    );
    // Mirror undici/fetch error shape: expose the system code under `.cause`.
    req.on('error', (err: NodeJS.ErrnoException) => {
      if (err.name === 'AbortError') {
        reject(err);
        return;
      }
      const wrapped = new TypeError(`fetch failed`);
      (wrapped as Error & { cause?: unknown }).cause = err;
      reject(wrapped);
    });
    req.end(init.body);
  });
}

// ── Core call ─────────────────────────────────────────────────────────────────

export class BackendClientError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly code?: string,
    public readonly detail?: string,
    public readonly correlationId?: string,
  ) {
    super(message);
    this.name = 'BackendClientError';
  }
}

type GatewayErrorBody = {
  status?: number;
  title?: string;
  code?: string;
  detail?: string;
  correlationId?: string;
  error?: string;
};

type FetchErrorCause = {
  code?: string;
  message?: string;
};

function fetchErrorDetail(err: unknown): { detail: string; causeCode?: string; isUntrustedTls: boolean } {
  const message = err instanceof Error ? err.message : String(err);
  const cause = err instanceof Error && 'cause' in err
    ? (err as Error & { cause?: FetchErrorCause }).cause
    : undefined;
  const causeCode = typeof cause?.code === 'string' ? cause.code : undefined;
  const causeMessage = typeof cause?.message === 'string' ? cause.message : undefined;
  const detail = causeCode
    ? `${message} (${causeCode}${causeMessage ? `: ${causeMessage}` : ''})`
    : message;
  const isUntrustedTls = causeCode !== undefined && [
    'DEPTH_ZERO_SELF_SIGNED_CERT',
    'SELF_SIGNED_CERT_IN_CHAIN',
    'UNABLE_TO_VERIFY_LEAF_SIGNATURE',
    'UNABLE_TO_GET_ISSUER_CERT_LOCALLY',
    'CERT_HAS_EXPIRED',
  ].includes(causeCode);
  return { detail, causeCode, isUntrustedTls };
}

function summarizeBackendError(
  status: number,
  path: string,
  body: GatewayErrorBody | null,
  raw: string,
  fallbackCorrelationId?: string,
): string {
  const code = body?.code;
  const detail = body?.detail || body?.error || raw;
  const effectiveCorrelationId = body?.correlationId ?? fallbackCorrelationId;
  const correlation = effectiveCorrelationId ? ` correlationId=${effectiveCorrelationId}` : '';

  if (status === 401) {
    return `Admin facade rejected ${path}: admin session is missing, expired, or not an admin account.${detail ? ` ${detail}` : ''}${correlation}`;
  }

  if (status === 502) {
    return `Admin facade ${path} returned 502 Bad Gateway: nginx could not reach gateway-service:5020.${correlation}`;
  }

  if (status === 504) {
    return `Admin facade ${path} timed out waiting for downstream Kafka/service response.${correlation}`;
  }

  return `Admin facade ${path} returned HTTP ${status}${code ? ` (${code})` : ''}: ${detail || 'no response body'}${correlation}`;
}

/**
 * Calls the gateway admin facade for the given Kafka topic.
 * Returns the parsed `data` field (mirrors kafkaRequest() return shape).
 *
 * @throws BackendClientError on HTTP, auth, timeout and network failures
 */
export async function backendCall(
  topic: string,
  payload: Record<string, unknown> | null = null,
  options?: { timeoutMs?: number; signal?: AbortSignal; accessToken?: string },
): Promise<Record<string, unknown>> {
  const path = TOPIC_PATH[topic];
  if (!path) {
    throw new Error(
      `backendClient: no HTTP path mapped for topic "${topic}". ` +
      `Add it to TOPIC_PATH in backendClient.ts.`,
    );
  }

  const url = `${ADMIN_BACKEND_BASE_URL}/api/admin/${path}`;
  const correlationId = randomUUID().replace(/-/g, '');
  const startedAt = Date.now();

  console.info('[admin-backend] request:start', {
    topic,
    path,
    baseUrl: ADMIN_BACKEND_BASE_URL || '(empty)',
    timeoutMs: options?.timeoutMs ?? 30_000,
    tlsInsecure: ADMIN_BACKEND_TLS_INSECURE,
    adminSessionConfigured: Boolean(options?.accessToken),
    correlationId,
    payloadKeys: payload ? Object.keys(payload) : [],
  });
  writeAdminRuntimeLog({
    level: 'info',
    source: 'admin-backend',
    event: 'request:start',
    fields: {
      topic,
      path,
      baseUrl: ADMIN_BACKEND_BASE_URL || '(empty)',
      timeoutMs: options?.timeoutMs ?? 30_000,
      tlsInsecure: ADMIN_BACKEND_TLS_INSECURE,
      adminSessionConfigured: Boolean(options?.accessToken),
      correlationId,
      payloadKeys: payload ? Object.keys(payload) : [],
    },
  });

  if (!options?.accessToken) {
    console.warn('[admin-backend] request:missing-admin-session', {
      topic,
      path,
      correlationId,
    });
    writeAdminRuntimeLog({
      level: 'error',
      source: 'admin-backend',
      event: 'request:missing-admin-session',
      fields: { topic, path, correlationId },
    });
    throw new BackendClientError(
      401,
      `Admin session is missing on admin-host; sign in with an admin account. correlationId=${correlationId}`,
      'admin_session_required',
      undefined,
      correlationId,
    );
  }

  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    'X-Correlation-Id': correlationId,
  };
  headers['Authorization'] = `Bearer ${options.accessToken}`;

  const timeoutMs = options?.timeoutMs ?? 30_000;
  const localAbort = AbortSignal.timeout(timeoutMs);
  const signal = options?.signal
    ? AbortSignal.any([options.signal, localAbort])
    : localAbort;

  let res: Response;
  try {
    res = await backendFetch(url, {
      method: 'POST',
      headers,
      body: JSON.stringify(payload ?? {}),
      signal,
    });
  } catch (err) {
    if (err instanceof Error && /AbortError|TimeoutError/i.test(err.name)) {
      console.warn('[admin-backend] request:timeout', {
        topic,
        path,
        timeoutMs,
        durationMs: Date.now() - startedAt,
        correlationId,
      });
      writeAdminRuntimeLog({
        level: 'error',
        source: 'admin-backend',
        event: 'request:timeout',
        fields: { topic, path, timeoutMs, durationMs: Date.now() - startedAt, correlationId },
      });
      throw new BackendClientError(
        504,
        `Admin facade ${path} did not respond within ${timeoutMs} ms. Check backend gateway/data/analytics logs for a slow or missing Kafka reply. correlationId=${correlationId}`,
        'admin_backend_timeout',
        undefined,
        correlationId,
      );
    }
    const { detail, isUntrustedTls } = fetchErrorDetail(err);
    if (isUntrustedTls) {
      console.warn('[admin-backend] request:tls-untrusted', {
        topic,
        path,
        baseUrl: ADMIN_BACKEND_BASE_URL,
        tlsInsecure: ADMIN_BACKEND_TLS_INSECURE,
        durationMs: Date.now() - startedAt,
        detail,
        correlationId,
      });
      writeAdminRuntimeLog({
        level: 'error',
        source: 'admin-backend',
        event: 'request:tls-untrusted',
        message: detail,
        fields: { topic, path, baseUrl: ADMIN_BACKEND_BASE_URL, tlsInsecure: ADMIN_BACKEND_TLS_INSECURE, durationMs: Date.now() - startedAt, correlationId },
      });
      throw new BackendClientError(
        503,
        `Admin backend TLS certificate is not trusted while calling ${ADMIN_BACKEND_BASE_URL}. Set ADMIN_BACKEND_TLS_INSECURE=1 for autogenerated self-signed backend certs or install a trusted certificate. ${detail} correlationId=${correlationId}`,
        'admin_backend_tls_untrusted',
        detail,
        correlationId,
      );
    }
    console.warn('[admin-backend] request:network-error', {
      topic,
      path,
      baseUrl: ADMIN_BACKEND_BASE_URL,
      durationMs: Date.now() - startedAt,
      detail,
      correlationId,
    });
    writeAdminRuntimeLog({
      level: 'error',
      source: 'admin-backend',
      event: 'request:network-error',
      message: detail,
      fields: { topic, path, baseUrl: ADMIN_BACKEND_BASE_URL, durationMs: Date.now() - startedAt, correlationId },
    });
    throw new BackendClientError(
      503,
      `Admin facade ${path} network error while calling ${ADMIN_BACKEND_BASE_URL}: ${detail} correlationId=${correlationId}`,
      'admin_backend_network_error',
      detail,
      correlationId,
    );
  }

  if (!res.ok) {
    let raw = '';
    let body: GatewayErrorBody | null = null;
    try {
      raw = await res.text();
      body = raw ? JSON.parse(raw) as GatewayErrorBody : null;
    } catch { /* ignore */ }
    console.warn('[admin-backend] request:http-error', {
      topic,
      path,
      status: res.status,
      code: body?.code,
      durationMs: Date.now() - startedAt,
      responseCorrelationId: body?.correlationId ?? res.headers.get('x-correlation-id'),
      correlationId,
      detail: body?.detail ?? body?.error ?? raw.slice(0, 300),
    });
    writeAdminRuntimeLog({
      level: 'error',
      source: 'admin-backend',
      event: 'request:http-error',
      message: body?.detail ?? body?.error ?? raw.slice(0, 300),
      fields: {
        topic,
        path,
        status: res.status,
        code: body?.code,
        durationMs: Date.now() - startedAt,
        responseCorrelationId: body?.correlationId ?? res.headers.get('x-correlation-id'),
        correlationId,
      },
    });
    throw new BackendClientError(
      res.status,
      summarizeBackendError(res.status, path, body, raw, res.headers.get('x-correlation-id') ?? correlationId),
      body?.code,
      body?.detail ?? body?.error ?? raw,
      body?.correlationId ?? res.headers.get('x-correlation-id') ?? correlationId,
    );
  }

  // Gateway wraps responses as plain JSON (JsonElement from .RequestAsync).
  // We return it as-is to match the shape kafkaRequest() callers expect.
  const data = (await res.json()) as Record<string, unknown>;
  console.info('[admin-backend] request:success', {
    topic,
    path,
    status: res.status,
    durationMs: Date.now() - startedAt,
    responseCorrelationId: res.headers.get('x-correlation-id'),
    correlationId,
    responseKeys: Object.keys(data),
  });
  writeAdminRuntimeLog({
    level: 'success',
    source: 'admin-backend',
    event: 'request:success',
    fields: {
      topic,
      path,
      status: res.status,
      durationMs: Date.now() - startedAt,
      responseCorrelationId: res.headers.get('x-correlation-id'),
      correlationId,
      responseKeys: Object.keys(data),
    },
  });
  return data;
}
