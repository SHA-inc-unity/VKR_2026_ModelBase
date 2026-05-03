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

// ── Quality-audit / repair progress (analitic → admin) ──────────────────────

export type RepairStageId = 'prepare' | 'fetch' | 'upsert' | 'recompute';

export interface RepairProgressEvent {
  correlation_id: string;
  stage: RepairStageId;
  label: string;
  status: 'running' | 'done' | 'error';
  progress: number;
  detail?: string;
}

export interface RepairStage {
  id: RepairStageId;
  label: string;
  status: 'pending' | 'running' | 'done' | 'error';
  progress: number;
  detail?: string;
}

// ── Dataset jobs (microservice_data → admin, Phase B/C) ─────────────────────

export type DatasetJobStatus =
  | 'queued' | 'running' | 'succeeded' | 'failed' | 'canceled' | 'skipped';

export type DatasetJobType =
  | 'ingest' | 'detect_anomalies' | 'compute_features'
  | 'clean_apply' | 'export' | 'import_csv' | 'upsert_ohlcv';

export interface DatasetJobProgressEvent {
  job_id: string;
  type: DatasetJobType;
  status: DatasetJobStatus;
  progress: number;        // 0..100
  stage?: string | null;
  detail?: string | null;
  target_table?: string | null;
  updated_at?: string;
}

export interface DatasetJobCompletedEvent {
  job_id: string;
  type: DatasetJobType;
  status: DatasetJobStatus; // succeeded | failed | canceled | skipped
  target_table?: string | null;
  target_timeframe?: string | null;
  completed?: number | null;  // rows written (ingest jobs)
  error_code?: string | null;
  error_message?: string | null;
  finished_at?: string;
}

export type QualityStatus = 'full' | 'partial' | 'missing';

export interface QualityGroupReport {
  id: string;
  label: string;
  columns: string[];
  fill_pct: number;
  status: QualityStatus;
  repair_action: 'load_ohlcv' | 'recompute_features';
}

export interface QualityReport {
  table: string;
  total_rows: number;
  groups: QualityGroupReport[];
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

