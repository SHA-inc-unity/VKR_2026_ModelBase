import { NextRequest, NextResponse } from 'next/server';
import { kafkaRequest } from '@/lib/kafka';
import { coalesce, coalesceTtlFor, makeKey } from '@/lib/kafkaCoalesce';
import { isSplitMode, backendCall, BackendClientError } from '@/lib/backendClient';

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
  try {
    const body = await req.json();
    const { topic, payload, timeoutMs, correlationId } = body as {
      topic: string;
      payload?: Record<string, unknown>;
      timeoutMs?: number;
      correlationId?: string;
    };

    if (!topic || typeof topic !== 'string') {
      return NextResponse.json({ error: 'topic is required' }, { status: 400 });
    }

    const ttl = coalesceTtlFor(topic, body);

    if (isSplitMode) {
      // ── Split-deployment: call the gateway admin facade via HTTP ──────────
      const factory = () =>
        backendCall(topic, payload ?? null, { timeoutMs: timeoutMs ?? 30_000 });

      const data = ttl !== null
        ? await coalesce(makeKey(topic, payload ?? null), ttl, factory)
        : await factory();

      return NextResponse.json({ data });
    }

    // ── Local / full-stack: use Kafka directly (unchanged legacy path) ────────
    const factory = () =>
      kafkaRequest(topic, payload ?? null, { timeoutMs, correlationId });

    const data = ttl !== null
      ? await coalesce(makeKey(topic, payload ?? null), ttl, factory)
      : await factory();

    return NextResponse.json({ data });
  } catch (err) {
    if (err instanceof BackendClientError) {
      const status = err.status >= 400 && err.status < 600 ? err.status : 500;
      return NextResponse.json({
        error: err.message,
        status,
        code: err.code,
        detail: err.detail,
        correlationId: err.correlationId,
      }, { status });
    }
    const message = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ error: message }, { status: 500 });
  }
}

