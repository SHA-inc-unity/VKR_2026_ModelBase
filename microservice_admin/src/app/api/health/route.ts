/**
 * GET /api/health — infrastructure health probe.
 *
 * Application services (data, analitic, account, gateway) are health-checked
 * via Kafka (cmd.*.health topics), not here. This route only probes the two
 * shared-infra services that sit in modelline_net and don't speak Kafka:
 * Redpanda's admin API and MinIO's liveness endpoint.
 */
import type { InfraHealthResponse, InfraServiceHealth } from '@/lib/types';

export const dynamic = 'force-dynamic';

const TIMEOUT_MS = 2_000;

const REDPANDA_ADMIN_URL = process.env.REDPANDA_ADMIN_URL ?? 'redpanda:9644';
const MINIO_URL          = process.env.MINIO_URL          ?? 'minio:9000';
const ACCOUNT_URL        = process.env.ACCOUNT_URL        ?? 'account_service_api:5000';
const GATEWAY_URL        = process.env.GATEWAY_URL        ?? 'exchange-gateway:5020';

async function probe(url: string): Promise<InfraServiceHealth> {
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(TIMEOUT_MS) });
    if (res.ok) return { status: 'online' };
    return { status: 'offline', error: `HTTP ${res.status}` };
  } catch (err) {
    const msg = err instanceof Error ? err.message : 'unreachable';
    return { status: 'offline', error: msg };
  }
}

export async function GET(): Promise<Response> {
  const [redpanda, minio, account, gateway] = await Promise.allSettled([
    probe(`http://${REDPANDA_ADMIN_URL}/v1/status/ready`),
    probe(`http://${MINIO_URL}/minio/health/live`),
    probe(`http://${ACCOUNT_URL}/health`),
    probe(`http://${GATEWAY_URL}/health`),
  ]);

  const unwrap = (r: PromiseSettledResult<InfraServiceHealth>): InfraServiceHealth =>
    r.status === 'fulfilled' ? r.value : { status: 'offline', error: String(r.reason) };

  const body: InfraHealthResponse = {
    redpanda: unwrap(redpanda),
    minio:    unwrap(minio),
    account:  unwrap(account),
    gateway:  unwrap(gateway),
  };

  return Response.json(body);
}
