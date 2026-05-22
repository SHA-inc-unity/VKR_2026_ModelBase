/**
 * GET /api/health — infrastructure health probe.
 *
 * In local / full-stack mode: probes Kafka, Redpanda admin API, MinIO,
 * account service and gateway directly.
 *
 * In split-deployment mode (ADMIN_BACKEND_BASE_URL set): skips all direct
 * backend probes and instead probes only the backend readiness endpoint,
 * deriving the gateway/Redpanda/MinIO/account results from the combined backend
 * reachability status.
 */
import { probeKafkaConnectivity } from '@/lib/kafka';
import { writeAdminRuntimeLog } from '@/lib/adminRuntimeLog';
import type { InfraHealthResponse, InfraServiceHealth } from '@/lib/types';

export const dynamic = 'force-dynamic';

const TIMEOUT_MS = 2_000;

const REDPANDA_ADMIN_URL = process.env.REDPANDA_ADMIN_URL ?? 'redpanda:9644';
const MINIO_URL          = process.env.MINIO_URL          ?? 'minio:9000';
const ACCOUNT_URL        = process.env.ACCOUNT_URL        ?? 'account_service_api:5000';
const GATEWAY_URL        = process.env.GATEWAY_URL        ?? 'exchange-gateway:5020';
const KAFKA_BOOTSTRAP_SERVERS = process.env.KAFKA_BOOTSTRAP_SERVERS ?? 'unconfigured';
const BACKEND_CONNECTION_TARGET = process.env.BACKEND_CONNECTION_TARGET?.trim() || 'localhost';

async function probe(url: string, label: string): Promise<InfraServiceHealth> {
  const startedAt = Date.now();
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(TIMEOUT_MS) });
    console.info('[api/health] probe:response', {
      label,
      url,
      status: res.status,
      ok: res.ok,
      durationMs: Date.now() - startedAt,
    });
    writeAdminRuntimeLog({
      level: res.ok ? 'success' : 'warn',
      source: 'api/health',
      event: 'probe:response',
      fields: { label, url, status: res.status, ok: res.ok, durationMs: Date.now() - startedAt },
    });
    if (res.ok) return { status: 'online' };
    return { status: 'offline', error: `HTTP ${res.status}` };
  } catch (err) {
    const msg = err instanceof Error ? err.message : 'unreachable';
    const cause = err instanceof Error && 'cause' in err
      ? (err as Error & { cause?: { code?: string; message?: string } }).cause
      : undefined;
    console.warn('[api/health] probe:error', {
      label,
      url,
      message: msg,
      causeCode: cause?.code,
      causeMessage: cause?.message,
      durationMs: Date.now() - startedAt,
    });
    writeAdminRuntimeLog({
      level: 'error',
      source: 'api/health',
      event: 'probe:error',
      message: msg,
      fields: { label, url, causeCode: cause?.code, causeMessage: cause?.message, durationMs: Date.now() - startedAt },
    });
    return { status: 'offline', error: msg };
  }
}

async function probeWithFallback(
  primaryUrl: string,
  fallbackUrl: string,
  label: string,
): Promise<InfraServiceHealth> {
  const primary = await probe(primaryUrl, label);
  if (primary.status === 'offline' && primary.error === 'HTTP 404') {
    console.info('[api/health] probe:fallback', {
      label,
      primaryUrl,
      fallbackUrl,
      reason: primary.error,
    });
    writeAdminRuntimeLog({
      level: 'warn',
      source: 'api/health',
      event: 'probe:fallback',
      message: primary.error,
      fields: { label, primaryUrl, fallbackUrl },
    });
    return probe(fallbackUrl, label);
  }
  return primary;
}

export async function GET(): Promise<Response> {
  const startedAt = Date.now();
  const adminBackendBaseUrl = (process.env.ADMIN_BACKEND_BASE_URL ?? '').replace(/\/$/, '');
  const adminBackendTlsInsecure = /^(1|true|yes|on)$/i.test(process.env.ADMIN_BACKEND_TLS_INSECURE ?? '');

  console.info('[api/health] request:start', {
    splitMode: adminBackendBaseUrl.length > 0,
    adminBackendBaseUrl: adminBackendBaseUrl || '(empty)',
    adminBackendTlsInsecure,
    nodeTlsRejectUnauthorized: process.env.NODE_TLS_REJECT_UNAUTHORIZED ?? '(unset)',
    kafkaBootstrapServers: KAFKA_BOOTSTRAP_SERVERS,
  });
  writeAdminRuntimeLog({
    level: 'info',
    source: 'api/health',
    event: 'request:start',
    fields: {
      splitMode: adminBackendBaseUrl.length > 0,
      adminBackendBaseUrl: adminBackendBaseUrl || '(empty)',
      adminBackendTlsInsecure,
      nodeTlsRejectUnauthorized: process.env.NODE_TLS_REJECT_UNAUTHORIZED ?? '(unset)',
      kafkaBootstrapServers: KAFKA_BOOTSTRAP_SERVERS,
    },
  });

  // ── Split-deployment: only probe the backend HTTPS endpoint ───────────────
  if (adminBackendBaseUrl.length > 0) {
    if (adminBackendTlsInsecure && adminBackendBaseUrl.startsWith('https://')) {
      process.env.NODE_TLS_REJECT_UNAUTHORIZED = '0';
    }
    const backendProbe = await probeWithFallback(
      `${adminBackendBaseUrl}/health/ready`,
      `${adminBackendBaseUrl}/health`,
      'backend-facade',
    );
    const body: InfraHealthResponse = {
      connectionTarget: adminBackendBaseUrl,
      kafka: {
        status: backendProbe.status,
        bootstrapServers: KAFKA_BOOTSTRAP_SERVERS,
        ...(backendProbe.error ? { error: backendProbe.error } : {}),
      },
      redpanda: backendProbe,
      minio:    backendProbe,
      account:  backendProbe,
      gateway:  backendProbe,
    };
    console.info('[api/health] request:split-result', {
      status: backendProbe.status,
      error: backendProbe.error,
      durationMs: Date.now() - startedAt,
    });
    writeAdminRuntimeLog({
      level: backendProbe.status === 'online' ? 'success' : 'error',
      source: 'api/health',
      event: 'request:split-result',
      message: backendProbe.error,
      fields: { status: backendProbe.status, durationMs: Date.now() - startedAt },
    });
    return Response.json(body);
  }

  // ── Local / full-stack: probe each service directly ───────────────────────
  const kafka = await probeKafkaConnectivity();

  const [redpanda, minio, account, gateway] = await Promise.allSettled([
    probe(`http://${REDPANDA_ADMIN_URL}/v1/status/ready`, 'redpanda'),
    probe(`http://${MINIO_URL}/minio/health/live`, 'minio'),
    probe(`http://${ACCOUNT_URL}/health`, 'account'),
    probeWithFallback(
      `http://${GATEWAY_URL}/health/ready`,
      `http://${GATEWAY_URL}/health`,
      'gateway',
    ),
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

  console.info('[api/health] request:local-result', {
    kafka: body.kafka.status,
    redpanda: body.redpanda.status,
    minio: body.minio.status,
    account: body.account.status,
    gateway: body.gateway.status,
    durationMs: Date.now() - startedAt,
  });
  writeAdminRuntimeLog({
    level: body.kafka.status === 'online' ? 'success' : 'warn',
    source: 'api/health',
    event: 'request:local-result',
    fields: {
      kafka: body.kafka.status,
      redpanda: body.redpanda.status,
      minio: body.minio.status,
      account: body.account.status,
      gateway: body.gateway.status,
      durationMs: Date.now() - startedAt,
    },
  });

  return Response.json(body);
}
