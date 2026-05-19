import 'server-only';

import { cacheGet, cacheSet } from '@/lib/redisCache';

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

const STORAGE_KEY = 'modelline:queue-history:v1';
const STORAGE_TTL_SECONDS = 60 * 60 * 24 * 30;
const MAX_ENTRIES = 400;

let entries: QueueHistoryEntry[] = [];
let hydrated = false;
let hydratePromise: Promise<void> | null = null;

function normalizeEntry(value: unknown): QueueHistoryEntry | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null;

  const record = value as Record<string, unknown>;
  if (typeof record.id !== 'string' || typeof record.ts !== 'string' || typeof record.topic !== 'string') {
    return null;
  }

  const level = record.level === 'error' ? 'error' : 'success';
  return {
    id: record.id,
    ts: record.ts,
    topic: record.topic,
    level,
    durationMs: typeof record.durationMs === 'number' ? record.durationMs : 0,
    splitMode: record.splitMode === true,
    payloadSummary: isRecord(record.payloadSummary) ? record.payloadSummary : null,
    responseSummary: isRecord(record.responseSummary) ? record.responseSummary : null,
    message: typeof record.message === 'string' ? record.message : null,
    code: typeof record.code === 'string' ? record.code : null,
    detail: typeof record.detail === 'string' ? record.detail : null,
    correlationId: typeof record.correlationId === 'string' ? record.correlationId : null,
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

async function persistEntries(): Promise<void> {
  await cacheSet(STORAGE_KEY, JSON.stringify(entries), STORAGE_TTL_SECONDS);
}

async function ensureHydrated(): Promise<void> {
  if (hydrated) return;
  if (hydratePromise) return hydratePromise;

  hydratePromise = (async () => {
    const raw = await cacheGet(STORAGE_KEY);
    if (!raw) {
      hydrated = true;
      return;
    }

    try {
      const parsed = JSON.parse(raw) as unknown;
      if (Array.isArray(parsed)) {
        entries = parsed
          .map(normalizeEntry)
          .filter((entry): entry is QueueHistoryEntry => entry !== null)
          .slice(0, MAX_ENTRIES);
      }
    } catch {
      entries = [];
    } finally {
      hydrated = true;
    }
  })().finally(() => {
    hydratePromise = null;
  });

  return hydratePromise;
}

export async function readQueueHistory(limit = 200): Promise<QueueHistoryEntry[]> {
  await ensureHydrated();
  const safeLimit = Math.min(Math.max(limit, 1), MAX_ENTRIES);
  return entries.slice(0, safeLimit);
}

export async function appendQueueHistory(entry: QueueHistoryEntry): Promise<void> {
  await ensureHydrated();
  entries = [entry, ...entries.filter((item) => item.id !== entry.id)].slice(0, MAX_ENTRIES);
  await persistEntries();
}

export async function clearQueueHistory(): Promise<void> {
  await ensureHydrated();
  entries = [];
  await persistEntries();
}