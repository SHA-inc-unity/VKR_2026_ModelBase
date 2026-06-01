/**
 * GET /api/events — Server-Sent Events stream.
 *
 * Two modes, transparent to the browser (frame shape is identical):
 *
 * • Local mode — subscribes the connecting browser to the process-wide SSE
 *   hub (`lib/sseHub.ts`). The hub runs a single Kafka consumer for all EVT_*
 *   topics and fans every received event out to all active SSE subscribers —
 *   there is **no** per-client Kafka consumer or group.
 *
 * • Split mode (`ADMIN_BACKEND_BASE_URL` set) — the admin head runs on a
 *   separate host and cannot reach the backend broker. We instead reverse-proxy
 *   the gateway's authenticated `GET /api/admin/events` SSE stream, forwarding
 *   the logged-in admin user's own JWT. The gateway emits the exact same
 *   `data: {type,payload}` frames, so we pipe its body through verbatim. No
 *   Redpanda credential is ever exposed off the backend host.
 *
 * Heartbeat: a `:keepalive` comment is emitted every 25 s (local) / 20 s
 * (gateway) so idle Nginx/Cloudflare proxies don't close the stream.
 */
import { NextRequest } from 'next/server';
import { subscribe } from '@/lib/sseHub';
import { requireAdminSession } from '@/lib/adminSession';
import { isSplitMode, ADMIN_BACKEND_BASE_URL } from '@/lib/backendClient';

export const dynamic = 'force-dynamic';

const SSE_HEADERS = {
  'Content-Type': 'text/event-stream',
  'Cache-Control': 'no-cache, no-transform',
  Connection: 'keep-alive',
  'X-Accel-Buffering': 'no',
} as const;

export async function GET(request: NextRequest): Promise<Response> {
  const session = await requireAdminSession(request);
  if (!session.ok) return session.response;

  // Split mode: reverse-proxy the gateway's authenticated SSE stream.
  if (isSplitMode) {
    let upstream: Response;
    try {
      upstream = await fetch(`${ADMIN_BACKEND_BASE_URL}/api/admin/events`, {
        headers: {
          Authorization: `Bearer ${session.accessToken}`,
          Accept: 'text/event-stream',
        },
        // Propagate browser disconnects → gateway sees the drop and unsubscribes.
        signal: request.signal,
        cache: 'no-store',
      });
    } catch {
      // Backend unreachable — close so the browser's EventSource reconnects.
      return new Response(': upstream-unavailable\n\n', { status: 502 });
    }

    if (!upstream.ok || !upstream.body) {
      return new Response(`: upstream-status-${upstream.status}\n\n`, { status: 502 });
    }

    return new Response(upstream.body, { headers: SSE_HEADERS });
  }

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
