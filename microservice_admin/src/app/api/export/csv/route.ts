import { kafkaRequest } from '@/lib/kafka';
import { Topics } from '@/lib/topics';
import { TIMEFRAMES, makeTableName } from '@/lib/constants';

export const runtime = 'nodejs';

/**
 * GET /api/export/csv
 *
 * Two modes based on query params:
 *
 * 1. Single table:
 *      ?table=<t>&start_ms=<n>&end_ms=<n>
 *    DataService exports CSV to MinIO via streaming pipe and returns
 *    { presigned_url }. We forward the URL as JSON; the browser downloads
 *    directly from MinIO — zero bytes flow through Admin.
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

      return Response.json({ presigned_url: reply.presigned_url });
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

    return Response.json({ presigned_url: reply.presigned_url });
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return Response.json({ error: message }, { status: 500 });
  }
}


