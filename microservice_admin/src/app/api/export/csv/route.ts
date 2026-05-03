import { kafkaRequest } from '@/lib/kafka';
import { Topics } from '@/lib/topics';
import { TIMEFRAMES, makeTableName } from '@/lib/constants';

export const runtime = 'nodejs';

const DOWNLOAD_BUCKET_PREFIX = '/modelline-blobs/';

function firstHeaderValue(value: string | null): string | null {
  if (!value) return null;
  const [first] = value.split(',');
  return first?.trim() || null;
}

function isLoopbackHost(hostname: string): boolean {
  const host = hostname.toLowerCase();
  return host === 'localhost'
    || host === '127.0.0.1'
    || host === '0.0.0.0'
    || host === '::1'
    || host === '[::1]';
}

function isProxyFriendlyOrigin(url: URL): boolean {
  if (!url.port) return true;
  return (url.protocol === 'http:' && url.port === '80')
    || (url.protocol === 'https:' && url.port === '443');
}

function getRequestOrigin(req: Request): URL {
  const fallback = new URL(req.url);
  const proto = firstHeaderValue(req.headers.get('x-forwarded-proto')) ?? fallback.protocol.replace(/:$/, '');
  const host = firstHeaderValue(req.headers.get('x-forwarded-host'))
    ?? firstHeaderValue(req.headers.get('host'))
    ?? fallback.host;
  return new URL(`${proto}://${host}`);
}

function normalizePresignedDownloadUrl(rawUrl: string, req: Request): string {
  const downloadUrl = new URL(rawUrl);
  const requestOrigin = getRequestOrigin(req);
  const host = downloadUrl.hostname.toLowerCase();
  const pathLooksLikeObject = downloadUrl.pathname.startsWith(DOWNLOAD_BUCKET_PREFIX);
  const pointsToRawObjectStorage = host === 'minio'
    || isLoopbackHost(host)
    || downloadUrl.port === '9000';

  if (pathLooksLikeObject && pointsToRawObjectStorage && isProxyFriendlyOrigin(requestOrigin)) {
    downloadUrl.protocol = requestOrigin.protocol;
    downloadUrl.host = requestOrigin.host;
    return downloadUrl.toString();
  }

  if (host === 'minio' || (isLoopbackHost(host) && !isLoopbackHost(requestOrigin.hostname))) {
    throw new Error(
      'Download path points to an internal/local object-storage address. Configure a browser-reachable MinIO path or expose /modelline-blobs/* through the external proxy.',
    );
  }

  return downloadUrl.toString();
}

/**
 * GET /api/export/csv
 *
 * Two modes based on query params:
 *
 * 1. Single table:
 *      ?table=<t>&start_ms=<n>&end_ms=<n>
 *    DataService exports CSV to MinIO via streaming pipe and returns
 *    { presigned_url }. We forward a browser-reachable URL as JSON; in
 *    proxy deployments the host is normalized to the current external
 *    origin while keeping the signed bucket path/query intact. The browser
 *    still downloads the object directly — zero file bytes flow through Admin.
 *
 * 2. All-timeframes ZIP:
 *      ?symbol=<s>&timeframe=ALL&start_ms=<n>&end_ms=<n>
 *    DataService streams all per-timeframe CSVs into a ZIP via pipe →
 *    MinIO and returns { presigned_url }. Same pattern as mode 1.
 *
 * No S3 SDK, no bytes in Admin memory, no Content-Length buffering.
 */
export async function GET(req: Request) {
  try {
    const { searchParams } = new URL(req.url);
    const startMs   = searchParams.get('start_ms');
    const endMs     = searchParams.get('end_ms');
    const table     = searchParams.get('table');
    const symbol    = searchParams.get('symbol');
    const timeframe = searchParams.get('timeframe');

    if (!startMs || !endMs) {
      return Response.json(
        { error: 'start_ms and end_ms are required' },
        { status: 400 },
      );
    }

    const startNum = Number(startMs);
    const endNum   = Number(endMs);
    if (!Number.isFinite(startNum) || !Number.isFinite(endNum)) {
      return Response.json(
        { error: 'start_ms and end_ms must be numeric' },
        { status: 400 },
      );
    }

    const isAll = timeframe === 'ALL';

    // ── Mode 2: ZIP of all timeframes ──────────────────────────────────────
    if (isAll) {
      if (!symbol) {
        return Response.json(
          { error: 'symbol is required when timeframe=ALL' },
          { status: 400 },
        );
      }
      const tables = TIMEFRAMES.map(tf => makeTableName(symbol, tf));

      const reply = await kafkaRequest(
        Topics.CMD_DATA_DATASET_EXPORT,
        { tables, symbol, start_ms: startNum, end_ms: endNum },
        { timeoutMs: 300_000 },
      ) as { presigned_url?: string; error?: string };

      if (reply?.error) {
        return Response.json({ error: reply.error }, { status: 500 });
      }
      if (!reply?.presigned_url) {
        return Response.json(
          { error: 'export reply missing presigned_url' },
          { status: 500 },
        );
      }

      return Response.json({ presigned_url: normalizePresignedDownloadUrl(reply.presigned_url, req) });
    }

    // ── Mode 1: single-table CSV ───────────────────────────────────────────
    if (!table) {
      return Response.json(
        { error: 'table is required (or provide symbol + timeframe=ALL)' },
        { status: 400 },
      );
    }

    const reply = await kafkaRequest(
      Topics.CMD_DATA_DATASET_EXPORT,
      { table, start_ms: startNum, end_ms: endNum },
      { timeoutMs: 300_000 },
    ) as { presigned_url?: string; error?: string };

    if (reply?.error) {
      return Response.json({ error: reply.error }, { status: 500 });
    }
    if (!reply?.presigned_url) {
      return Response.json(
        { error: 'export reply missing presigned_url' },
        { status: 500 },
      );
    }

    return Response.json({ presigned_url: normalizePresignedDownloadUrl(reply.presigned_url, req) });
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return Response.json({ error: message }, { status: 500 });
  }
}


