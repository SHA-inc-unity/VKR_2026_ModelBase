// Common TypeScript types for ModelLine Admin Panel

export interface ServiceHealth {
  status: 'ok' | 'error';
  service?: string;
  version?: string;
  error?: string;
}

export interface TableCoverage {
  table: string;
  exists: boolean;
  rows: number;
  min_ts_ms: number | null;
  max_ts_ms: number | null;
  status: string;
}

export interface TrainStatus {
  status: string;
  progress?: number;
  message?: string;
  model_id?: string;
}

export interface ModelInfo {
  model_id: string;
  symbol: string;
  timeframe: string;
  created_at: string;
  metrics?: Record<string, number>;
}

export interface PredictionRow {
  timestamp: number;
  predicted_value: number;
  confidence?: number;
}

export interface CoverageDetail {
  rows: number;
  expected: number;
  pct: number;
  gap_count: number;
}

export interface ExportResult {
  csv?: string;
  url?: string;
  filename?: string;
}

// ── SSE event payloads ────────────────────────────────────────────────────────

export interface TrainProgressEvent {
  symbol: string;
  timeframe: string;
  progress: number;   // 0.0–1.0
  step?: number;
  message?: string;
}

export interface ModelReadyEvent {
  model_id: string;
  symbol: string;
  timeframe: string;
}

// ── Ingest progress (microservice_data → admin) ─────────────────────────────

export type IngestStageId =
  | 'prepare'
  | 'fetch_klines'
  | 'fetch_funding'
  | 'fetch_oi'
  | 'compute_rsi'
  | 'upsert';

export interface IngestProgressEvent {
  correlation_id: string;
  stage: IngestStageId;
  label: string;
  status: 'running' | 'done' | 'error';
  progress: number;   // 0–100
  detail?: string;
}

export interface IngestStage {
  id: IngestStageId;
  label: string;
  status: 'pending' | 'running' | 'done' | 'error';
  progress: number;
  detail?: string;
}

// ── Infrastructure health (HTTP probes via /api/health) ───────────────────────

export interface InfraServiceHealth {
  status: 'online' | 'offline';
  error?: string;
}

export interface InfraHealthResponse {
  redpanda: InfraServiceHealth;
  minio:    InfraServiceHealth;
  account:  InfraServiceHealth;
  gateway:  InfraServiceHealth;
}

