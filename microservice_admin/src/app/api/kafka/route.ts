import { NextRequest, NextResponse } from 'next/server';
import { kafkaRequest } from '@/lib/kafka';
import { coalesce, coalesceTtlFor, makeKey } from '@/lib/kafkaCoalesce';
import { isSplitMode, backendCall, BackendClientError } from '@/lib/backendClient';
import { writeAdminRuntimeLog } from '@/lib/adminRuntimeLog';
import { Topics } from '@/lib/topics';

const QUEUE_HISTORY_TOPICS = new Set<string>([
  Topics.CMD_DATA_DATASET_JOBS_START,
  Topics.CMD_DATA_DATASET_JOBS_CANCEL,
  Topics.CMD_DATA_DATASET_DELETE_ROWS,
  Topics.CMD_DATA_DATASET_CLEAN_APPLY,
  Topics.CMD_DATA_DATASET_EXPORT,
  Topics.CMD_DATA_DATASET_IMPORT_CSV,
  Topics.CMD_DATA_DATASET_UPSERT_OHLCV,
  Topics.CMD_ANALITIC_DATASET_LOAD,
  Topics.CMD_ANALITIC_DATASET_LOAD_OHLCV,
  Topics.CMD_ANALITIC_DATASET_RECOMPUTE_FEATURES,
  Topics.CMD_ANALITIC_ANOMALY_DBSCAN,
  Topics.CMD_ANALYTICS_TRAIN_START,
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function buildPayloadSummary(payload?: Record<string, unknown>): Record<string, unknown> | null {
  if (!payload) return null;

  const summary: Record<string, unknown> = {};
  for (const key of [
    'type',
    'table',
    'target_table',
    'symbol',
    'target_symbol',
    'timeframe',
    'target_timeframe',
    'start_ms',
    'end_ms',
    'target_start_ms',
    'target_end_ms',
    'job_id',
  ]) {
    if (payload[key] !== undefined && payload[key] !== null) {
      summary[key] = payload[key];
    }
  }

  if (isRecord(payload.params)) {
    const paramsSummary: Record<string, unknown> = {};
    for (const key of ['table', 'symbol', 'timeframe', 'start_ms', 'end_ms']) {
      if (payload.params[key] !== undefined && payload.params[key] !== null) {
        paramsSummary[key] = payload.params[key];
      }
    }
    if (Object.keys(paramsSummary).length > 0) {
      summary.params = paramsSummary;
    } else {
      summary.paramsKeys = Object.keys(payload.params).slice(0, 10);
    }
  }

  return Object.keys(summary).length > 0 ? summary : null;
}

function buildResponseSummary(data: Record<string, unknown>): Record<string, unknown> | null {
  const summary: Record<string, unknown> = {};
  for (const key of [
    'job_id',
    'status',
    'deduped',
    'rows_deleted',
    'rows_affected',
    'rows_updated',
    'rows_written',
    'total',
    'audit_id',
    'model_id',
  ]) {
    if (data[key] !== undefined && data[key] !== null) {
      summary[key] = data[key];
    }
  }
  return Object.keys(summary).length > 0 ? summary : null;
}

/**
 * POST /api/kafka
 * Body: { topic: string; payload?: Record<string, unknown>; timeoutMs?: number; correlationId?: string }
 * Returns: { data: Record<string, unknown> } | { error: string }
 *
 * In local / full-stack mode (ADMIN_BACKEND_BASE_URL not set):
 *   proxies to Kafka directly via kafkajs (legacy path).
 *
 * In split-deployment mode (ADMIN_BACKEND_BASE_URL is set):
 *   forwards the call to the gateway admin facade via HTTP instead of Kafka,
 *   eliminating the direct kafkajs dependency on the admin host.
 *
 * Read-only summary topics are coalesced with a short TTL in both modes so
 * that simultaneous fan-outs from the dashboard collapse into one roundtrip.
 */
export async function POST(req: NextRequest) {
  const startedAt = Date.now();
  let requestTopic: string | null = null;
  let queueTopic = false;
  let payloadSummary: Record<string, unknown> | null = null;
  try {
    const body = await req.json();
    const { topic, payload, timeoutMs, correlationId } = body as {
      topic: string;
      payload?: Record<string, unknown>;
      timeoutMs?: number;
      correlationId?: string;
    };

    if (!topic || typeof topic !== 'string') {
      console.warn('[api/kafka] invalid-request', { bodyKeys: Object.keys(body ?? {}) });
      writeAdminRuntimeLog({
        level: 'warn',
        source: 'api/kafka',
        event: 'invalid-request',
        message: 'topic is required',
        fields: { bodyKeys: Object.keys(body ?? {}) },
      });
      return NextResponse.json({ error: 'topic is required' }, { status: 400 });
    }

    requestTopic = topic;

    const ttl = coalesceTtlFor(topic, body);
    queueTopic = QUEUE_HISTORY_TOPICS.has(topic);
    payloadSummary = queueTopic ? buildPayloadSummary(payload ?? undefined) : null;

    console.info('[api/kafka] request:start', {
      topic,
      splitMode: isSplitMode,
      timeoutMs: timeoutMs ?? null,
      callerCorrelationId: correlationId ?? null,
      coalesceTtlMs: ttl,
      payloadKeys: payload ? Object.keys(payload) : [],
    });
    writeAdminRuntimeLog({
      level: 'info',
      source: 'api/kafka',
      event: 'request:start',
      fields: {
        topic,
        queueTopic,
        splitMode: isSplitMode,
        timeoutMs: timeoutMs ?? null,
        callerCorrelationId: correlationId ?? null,
        coalesceTtlMs: ttl,
        payloadKeys: payload ? Object.keys(payload) : [],
        payloadSummary,
      },
    });

    if (isSplitMode) {
      // ── Split-deployment: call the gateway admin facade via HTTP ──────────
      const factory = () =>
        backendCall(topic, payload ?? null, { timeoutMs: timeoutMs ?? 30_000 });

      const data = ttl !== null
        ? await coalesce(makeKey(topic, payload ?? null), ttl, factory)
        : await factory();

      console.info('[api/kafka] request:success', {
        topic,
        splitMode: true,
        durationMs: Date.now() - startedAt,
        responseKeys: Object.keys(data),
      });
      writeAdminRuntimeLog({
        level: 'success',
        source: 'api/kafka',
        event: 'request:success',
        fields: {
          topic,
          queueTopic,
          splitMode: true,
          durationMs: Date.now() - startedAt,
          responseKeys: Object.keys(data),
          payloadSummary,
          responseSummary: queueTopic ? buildResponseSummary(data) : null,
        },
      });
      return NextResponse.json({ data });
    }

    // ── Local / full-stack: use Kafka directly (unchanged legacy path) ────────
    const factory = () =>
      kafkaRequest(topic, payload ?? null, { timeoutMs, correlationId });

    const data = ttl !== null
      ? await coalesce(makeKey(topic, payload ?? null), ttl, factory)
      : await factory();

    console.info('[api/kafka] request:success', {
      topic,
      splitMode: false,
      durationMs: Date.now() - startedAt,
      responseKeys: Object.keys(data),
    });
    writeAdminRuntimeLog({
      level: 'success',
      source: 'api/kafka',
      event: 'request:success',
      fields: {
        topic,
        queueTopic,
        splitMode: false,
        durationMs: Date.now() - startedAt,
        responseKeys: Object.keys(data),
        payloadSummary,
        responseSummary: queueTopic ? buildResponseSummary(data) : null,
      },
    });
    return NextResponse.json({ data });
  } catch (err) {
    if (err instanceof BackendClientError) {
      const status = err.status >= 400 && err.status < 600 ? err.status : 500;
      console.warn('[api/kafka] request:backend-error', {
        status,
        code: err.code,
        detail: err.detail,
        correlationId: err.correlationId,
        durationMs: Date.now() - startedAt,
      });
      writeAdminRuntimeLog({
        level: 'error',
        source: 'api/kafka',
        event: 'request:backend-error',
        message: err.message,
        fields: {
          topic: requestTopic,
          queueTopic,
          status,
          code: err.code,
          detail: err.detail,
          correlationId: err.correlationId,
          durationMs: Date.now() - startedAt,
          payloadSummary,
        },
      });
      return NextResponse.json({
        error: err.message,
        status,
        code: err.code,
        detail: err.detail,
        correlationId: err.correlationId,
      }, { status });
    }
    const message = err instanceof Error ? err.message : String(err);
    console.error('[api/kafka] request:unexpected-error', {
      message,
      durationMs: Date.now() - startedAt,
    });
    writeAdminRuntimeLog({
      level: 'error',
      source: 'api/kafka',
      event: 'request:unexpected-error',
      message,
      fields: {
        topic: requestTopic,
        queueTopic,
        durationMs: Date.now() - startedAt,
        payloadSummary,
      },
    });
    return NextResponse.json({ error: message }, { status: 500 });
  }
}

