import { NextRequest, NextResponse } from 'next/server';
import { kafkaRequest } from '@/lib/kafka';
import { coalesce, coalesceTtlFor, makeKey } from '@/lib/kafkaCoalesce';
import { isSplitMode, backendCall, BackendClientError } from '@/lib/backendClient';
import { writeAdminRuntimeLog } from '@/lib/adminRuntimeLog';

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

    const ttl = coalesceTtlFor(topic, body);

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
        splitMode: isSplitMode,
        timeoutMs: timeoutMs ?? null,
        callerCorrelationId: correlationId ?? null,
        coalesceTtlMs: ttl,
        payloadKeys: payload ? Object.keys(payload) : [],
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
        fields: { topic, splitMode: true, durationMs: Date.now() - startedAt, responseKeys: Object.keys(data) },
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
      fields: { topic, splitMode: false, durationMs: Date.now() - startedAt, responseKeys: Object.keys(data) },
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
          status,
          code: err.code,
          detail: err.detail,
          correlationId: err.correlationId,
          durationMs: Date.now() - startedAt,
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
      fields: { durationMs: Date.now() - startedAt },
    });
    return NextResponse.json({ error: message }, { status: 500 });
  }
}

