/**
 * Server-side Kafka client using kafkajs.
 * Implements request-reply pattern over Kafka.
 *
 * IMPORTANT: This module is server-only (not included in client bundles).
 * Use it only in Route Handlers (app/api/**) or Server Actions.
 */
import { Kafka, Producer, Consumer, EachMessagePayload } from 'kafkajs';
import { v4 as uuidv4 } from 'uuid';
import { replyInbox } from './topics';

const SERVICE_NAME = 'microservice_admin';
const BOOTSTRAP_SERVERS = process.env.KAFKA_BOOTSTRAP_SERVERS ?? 'redpanda:29092';
const DEFAULT_TIMEOUT_MS = 15_000;

// ── Singleton Kafka client ────────────────────────────────────────────────────
let kafka: Kafka | null = null;
let producer: Producer | null = null;
let isProducerConnected = false;

function getKafka(): Kafka {
  if (!kafka) {
    kafka = new Kafka({
      clientId: SERVICE_NAME,
      brokers: BOOTSTRAP_SERVERS.split(','),
      retry: { retries: 5 },
    });
  }
  return kafka;
}

async function getProducer(): Promise<Producer> {
  if (!producer) {
    producer = getKafka().producer();
  }
  if (!isProducerConnected) {
    await producer.connect();
    isProducerConnected = true;
  }
  return producer;
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
 * Creates a temporary consumer subscribed to a unique reply-inbox topic.
 */
export async function kafkaRequest(
  topic: string,
  payload: Record<string, unknown> | null = null,
  options: KafkaRequestOptions = {},
): Promise<Record<string, unknown>> {
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const correlationId = (options.correlationId ?? uuidv4().replace(/-/g, ''));
  const instanceId = uuidv4().replace(/-/g, '');
  const replyTopic = replyInbox(SERVICE_NAME, instanceId);

  const consumer = getKafka().consumer({
    groupId: `${SERVICE_NAME}-reply-${instanceId}`,
    // KafkaJS 2.x + Redpanda: MetadataRequest v6 with auto-create flag returns
    // INVALID_PARTITIONS for non-existent topics. Disable auto-create here and
    // create the reply-inbox topic explicitly via Admin API below.
    allowAutoTopicCreation: false,
  });

  try {
    // ── Explicitly create the reply-inbox topic before subscribing ──
    // Workaround for KafkaJS/Redpanda INVALID_PARTITIONS on MetadataRequest v6.
    const admin = getKafka().admin();
    try {
      await admin.connect();
      await admin.createTopics({
        topics: [{ topic: replyTopic, numPartitions: 1, replicationFactor: 1 }],
        // Redpanda v24 returns an inconsistent response with waitForLeaders: true —
        // KafkaJS throws even though the topic is actually created. Use false and
        // compensate with a post-disconnect sleep below.
        waitForLeaders: false,
      });
    } catch (err) {
      // KafkaJS surfaces TOPIC_ALREADY_EXISTS (error code 36) — idempotent, ignore.
      // Redpanda v24 also surfaces a generic "Topic creation errors" wrapper even
      // when the topic was created successfully — treat that as non-fatal too.
      // Anything else: log and continue; if the topic really is missing,
      // consumer.subscribe() will fail below with a clearer error.
      const e = err as { type?: string; code?: number; message?: string };
      const msg = e?.message ?? '';
      const isAlreadyExists =
        e?.type === 'TOPIC_ALREADY_EXISTS' || e?.code === 36 ||
        /already exists/i.test(msg);
      const isTopicCreationWrapper = /topic creation errors/i.test(msg);
      if (isAlreadyExists) {
        // silent — idempotent
      } else if (isTopicCreationWrapper) {
        console.warn(`[kafka] admin.createTopics(${replyTopic}) wrapper error (non-fatal):`, msg);
      } else {
        console.warn(`[kafka] admin.createTopics(${replyTopic}) failed:`, msg || err);
      }
    } finally {
      try { await admin.disconnect(); } catch { /* ignore */ }
    }

    // Give Redpanda time for leader election after admin.disconnect(); 500ms is
    // enough for a single-node cluster.
    await new Promise((r) => setTimeout(r, 500));

    await consumer.connect();
    // fromBeginning: true — the reply-inbox is a freshly created unique topic,
    // so there are no pre-existing messages. Reading from offset 0 eliminates
    // the race where the reply arrives before the consumer's first poll cycle.
    await consumer.subscribe({ topic: replyTopic, fromBeginning: true });

    const p = await getProducer();

    const envelope = JSON.stringify({
      correlation_id: correlationId,
      reply_to: replyTopic,
      payload: payload ?? {},
    });

    const responsePromise = new Promise<Record<string, unknown>>((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(`Kafka timeout for topic ${topic}`)), timeoutMs);

      consumer.run({
        eachMessage: async ({ message }: EachMessagePayload) => {
          try {
            const body = JSON.parse(message.value?.toString() ?? '{}');
            if (body.correlation_id === correlationId) {
              clearTimeout(timer);
              resolve((body.payload ?? body) as Record<string, unknown>);
            }
          } catch {
            // ignore parse errors
          }
        },
      });

      // Publish the request after consumer is running
      p.send({ topic, messages: [{ key: correlationId, value: envelope }] })
        .catch((err: unknown) => {
          clearTimeout(timer);
          reject(err);
        });
    });

    return await responsePromise;
  } finally {
    await consumer.disconnect();
    // Fire-and-forget cleanup of the ephemeral reply-inbox topic. KafkaJS
    // retries broker errors with exponential backoff (~20s total) — awaiting
    // this would block kafkaRequest() from returning the already-received
    // reply to the caller, breaking short-timeout callers (e.g. health-check
    // with a 2s deadline). Orphaned reply.* topics don't break correctness.
    void (async () => {
      const cleanupAdmin = getKafka().admin();
      try {
        await cleanupAdmin.connect();
        await cleanupAdmin.deleteTopics({ topics: [replyTopic], timeout: 5_000 });
      } catch {
        // ignore
      } finally {
        try { await cleanupAdmin.disconnect(); } catch { /* ignore */ }
      }
    })();
  }
}
