import 'server-only';

import {
  appendQueueHistoryRow,
  clearQueueHistoryRows,
  readQueueHistoryRows,
} from '@/lib/sqliteStore';

export type QueueHistoryLevel = 'success' | 'error';

export interface QueueHistoryEntry {
  id: string;
  ts: string;
  topic: string;
  level: QueueHistoryLevel;
  durationMs: number;
  splitMode: boolean;
  payloadSummary?: Record<string, unknown> | null;
  responseSummary?: Record<string, unknown> | null;
  message?: string | null;
  code?: string | null;
  detail?: string | null;
  correlationId?: string | null;
}

export async function readQueueHistory(limit = 200): Promise<QueueHistoryEntry[]> {
  return readQueueHistoryRows(limit);
}

export async function appendQueueHistory(entry: QueueHistoryEntry): Promise<void> {
  appendQueueHistoryRow({
    ...entry,
    payloadSummary: entry.payloadSummary ?? null,
    responseSummary: entry.responseSummary ?? null,
    message: entry.message ?? null,
    code: entry.code ?? null,
    detail: entry.detail ?? null,
    correlationId: entry.correlationId ?? null,
  });
}

export async function clearQueueHistory(): Promise<void> {
  clearQueueHistoryRows();
}