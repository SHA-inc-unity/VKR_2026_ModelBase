import 'server-only';

export type AdminRuntimeLogLevel = 'info' | 'success' | 'warn' | 'error';

export interface AdminRuntimeLogEntry {
  id: number;
  ts: string;
  level: AdminRuntimeLogLevel;
  source: string;
  event: string;
  message?: string;
  fields?: Record<string, unknown>;
}

interface AdminRuntimeLogStore {
  nextId: number;
  entries: AdminRuntimeLogEntry[];
}

const STORE_KEY = '__modelline_admin_runtime_logs__';
const MAX_LOGS = 500;

type GlobalWithAdminLogs = typeof globalThis & {
  [STORE_KEY]?: AdminRuntimeLogStore;
};

function store(): AdminRuntimeLogStore {
  const globalStore = globalThis as GlobalWithAdminLogs;
  if (!globalStore[STORE_KEY]) {
    globalStore[STORE_KEY] = { nextId: 1, entries: [] };
  }
  return globalStore[STORE_KEY];
}

function sanitize(value: unknown, depth = 0): unknown {
  if (value === null || value === undefined) return value;
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return value;
  }
  if (value instanceof Date) return value.toISOString();
  if (depth >= 3) return String(value);
  if (Array.isArray(value)) return value.slice(0, 20).map(item => sanitize(item, depth + 1));
  if (typeof value === 'object') {
    const output: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(value as Record<string, unknown>).slice(0, 40)) {
      output[key] = sanitize(item, depth + 1);
    }
    return output;
  }
  return String(value);
}

export function writeAdminRuntimeLog(entry: Omit<AdminRuntimeLogEntry, 'id' | 'ts'>) {
  const logStore = store();
  logStore.entries.push({
    ...entry,
    id: logStore.nextId,
    ts: new Date().toISOString(),
    fields: entry.fields ? sanitize(entry.fields) as Record<string, unknown> : undefined,
  });
  logStore.nextId += 1;
  if (logStore.entries.length > MAX_LOGS) {
    logStore.entries.splice(0, logStore.entries.length - MAX_LOGS);
  }
}

export function readAdminRuntimeLogs(limit = 200): AdminRuntimeLogEntry[] {
  return store().entries.slice(-limit).reverse();
}

export function clearAdminRuntimeLogs() {
  store().entries = [];
}