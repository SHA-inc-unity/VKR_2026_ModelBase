/**
 * GET /api/events — Server-Sent Events stream.
 *
 * Subscribes the connecting browser to the process-wide SSE hub
 * (`lib/sseHub.ts`). The hub runs a single Kafka consumer for all
 * EVT_* topics and fans every received event out to all active SSE
 * subscribers — there is **no** per-client Kafka consumer or group.
 *
 * Heartbeat: a `:keepalive` comment is emitted every 25 s so that
 * idle Nginx/Cloudflare proxies don't close the stream.
 */
import { NextRequest } from 'next/server';
import { subscribe } from '@/lib/sseHub';

export const dynamic = 'force-dynamic';

export async function GET(request: NextRequest): Promise<Response> {
  const encoder = new TextEncoder();

  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      if (request.signal.aborted) {
        controller.close();
        return;
      }

      let unsubscribe: (() => void) | null = null;
      let heartbeat: ReturnType<typeof setInterval> | null = null;

      const cleanup = () => {
        if (unsubscribe) { try { unsubscribe(); } catch { /* ignore */ } unsubscribe = null; }
        if (heartbeat)   { clearInterval(heartbeat); heartbeat = null; }
        try { controller.close(); } catch { /* already closed */ }
      };

      request.signal.addEventListener('abort', cleanup);

      try {
        unsubscribe = await subscribe((event) => {
          if (request.signal.aborted) return;
          try {
            const data = JSON.stringify(event);
            controller.enqueue(encoder.encode(`data: ${data}\n\n`));
          } catch {
            // controller closed mid-write — ignore
          }
        });

        heartbeat = setInterval(() => {
          if (request.signal.aborted) return;
          try {
            controller.enqueue(encoder.encode(`: keepalive\n\n`));
          } catch {
            // controller closed — let abort handler clean up
          }
        }, 25_000);
      } catch {
        // Hub failed to start (Kafka unavailable) — close gracefully so
        // the browser's EventSource reconnects.
        cleanup();
      }
    },
    cancel() {
      // The abort listener above runs as well; nothing extra needed.
    },
  });

  return new Response(stream, {
    headers: {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache, no-transform',
      'Connection': 'keep-alive',
      'X-Accel-Buffering': 'no',
    },
  });
}
