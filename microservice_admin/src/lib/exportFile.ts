/**
 * Browser-side download helpers — convert in-memory rows to a CSV/JSON Blob
 * and trigger a download via a temporary anchor element. No external libs,
 * no backend round-trip.
 *
 * For our anomaly-report use case the rows are small (a few thousand at
 * most), so building the entire file in memory is fine. Switch to a
 * streaming writer if that ever changes.
 */

/** Escape a value for embedding into a CSV cell. */
function csvEscape(v: unknown): string {
  if (v === null || v === undefined) return '';
  const s = typeof v === 'string' ? v : JSON.stringify(v);
  // Wrap in quotes if cell contains comma, quote, newline; double internal quotes.
  if (/[",\r\n]/.test(s)) {
    return `"${s.replace(/"/g, '""')}"`;
  }
  return s;
}

export function rowsToCsv(
  rows: ReadonlyArray<Record<string, unknown>>,
  columns?: ReadonlyArray<string>,
): string {
  if (rows.length === 0) return '';
  const cols = columns ?? Object.keys(rows[0]);
  const header = cols.join(',');
  const body = rows.map(r => cols.map(c => csvEscape(r[c])).join(','));
  return [header, ...body].join('\r\n');
}

/** Trigger a browser download of `content` with the given filename and MIME. */
export function downloadFile(content: string, filename: string, mime: string): void {
  // BOM helps Excel recognise UTF-8 CSVs; harmless for JSON consumers.
  const prefix = mime.startsWith('text/csv') ? '﻿' : '';
  const blob = new Blob([prefix + content], { type: `${mime};charset=utf-8` });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement('a');
  a.href     = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  // Revoke async to avoid races on Safari.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function downloadCsv(
  rows: ReadonlyArray<Record<string, unknown>>,
  filename: string,
  columns?: ReadonlyArray<string>,
): void {
  downloadFile(rowsToCsv(rows, columns), filename, 'text/csv');
}

export function downloadJson(payload: unknown, filename: string): void {
  downloadFile(JSON.stringify(payload, null, 2), filename, 'application/json');
}

/** Default report-filename builder: anomaly_report_{pair}_{tf}_{YYYYMMDDHHMM}.{ext} */
export function buildReportFilename(
  symbol: string,
  timeframe: string,
  ext: 'csv' | 'json',
): string {
  const ts = new Date()
    .toISOString()
    .replace(/[-:]/g, '')
    .replace(/\..+$/, '')
    .replace('T', '_');
  return `anomaly_report_${symbol}_${timeframe}_${ts}.${ext}`;
}
