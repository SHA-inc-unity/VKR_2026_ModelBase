/**
 * Client-side helper to call the /api/kafka proxy.
 * Never imports kafkajs (avoids server-side-only modules in browser).
 */
export interface KafkaCallOptions {
  timeoutMs?: number;
  /**
   * Pre-generated correlation id propagated to the server-side request.
   * Lets callers subscribe to progress events that reference the same id
   * before awaiting the reply.
   */
  correlationId?: string;
}

export async function kafkaCall<T = Record<string, unknown>>(
  topic: string,
  payload?: Record<string, unknown>,
  timeoutMsOrOptions?: number | KafkaCallOptions,
): Promise<T> {
  const opts: KafkaCallOptions =
    typeof timeoutMsOrOptions === 'number'
      ? { timeoutMs: timeoutMsOrOptions }
      : timeoutMsOrOptions ?? {};

  const base = process.env.NEXT_PUBLIC_BASE_PATH ?? '';
  const res = await fetch(`${base}/api/kafka`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      topic,
      payload: payload ?? {},
      timeoutMs: opts.timeoutMs,
      correlationId: opts.correlationId,
    }),
  });

  const json = await res.json();

  if (!res.ok || json.error) {
    throw new Error(json.error ?? `HTTP ${res.status}`);
  }

  return json.data as T;
}

export function newCorrelationId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID().replace(/-/g, '');
  }
  // Fallback (unlikely in modern browsers / Node 19+).
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}
