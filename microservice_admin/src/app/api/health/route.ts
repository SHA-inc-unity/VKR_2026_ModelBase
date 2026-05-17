/**
 * GET /api/health — infrastructure health probe.
 *
 * In local / full-stack mode: probes Kafka, Redpanda admin API, MinIO,
 * account service and gateway directly.
 *
 * In split-deployment mode (ADMIN_BACKEND_BASE_URL set): skips all direct
 * backend probes and instead probes only the backend HTTPS endpoint itself,
 * deriving the gateway/Redpanda/MinIO results from the combined backend
 * reachability status.
 */
import { probeKafkaConnectivity } from '@/lib/kafka';
import { isSplitMode, ADMIN_BACKEND_BASE_URL } from '@/lib/backendClient';
import type { InfraHealthResponse, InfraServiceHealth } from '@/lib/types';

export const dynamic = 'force-dynamic';

const TIMEOUT_MS = 2_000;

const REDPANDA_ADMIN_URL = process.env.REDPANDA_ADMIN_URL ?? 'redpanda:9644';
const MINIO_URL          = process.env.MINIO_URL          ?? 'minio:9000';
const ACCOUNT_URL        = process.env.ACCOUNT_URL        ?? 'account_service_api:5000';
const GATEWAY_URL        = process.env.GATEWAY_URL        ?? 'exchange-gateway:5020';
const BACKEND_CONNECTION_TARGET = process.env.BACKEND_CONNECTION_TARGET?.trim() || 'localhost';

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
  // ── Split-deployment: only probe the backend HTTPS endpoint ───────────────
  if (isSplitMode) {
    const backendProbe = await probe(`${ADMIN_BACKEND_BASE_URL}/health`);
    const body: InfraHealthResponse = {
      connectionTarget: ADMIN_BACKEND_BASE_URL,
      kafka:    backendProbe.status === 'online' ? { status: 'ok' } : { status: 'error', error: backendProbe.error },
      redpanda: backendProbe,
      minio:    backendProbe,
      account:  { status: 'unknown' },
      gateway:  backendProbe,
    };
    return Response.json(body);
  }

  // ── Local / full-stack: probe each service directly ───────────────────────
  const kafka = await probeKafkaConnectivity();

  const [redpanda, minio, account, gateway] = await Promise.allSettled([
    probe(`http://${REDPANDA_ADMIN_URL}/v1/status/ready`),
    probe(`http://${MINIO_URL}/minio/health/live`),
    probe(`http://${ACCOUNT_URL}/health`),
    probe(`http://${GATEWAY_URL}/health`),
  ]);

  const unwrap = (r: PromiseSettledResult<InfraServiceHealth>): InfraServiceHealth =>
    r.status === 'fulfilled' ? r.value : { status: 'offline', error: String(r.reason) };

  const body: InfraHealthResponse = {
    connectionTarget: BACKEND_CONNECTION_TARGET,
    kafka,
    redpanda: unwrap(redpanda),
    minio:    unwrap(minio),
    account:  unwrap(account),
    gateway:  unwrap(gateway),
  };

  return Response.json(body);
}
