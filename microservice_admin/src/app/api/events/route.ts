/**
 * GET /api/events — Server-Sent Events stream.
 *
 * Subscribes to all EVT_* Kafka topics and forwards each message to the client
 * as an SSE event with the shape: { type: string; payload: unknown }
 *
 * A new Kafka consumer is created per connection (unique group ID) so every
 * connected client receives all events independently.
 * The consumer is disconnected when the HTTP connection is closed.
 */
import { NextRequest } from 'next/server';
import { Kafka, logLevel } from 'kafkajs';
import { v4 as uuidv4 } from 'uuid';
import { Topics } from '@/lib/topics';

const BOOTSTRAP_SERVERS = process.env.KAFKA_BOOTSTRAP_SERVERS ?? 'redpanda:29092';

// Collect all EVT_ topic values from the Topics map
const EVT_TOPICS = (Object.entries(Topics) as [string, string][])
  .filter(([key]) => key.startsWith('EVT_'))
  .map(([, value]) => value);

// Shared Kafka client for SSE consumers (not the same as lib/kafka.ts)
let sseKafka: Kafka | null = null;
function getSseKafka(): Kafka {
  if (!sseKafka) {
    sseKafka = new Kafka({
      clientId: 'admin-sse',
      brokers: BOOTSTRAP_SERVERS.split(','),
      retry: { retries: 2 },
      logLevel: logLevel.ERROR,
    });
  }
  return sseKafka;
}

export const dynamic = 'force-dynamic';

export async function GET(request: NextRequest): Promise<Response> {
  const instanceId = uuidv4().replace(/-/g, '');
  const consumer = getSseKafka().consumer({
    groupId: `admin-sse-${instanceId}`,
    // KafkaJS MetadataRequest v6 against Redpanda returns INVALID_PARTITIONS for
    // non-existent topics; disable auto-create and create EVT_* topics explicitly
    // via Admin API below.
    allowAutoTopicCreation: false,
  });

  // Encoder is created once per connection
  const encoder = new TextEncoder();

  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      if (request.signal.aborted) {
        controller.close();
        return;
      }

      // Clean up Kafka consumer when the client disconnects
      request.signal.addEventListener('abort', () => {
        consumer.disconnect().catch(() => { /* ignore */ });
        try { controller.close(); } catch { /* already closed */ }
      });

      try {
        // Explicitly create all EVT_* topics before the consumer subscribes —
        // same workaround as lib/kafka.ts for KafkaJS/Redpanda INVALID_PARTITIONS.
        const admin = getSseKafka().admin();
        try {
          await admin.connect();
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
            console.warn('[sse] admin.createTopics(EVT_*) failed:', e?.message ?? err);
          }
        } finally {
          try { await admin.disconnect(); } catch { /* ignore */ }
        }

        // Let Redpanda elect leaders before the consumer connects.
        await new Promise((r) => setTimeout(r, 300));

        await consumer.connect();
        await consumer.subscribe({ topics: EVT_TOPICS, fromBeginning: false });
        await consumer.run({
          eachMessage: async ({ topic, message }) => {
            if (request.signal.aborted) return;
            try {
              const payload = JSON.parse(message.value?.toString() ?? 'null') as unknown;
              const data = JSON.stringify({ type: topic, payload });
              controller.enqueue(encoder.encode(`data: ${data}\n\n`));
            } catch {
              // skip malformed messages
            }
          },
        });
      } catch {
        // Kafka unavailable – close stream gracefully so the client can reconnect
        try { controller.close(); } catch { /* already closed */ }
      }
    },
    cancel() {
      consumer.disconnect().catch(() => { /* ignore */ });
    },
  });

  return new Response(stream, {
    headers: {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      'Connection': 'keep-alive',
    },
  });
}
