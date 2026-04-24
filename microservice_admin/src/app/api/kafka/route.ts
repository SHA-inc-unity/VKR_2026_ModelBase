import { NextRequest, NextResponse } from 'next/server';
import { kafkaRequest } from '@/lib/kafka';

/**
 * POST /api/kafka
 * Body: { topic: string; payload?: Record<string, unknown>; timeoutMs?: number }
 * Returns: { data: Record<string, unknown> } | { error: string }
 *
 * Generic server-side Kafka request-reply proxy.
 * All admin panel pages use this endpoint to send Kafka commands.
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

    const data = await kafkaRequest(topic, payload ?? null, { timeoutMs, correlationId });
    return NextResponse.json({ data });
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
