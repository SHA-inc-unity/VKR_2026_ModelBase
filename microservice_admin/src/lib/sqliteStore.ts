import 'server-only';

import fs from 'node:fs';
import path from 'node:path';

interface SqliteStatement {
  get(...params: unknown[]): Record<string, unknown> | undefined;
  all(...params: unknown[]): Record<string, unknown>[];
  run(...params: unknown[]): { changes?: number; lastInsertRowid?: number | bigint };
}

interface SqliteDatabase {
  exec(sql: string): void;
  prepare(sql: string): SqliteStatement;
}

const { DatabaseSync } = require('node:sqlite') as {
  DatabaseSync: new (location: string) => SqliteDatabase;
};

const DB_PATH = process.env.SQLITE_DB_PATH?.trim().length
  ? process.env.SQLITE_DB_PATH.trim()
  : path.join(process.cwd(), '.runtime-data', 'admin-state.sqlite');

const MAX_QUEUE_HISTORY = 5000;

let db: SqliteDatabase | null = null;

function getDb(): SqliteDatabase {
  if (db) return db;

  fs.mkdirSync(path.dirname(DB_PATH), { recursive: true });

  db = new DatabaseSync(DB_PATH);
  db.exec(`
    PRAGMA journal_mode = WAL;
    PRAGMA synchronous = NORMAL;
    PRAGMA busy_timeout = 5000;

    CREATE TABLE IF NOT EXISTS kv_store (
      key TEXT PRIMARY KEY,
      value TEXT NOT NULL,
      expires_at_ms INTEGER NULL,
      updated_at_ms INTEGER NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_kv_store_expires_at
      ON kv_store (expires_at_ms);

    CREATE TABLE IF NOT EXISTS queue_history (
      id TEXT PRIMARY KEY,
      ts TEXT NOT NULL,
      topic TEXT NOT NULL,
      level TEXT NOT NULL,
      duration_ms INTEGER NOT NULL DEFAULT 0,
      split_mode INTEGER NOT NULL DEFAULT 0,
      payload_summary TEXT NULL,
      response_summary TEXT NULL,
      message TEXT NULL,
      code TEXT NULL,
      detail TEXT NULL,
      correlation_id TEXT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_queue_history_ts
      ON queue_history (ts DESC);
  `);

  return db;
}

function cleanupExpiredValues(): void {
  getDb()
    .prepare('DELETE FROM kv_store WHERE expires_at_ms IS NOT NULL AND expires_at_ms <= ?')
    .run(Date.now());
}

export function readStoredValue(key: string): string | null {
  cleanupExpiredValues();
  const row = getDb()
    .prepare('SELECT value FROM kv_store WHERE key = ?')
    .get(key) as { value?: string } | undefined;
  return typeof row?.value === 'string' ? row.value : null;
}

export function writeStoredValue(key: string, value: string, ttlSeconds?: number | null): void {
  const expiresAtMs = typeof ttlSeconds === 'number' && ttlSeconds > 0
    ? Date.now() + ttlSeconds * 1000
    : null;

  getDb()
    .prepare(`
      INSERT INTO kv_store (key, value, expires_at_ms, updated_at_ms)
      VALUES (?, ?, ?, ?)
      ON CONFLICT(key) DO UPDATE SET
        value = excluded.value,
        expires_at_ms = excluded.expires_at_ms,
        updated_at_ms = excluded.updated_at_ms
    `)
    .run(key, value, expiresAtMs, Date.now());
}

export interface QueueHistoryRow {
  id: string;
  ts: string;
  topic: string;
  level: 'success' | 'error';
  durationMs: number;
  splitMode: boolean;
  payloadSummary: Record<string, unknown> | null;
  responseSummary: Record<string, unknown> | null;
  message: string | null;
  code: string | null;
  detail: string | null;
  correlationId: string | null;
}

export interface QueueHistoryPage {
  items: QueueHistoryRow[];
  total: number;
  errorCount: number;
  limit: number;
  offset: number;
}

function parseRecordJson(value: unknown): Record<string, unknown> | null {
  if (typeof value !== 'string' || value.length === 0) return null;
  try {
    const parsed = JSON.parse(value) as unknown;
    return typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : null;
  } catch {
    return null;
  }
}

function mapQueueHistoryRows(rows: Record<string, unknown>[]): QueueHistoryRow[] {
  return rows.map((row) => ({
    id: String(row.id),
    ts: String(row.ts),
    topic: String(row.topic),
    level: row.level === 'error' ? 'error' : 'success',
    durationMs: typeof row.duration_ms === 'number' ? row.duration_ms : 0,
    splitMode: row.split_mode === 1,
    payloadSummary: parseRecordJson(row.payload_summary),
    responseSummary: parseRecordJson(row.response_summary),
    message: typeof row.message === 'string' ? row.message : null,
    code: typeof row.code === 'string' ? row.code : null,
    detail: typeof row.detail === 'string' ? row.detail : null,
    correlationId: typeof row.correlation_id === 'string' ? row.correlation_id : null,
  }));
}

export function appendQueueHistoryRow(entry: QueueHistoryRow): void {
  const database = getDb();
  database.prepare(`
    INSERT INTO queue_history (
      id, ts, topic, level, duration_ms, split_mode,
      payload_summary, response_summary, message, code, detail, correlation_id
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(id) DO UPDATE SET
      ts = excluded.ts,
      topic = excluded.topic,
      level = excluded.level,
      duration_ms = excluded.duration_ms,
      split_mode = excluded.split_mode,
      payload_summary = excluded.payload_summary,
      response_summary = excluded.response_summary,
      message = excluded.message,
      code = excluded.code,
      detail = excluded.detail,
      correlation_id = excluded.correlation_id
  `).run(
    entry.id,
    entry.ts,
    entry.topic,
    entry.level,
    entry.durationMs,
    entry.splitMode ? 1 : 0,
    entry.payloadSummary ? JSON.stringify(entry.payloadSummary) : null,
    entry.responseSummary ? JSON.stringify(entry.responseSummary) : null,
    entry.message,
    entry.code,
    entry.detail,
    entry.correlationId,
  );

  database.prepare(`
    DELETE FROM queue_history
    WHERE id NOT IN (
      SELECT id FROM queue_history
      ORDER BY ts DESC
      LIMIT ?
    )
  `).run(MAX_QUEUE_HISTORY);
}

export function readQueueHistoryRows(limit = 200): QueueHistoryRow[] {
  const safeLimit = Math.min(Math.max(limit, 1), MAX_QUEUE_HISTORY);
  const rows = getDb()
    .prepare(`
      SELECT
        id,
        ts,
        topic,
        level,
        duration_ms,
        split_mode,
        payload_summary,
        response_summary,
        message,
        code,
        detail,
        correlation_id
      FROM queue_history
      ORDER BY ts DESC
      LIMIT ?
    `)
    .all(safeLimit);

  return mapQueueHistoryRows(rows);
}

export function readQueueHistoryPage(limit = 30, offset = 0): QueueHistoryPage {
  const safeLimit = Math.min(Math.max(limit, 1), MAX_QUEUE_HISTORY);
  const safeOffset = Math.max(offset, 0);
  const database = getDb();
  const totalRow = database
    .prepare('SELECT COUNT(*) AS total FROM queue_history')
    .get() as { total?: number } | undefined;
  const errorRow = database
    .prepare("SELECT COUNT(*) AS total FROM queue_history WHERE level='error'")
    .get() as { total?: number } | undefined;
  const rows = database
    .prepare(`
      SELECT
        id,
        ts,
        topic,
        level,
        duration_ms,
        split_mode,
        payload_summary,
        response_summary,
        message,
        code,
        detail,
        correlation_id
      FROM queue_history
      ORDER BY ts DESC
      LIMIT ? OFFSET ?
    `)
    .all(safeLimit, safeOffset);

  return {
    items: mapQueueHistoryRows(rows),
    total: typeof totalRow?.total === 'number' ? totalRow.total : 0,
    errorCount: typeof errorRow?.total === 'number' ? errorRow.total : 0,
    limit: safeLimit,
    offset: safeOffset,
  };
}

export function clearQueueHistoryRows(): void {
  getDb().prepare('DELETE FROM queue_history').run();
}