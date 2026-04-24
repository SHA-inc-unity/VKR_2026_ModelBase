import { NextRequest } from 'next/server';
import { kafkaRequest } from '@/lib/kafka';
import { Topics } from '@/lib/topics';

/**
 * POST /api/upload/csv
 * multipart/form-data:
 *   - file: CSV file (header row + data rows)
 *   - table: target table name (e.g. "btcusdt_5m")
 *
 * Response: NDJSON stream (Content-Type: application/x-ndjson). One JSON
 * object per line; the client reads incrementally to drive a batch-progress
 * indicator.
 *
 *   { type: "start", total: <row-count>, batch_size: <rows-per-batch> }
 *   { type: "batch", batch: N, batches: M, imported: <cumulative> }
 *   { type: "done",  imported: <total>, batches: M }
 *   { type: "error", error: <message> }
 *
 * Large files (> 1 MB) are split into `BATCH_SIZE`-row chunks and each chunk
 * is published as a separate `cmd.data.dataset.import_csv` Kafka request with
 * an awaited reply — this avoids huge single Kafka payloads and lets the UI
 * show per-batch progress. Smaller files are sent as a single batch.
 */
export const runtime = 'nodejs';

const BATCH_SIZE         = 1000;
const SINGLE_SHOT_BYTES  = 1 * 1024 * 1024;   // 1 MB
const BATCH_TIMEOUT_MS   = 60_000;

type CsvRow = Record<string, string>;

export async function POST(req: NextRequest) {
  let form: FormData;
  try {
    form = await req.formData();
  } catch (err) {
    return jsonError(400, err instanceof Error ? err.message : String(err));
  }

  const file  = form.get('file');
  const table = form.get('table');
  if (!(file instanceof File)) {
    return jsonError(400, "missing 'file' (multipart field, CSV)");
  }
  if (typeof table !== 'string' || !table.trim()) {
    return jsonError(400, "missing 'table' (target table name)");
  }

  let text: string;
  try {
    text = await file.text();
  } catch (err) {
    return jsonError(500, `failed to read file: ${err instanceof Error ? err.message : String(err)}`);
  }

  const rows = parseCsv(text);
  if (rows.length === 0) {
    return jsonError(400, 'CSV is empty or has no data rows');
  }

  const totalBytes = text.length;
  const total      = rows.length;

  // Large files → multi-batch with awaited replies; small files → single shot.
  const effectiveBatchSize = totalBytes > SINGLE_SHOT_BYTES ? BATCH_SIZE : total;
  const batches = Math.max(1, Math.ceil(total / effectiveBatchSize));

  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      const send = (obj: unknown) =>
        controller.enqueue(encoder.encode(JSON.stringify(obj) + '\n'));

      try {
        send({ type: 'start', total, batch_size: effectiveBatchSize });

        let imported = 0;
        let batch = 0;
        for (let i = 0; i < total; i += effectiveBatchSize) {
          const slice = rows.slice(i, i + effectiveBatchSize);
          const reply = await kafkaRequest(
            Topics.CMD_DATA_DATASET_IMPORT_CSV,
            { table, rows: slice },
            { timeoutMs: BATCH_TIMEOUT_MS },
          ) as { rows_imported?: number; error?: string };

          if (reply?.error) throw new Error(String(reply.error));

          imported += Number(reply?.rows_imported ?? 0);
          batch++;
          send({ type: 'batch', batch, batches, imported });
        }

        send({ type: 'done', imported, batches });
      } catch (err) {
        send({ type: 'error', error: err instanceof Error ? err.message : String(err) });
      } finally {
        controller.close();
      }
    },
  });

  return new Response(stream, {
    status: 200,
    headers: {
      'Content-Type':      'application/x-ndjson; charset=utf-8',
      'Cache-Control':     'no-cache, no-transform',
      'X-Accel-Buffering': 'no',
    },
  });
}

function jsonError(status: number, error: string): Response {
  return new Response(JSON.stringify({ error }), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

/**
 * RFC 4180-ish CSV parser. Handles quoted fields with embedded commas,
 * CRLF/LF line endings, and doubled-quote escape ("") inside quoted fields.
 * The first non-empty line is treated as the header row; subsequent lines
 * are mapped into `Record<header, value>` objects.
 */
function parseCsv(text: string): CsvRow[] {
  if (!text) return [];

  // Strip BOM if present.
  if (text.charCodeAt(0) === 0xFEFF) text = text.slice(1);

  const rows: string[][] = [];
  let field = '';
  let record: string[] = [];
  let inQuotes = false;

  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (inQuotes) {
      if (c === '"') {
        if (text[i + 1] === '"') { field += '"'; i++; }
        else { inQuotes = false; }
      } else {
        field += c;
      }
      continue;
    }
    if (c === '"') { inQuotes = true; continue; }
    if (c === ',') { record.push(field); field = ''; continue; }
    if (c === '\r') continue; // swallow — handled by \n below
    if (c === '\n') {
      record.push(field);
      if (record.length > 1 || record[0] !== '') rows.push(record);
      record = []; field = '';
      continue;
    }
    field += c;
  }
  // Trailing field/record (no terminating newline).
  if (field.length > 0 || record.length > 0) {
    record.push(field);
    if (record.length > 1 || record[0] !== '') rows.push(record);
  }

  if (rows.length < 2) return [];
  const headers = rows[0].map(h => h.trim());
  const out: CsvRow[] = [];
  for (let r = 1; r < rows.length; r++) {
    const values = rows[r];
    const obj: CsvRow = {};
    for (let c = 0; c < headers.length; c++) obj[headers[c]] = values[c] ?? '';
    out.push(obj);
  }
  return out;
}
