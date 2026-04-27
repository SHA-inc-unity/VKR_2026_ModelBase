import { NextRequest, NextResponse } from 'next/server';
import { kafkaRequest } from '@/lib/kafka';
import { coalesce, coalesceTtlFor, makeKey } from '@/lib/kafkaCoalesce';

/**
 * POST /api/kafka
 * Body: { topic: string; payload?: Record<string, unknown>; timeoutMs?: number; correlationId?: string }
 * Returns: { data: Record<string, unknown> } | { error: string }
 *
 * Generic server-side Kafka request-reply proxy.
 *
 * Read-only summary topics (see lib/kafkaCoalesce.ts) are coalesced with
 * a short TTL so that simultaneous "/health", "/list_tables", "/coverage"
 * fan-outs from a freshly-mounted dashboard collapse into one Kafka
 * roundtrip. Mutating topics pass through unchanged.
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
    const factory = () =>
      kafkaRequest(topic, payload ?? null, { timeoutMs, correlationId });

    const data = ttl !== null
      ? await coalesce(makeKey(topic, payload ?? null), ttl, factory)
      : await factory();

    return NextResponse.json({ data });
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
