/**
 * Server-only SSE hub.
 *
 * Single Kafka consumer per Admin process subscribed to all EVT_* topics,
 * fan-out to every active SSE client.
 *
 * Why this design (was: one consumer per browser tab)
 * ───────────────────────────────────────────────────
 * The previous /api/events route created a new Kafka consumer (with a
 * unique groupId) for each open browser tab. With ten admins on the
 * dashboard you had ten consumer groups, ten group-coordinator sessions,
 * and ten copies of every EVT_* message. That doesn't scale and races
 * with the per-request reply-inbox code in lib/kafka.ts during shutdown.
 *
 * The hub is created lazily on the first /api/events GET. Each browser
 * subscribes a `Subscriber` callback; when a Kafka message arrives, the
 * hub iterates all subscribers and forwards. When the last subscriber
 * disconnects we keep the consumer running — process startup is the only
 * heavy step, and steady-state events are tiny.
 */
import { Kafka, Consumer, logLevel } from 'kafkajs';
import { randomUUID } from 'crypto';
import { Topics } from './topics';

const BOOTSTRAP_SERVERS = process.env.KAFKA_BOOTSTRAP_SERVERS ?? 'redpanda:29092';

// Unique group per Admin process so that every running instance receives
// the full EVT_* stream. With a shared stable groupId, Kafka assigns the
// single EVT_* partition to only one consumer in the group — other instances
// go idle and their browser tabs never receive events.
//
// We intentionally avoid process.env.HOSTNAME: Docker sets HOSTNAME=0.0.0.0
// for the network-bind hint, which makes the group ID identical across all
// container replicas. Use an operator-supplied SSE_INSTANCE_ID env var when
// available; otherwise fall back to a random UUID that is stable for the
// lifetime of this process but unique across replicas.
const SSE_GROUP_ID = `admin-sse-${process.env.SSE_INSTANCE_ID ?? randomUUID()}`;

const EVT_TOPICS = (Object.entries(Topics) as [string, string][])
  .filter(([key]) => key.startsWith('EVT_'))
  .map(([, value]) => value);

export interface SseEvent {
  type: string;
  payload: unknown;
}
export type Subscriber = (event: SseEvent) => void;

let kafka: Kafka | null = null;
let consumer: Consumer | null = null;
let initPromise: Promise<void> | null = null;
const subscribers = new Set<Subscriber>();

function getKafka(): Kafka {
  if (!kafka) {
    kafka = new Kafka({
      clientId: 'admin-sse',
      brokers: BOOTSTRAP_SERVERS.split(','),
      retry: { retries: 2 },
      logLevel: logLevel.ERROR,
    });
  }
  return kafka;
}

async function ensureStarted(): Promise<void> {
  if (initPromise) return initPromise;
  initPromise = (async () => {
    const k = getKafka();

    // Pre-create all EVT_* topics — Redpanda v24 + KafkaJS metadata-v6
    // returns INVALID_PARTITIONS for missing topics on subscribe.
    const admin = k.admin();
    try {
      await admin.connect();
      try {
        await admin.createTopics({
          topics: EVT_TOPICS.map((t) => ({
            topic: t,
            numPartitions: 1,
            replicationFactor: 1,
          })),
          waitForLeaders: false,
        });
      } catch (err) {
        const e = err as { type?: string; code?: number; message?: string };
        const isAlreadyExists =
          e?.type === 'TOPIC_ALREADY_EXISTS' || e?.code === 36 ||
          /already exists/i.test(e?.message ?? '');
        if (!isAlreadyExists) {
          console.warn('[sse-hub] createTopics(EVT_*) failed:', e?.message ?? err);
        }
      }
    } finally {
      try { await admin.disconnect(); } catch { /* ignore */ }
    }

    // Use a unique groupId per process so every Admin instance gets the full
    // EVT_* stream. See SSE_GROUP_ID declaration above for rationale.
    consumer = k.consumer({
      groupId: SSE_GROUP_ID,
      allowAutoTopicCreation: false,
    });
    await consumer.connect();
    await consumer.subscribe({ topics: EVT_TOPICS, fromBeginning: false });
    await consumer.run({
      eachMessage: async ({ topic, message }) => {
        if (subscribers.size === 0) return;
        let payload: unknown;
        try {
          payload = JSON.parse(message.value?.toString() ?? 'null');
        } catch {
          return;
        }
        const event: SseEvent = { type: topic, payload };
        // Snapshot so a subscriber that mutates the set during iteration
        // (rare — caused by an eager unsubscribe inside a handler) is safe.
        for (const fn of [...subscribers]) {
          try { fn(event); } catch { /* never let one bad subscriber take down others */ }
        }
      },
    });
  })();
  initPromise.catch(() => { initPromise = null; });
  return initPromise;
}

/**
 * Subscribe to fan-out. Returns an unsubscribe function.
 * Lazily starts the underlying Kafka consumer on first call.
 */
export async function subscribe(fn: Subscriber): Promise<() => void> {
  await ensureStarted();
  subscribers.add(fn);
  return () => { subscribers.delete(fn); };
}

export function sseHubStatus(): { subscribers: number; running: boolean } {
  return { subscribers: subscribers.size, running: consumer !== null };
}
