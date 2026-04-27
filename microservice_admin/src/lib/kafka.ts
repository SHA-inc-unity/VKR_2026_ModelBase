/**
 * Server-side Kafka request/reply client (kafkajs).
 *
 * IMPORTANT: server-only. Do not import from client bundles.
 *
 * Architecture
 * ────────────
 * One Kafka producer + one consumer per Admin process. The consumer is
 * subscribed to a single long-lived reply-inbox topic created at startup
 * (`reply.microservice_admin.<instance>`). Outbound requests carry a unique
 * correlation_id and an entry in `_pending`; the consume loop routes the
 * matching reply envelope back to its TaskCompletionSource and resolves the
 * caller's promise.
 *
 * Why this design (was: per-request reply-inbox)
 * ──────────────────────────────────────────────
 * The previous implementation created a new reply-inbox topic + consumer
 * (and a 500 ms post-create sleep) for *every* kafkaRequest() call, then
 * deleted the topic in finally{}. That produced:
 *   - leaked topics after timeouts → required a 30-min janitor sweep,
 *   - 500 ms latency floor for every request,
 *   - tens of admin/createTopics + admin/deleteTopics RPCs per minute.
 *
 * The long-lived inbox removes per-request Kafka admin overhead: a request
 * is now "publish + await pending future". Latency for cached-warm topics
 * drops from ~700 ms to <50 ms locally.
 *
 * Lifetime
 * ────────
 * The consumer/producer are created lazily on first `kafkaRequest()` and
 * shut down on Node process exit (best effort — Kafka is fine if we don't).
 * Pending requests left over at shutdown reject with an error.
 */
import { Kafka, Producer, Consumer, EachMessagePayload, logLevel } from 'kafkajs';
import { v4 as uuidv4 } from 'uuid';
import { replyInbox } from './topics';

const SERVICE_NAME = 'microservice_admin';
const BOOTSTRAP_SERVERS = process.env.KAFKA_BOOTSTRAP_SERVERS ?? 'redpanda:29092';
const DEFAULT_TIMEOUT_MS = 15_000;

// One reply inbox per Admin process. Suffix is a short uuid so that two
// instances of admin (e.g. local + docker) don't collide on the same topic.
const INSTANCE_ID = uuidv4().replace(/-/g, '').slice(0, 8);
const REPLY_INBOX = replyInbox(SERVICE_NAME, INSTANCE_ID);

// ── Singleton Kafka client + connection state ────────────────────────────────
let kafka: Kafka | null = null;
let producer: Producer | null = null;
let consumer: Consumer | null = null;

interface PendingRequest {
  resolve: (value: Record<string, unknown>) => void;
  reject: (err: Error) => void;
  timer: NodeJS.Timeout;
}
const pending = new Map<string, PendingRequest>();

let initPromise: Promise<void> | null = null;

function getKafka(): Kafka {
  if (!kafka) {
    kafka = new Kafka({
      clientId: SERVICE_NAME,
      brokers: BOOTSTRAP_SERVERS.split(','),
      retry: { retries: 5 },
      logLevel: logLevel.ERROR,
    });
  }
  return kafka;
}

/**
 * Initialise (idempotent): create the reply-inbox topic, start producer,
 * start consumer, kick off the dispatch loop.
 *
 * Concurrent callers share a single in-flight initialisation — the function
 * caches its promise in `initPromise`.
 */
function ensureStarted(): Promise<void> {
  if (initPromise) return initPromise;
  initPromise = (async () => {
    const k = getKafka();

    // Create the reply-inbox topic explicitly. KafkaJS+Redpanda v24 returns
    // INVALID_PARTITIONS for non-existent topics on consumer subscribe, so
    // we cannot rely on auto-create. Idempotent.
    const admin = k.admin();
    try {
      await admin.connect();
      try {
        await admin.createTopics({
          topics: [{ topic: REPLY_INBOX, numPartitions: 1, replicationFactor: 1 }],
          waitForLeaders: false,
        });
      } catch (err) {
        const e = err as { type?: string; code?: number; message?: string };
        const isAlreadyExists =
          e?.type === 'TOPIC_ALREADY_EXISTS' || e?.code === 36 ||
          /already exists/i.test(e?.message ?? '');
        if (!isAlreadyExists && !/topic creation errors/i.test(e?.message ?? '')) {
          console.warn(`[kafka] createTopics(${REPLY_INBOX}) failed:`, e?.message ?? err);
        }
      }
    } finally {
      try { await admin.disconnect(); } catch { /* ignore */ }
    }

    producer = k.producer();
    await producer.connect();

    consumer = k.consumer({
      groupId: `${SERVICE_NAME}-reply-${INSTANCE_ID}`,
      allowAutoTopicCreation: false,
    });
    await consumer.connect();
    await consumer.subscribe({ topic: REPLY_INBOX, fromBeginning: false });

    // Single dispatch loop: routes each incoming reply to its waiter via
    // correlation_id. consumer.run() returns immediately; the loop runs
    // in the background.
    await consumer.run({
      eachMessage: async ({ message }: EachMessagePayload) => {
        try {
          const body = JSON.parse(message.value?.toString() ?? '{}');
          const cid = body.correlation_id as string | undefined;
          if (!cid) return;
          const waiter = pending.get(cid);
          if (!waiter) return;
          pending.delete(cid);
          clearTimeout(waiter.timer);
          waiter.resolve((body.payload ?? body) as Record<string, unknown>);
        } catch {
          // ignore malformed envelopes
        }
      },
    });

    // Best-effort cleanup on process exit. Kafka is durable; this is just
    // tidy: it disconnects the producer/consumer so there's no leftover
    // group-coordinator session.
    const shutdown = async () => {
      for (const [, w] of pending) {
        clearTimeout(w.timer);
        w.reject(new Error('Admin process is shutting down'));
      }
      pending.clear();
      try { await consumer?.disconnect(); } catch { /* ignore */ }
      try { await producer?.disconnect(); } catch { /* ignore */ }
    };
    process.once('SIGTERM', shutdown);
    process.once('SIGINT',  shutdown);
  })();

  // If init throws, drop the cached promise so the next caller retries.
  initPromise.catch(() => { initPromise = null; });
  return initPromise;
}

// ── Request-reply ─────────────────────────────────────────────────────────────

export interface KafkaRequestOptions {
  timeoutMs?: number;
  /**
   * Optional pre-generated correlation id. When provided, the caller can
   * subscribe to progress events (e.g. `events.data.ingest.progress`) that
   * reference the same id while the request is still in flight.
   */
  correlationId?: string;
}

/**
 * Send a request to a Kafka topic and wait for a reply.
 *
 * Uses the long-lived reply inbox: the request envelope's `reply_to` points
 * at our process-wide topic, the consume loop matches the reply by
 * correlation_id, and resolves the returned promise.
 */
export async function kafkaRequest(
  topic: string,
  payload: Record<string, unknown> | null = null,
  options: KafkaRequestOptions = {},
): Promise<Record<string, unknown>> {
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const correlationId = (options.correlationId ?? uuidv4().replace(/-/g, ''));

  await ensureStarted();
  if (!producer) throw new Error('Kafka producer not initialised');

  const envelope = JSON.stringify({
    correlation_id: correlationId,
    reply_to: REPLY_INBOX,
    payload: payload ?? {},
  });

  return new Promise<Record<string, unknown>>((resolve, reject) => {
    const timer = setTimeout(() => {
      pending.delete(correlationId);
      reject(new Error(`Kafka timeout for topic ${topic}`));
    }, timeoutMs);

    pending.set(correlationId, { resolve, reject, timer });

    producer!
      .send({ topic, messages: [{ key: correlationId, value: envelope }] })
      .catch((err: unknown) => {
        const w = pending.get(correlationId);
        if (!w) return;
        pending.delete(correlationId);
        clearTimeout(w.timer);
        reject(err instanceof Error ? err : new Error(String(err)));
      });
  });
}

/** Diagnostics — used by the optional /api/health route. */
export function kafkaStatus(): { replyInbox: string; pending: number } {
  return { replyInbox: REPLY_INBOX, pending: pending.size };
}
