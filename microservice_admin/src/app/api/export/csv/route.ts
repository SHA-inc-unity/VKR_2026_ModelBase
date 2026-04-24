import { NextRequest, NextResponse } from 'next/server';
import { GetObjectCommand, S3Client } from '@aws-sdk/client-s3';
import { kafkaRequest } from '@/lib/kafka';
import { Topics } from '@/lib/topics';
import { TIMEFRAMES, makeTableName } from '@/lib/constants';

export const runtime = 'nodejs';

/**
 * GET /api/export/csv
 *
 * Two modes based on query params:
 *
 * 1. Single table (legacy, default):
 *      ?table=<t>&start_ms=<n>&end_ms=<n>
 *    DataService streams CSV from PostgreSQL into MinIO and returns
 *    { presigned_url }. We reply with a 302 redirect — the browser
 *    fetches the object directly from MinIO, bytes never transit here.
 *    Timeout: 300 s (same as ZIP; export dominates for large windows).
 *
 * 2. All-timeframes ZIP:
 *      ?symbol=<s>&timeframe=ALL&start_ms=<n>&end_ms=<n>
 *    We expand the timeframe list server-side to { tables: [...] },
 *    DataService bundles every per-timeframe CSV into a single ZIP,
 *    parks it in MinIO, and returns a claim-check. We fetch the object
 *    with @aws-sdk/client-s3 and re-stream it to the browser as
 *    application/zip with filename "<symbol>_ALL.zip". This avoids the
 *    Chromium-only-allows-first-few-programmatic-downloads limit that
 *    the old loop-click-<a> client hit.
 */
export async function GET(req: NextRequest) {
  try {
    const { searchParams } = new URL(req.url);
    const startMs = searchParams.get('start_ms');
    const endMs   = searchParams.get('end_ms');
    const table   = searchParams.get('table');
    const symbol  = searchParams.get('symbol');
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

    // ── Mode 2: ZIP of all timeframes ──────────────────────────────────
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
        { tables, start_ms: startNum, end_ms: endNum },
        { timeoutMs: 300_000 },
      ) as { claim_check?: { key: string; bucket: string }; error?: string };

      if (reply?.error) {
        return Response.json({ error: reply.error }, { status: 500 });
      }

      const claim = reply?.claim_check;
      if (!claim?.key || !claim?.bucket) {
        return Response.json(
          { error: 'export reply missing claim_check' },
          { status: 500 },
        );
      }

      // Pull the ZIP object from MinIO. Admin reaches MinIO over the
      // Docker-internal hostname (MINIO_URL=minio:9000), so the browser
      // never needs a valid route to MinIO for this branch.
      const endpoint = `http://${process.env.MINIO_URL ?? 'minio:9000'}`;
      const s3 = new S3Client({
        endpoint,
        region: 'us-east-1',
        credentials: {
          accessKeyId:     process.env.MINIO_ACCESS_KEY ?? 'modelline',
          secretAccessKey: process.env.MINIO_SECRET_KEY ?? 'modelline_secret',
        },
        forcePathStyle: true,
      });

      const obj = await s3.send(new GetObjectCommand({
        Bucket: claim.bucket,
        Key:    claim.key,
      }));
      const body = obj.Body;
      if (!body) {
        return Response.json({ error: 'empty object body' }, { status: 500 });
      }
      const bytes = await (body as { transformToByteArray: () => Promise<Uint8Array> })
        .transformToByteArray();

      return new Response(Buffer.from(bytes), {
        status: 200,
        headers: {
          'Content-Type':        'application/zip',
          'Content-Disposition': `attachment; filename="${symbol}_ALL.zip"`,
          'Content-Length':      String(bytes.byteLength),
          'Cache-Control':       'no-store',
        },
      });
    }

    // ── Mode 1: single-table CSV via presigned redirect ────────────────
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
    const url = reply?.presigned_url;
    if (!url) {
      return Response.json(
        { error: 'export reply missing presigned_url' },
        { status: 500 },
      );
    }
    return NextResponse.redirect(url, 302);
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return Response.json({ error: message }, { status: 500 });
  }
}
