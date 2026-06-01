// ── Backend response shapes ──────────────────────────────────────────────────

export interface ColumnStat {
  name: string;
  dtype: string;
  non_null: number;
  null_count: number;
  null_pct: number;
  min: number | null;
  max: number | null;
  mean: number | null;
  std: number | null;
}

export interface ColumnStatsResponse {
  table: string;
  total_rows: number;
  columns: ColumnStat[];
  error?: string;
}

export interface HistogramBucket {
  range_start: number;
  range_end: number;
  count: number;
}

export interface HistogramResponse {
  column: string;
  min: number | null;
  max: number | null;
  buckets: HistogramBucket[];
  error?: string;
}

export interface BrowseResponse {
  table: string;
  page: number;
  page_size: number;
  /**
   * Exact COUNT(*). Source of truth for pagination math.
   * Only present when the backend computed it (typically page 0).
   * Once received, the UI must pin this value and IGNORE the estimate
   * across subsequent pages — exact never gets overwritten by estimate.
   */
  total_rows: number | null;
  /**
   * pg_class.reltuples — informational only. May lag by a few percent.
   * Must NOT drive page-count math or button availability.
   */
  total_rows_estimate?: number | null;
  total_rows_known?: boolean;
  rows: Record<string, unknown>[];
  error?: string;
}

export interface TimeSeriesPoint {
  timestamp_ms: number;
  value: number | null;
  min?: number | null;
  max?: number | null;
  count?: number | null;
}

export interface TimeSeriesResponse {
  table: string;
  column: string;
  max_points: number;
  source_rows: number;
  start_ms: number | null;
  end_ms: number | null;
  downsampled: boolean;
  points: TimeSeriesPoint[];
  error?: string;
}

// ── Anomaly + clean response shapes ─────────────────────────────────────────

export interface AnomalyRow {
  ts_ms: number;
  anomaly_type: string;
  severity: 'critical' | 'warning';
  column: string | null;
  value: number | null;
  details: string | null;
}

export interface DetectAnomaliesResponse {
  table: string;
  total: number;
  critical: number;
  warning: number;
  by_type: Record<string, number>;
  /** Populated only when page/page_size are given in the request. */
  rows?: AnomalyRow[] | null;
  /** Up-to-200-row priority sample always returned by DataService. */
  sample?: AnomalyRow[];
  report_url?: string;
  has_more?: boolean;
  page?: number;
  page_size?: number;
  error?: string;
}

export interface CleanPreviewResponse {
  table: string;
  counts: {
    drop_duplicates:      number;
    fix_ohlc:             number;
    fill_zero_streaks:    number;
    delete_by_timestamps: number;
    fill_gaps:            number;
  };
  error?: string;
}

export interface CleanApplyResponse {
  table: string;
  audit_id: number;
  rows_affected: Record<string, number>;
  total: number;
  error?: string;
}

export interface DatasetStatusResponse {
  loaded: boolean;
  symbol?: string;
  timeframe?: string;
  table_name?: string;
  row_count?: number;
  memory_mb_on_disk?: number;
  loaded_at?: number;
  error?: string;
}

export interface DbscanResponse {
  summary?: {
    total_rows:  number;
    sample_size: number;
    n_clusters:  number;
    n_anomalies: number;
    eps:         number;
    min_samples: number;
    columns:     string[];
  };
  anomaly_timestamps_ms?: number[];
  error?: string;
}

export interface IForestResponse {
  summary?: {
    total_rows:    number;
    sample_size:   number;
    n_anomalies:   number;
    contamination: number;
    n_estimators:  number;
    columns:       string[];
  };
  anomaly_timestamps_ms?: number[];
  error?: string;
}

export interface DistributionBin { x: number; count: number; normal: number }
export interface DistributionResponse {
  column?:   string;
  n?:        number;
  mean?:     number;
  std?:      number;
  skewness?: number;
  kurtosis?: number;
  jb_stat?:  number;
  jb_p?:     number;
  verdict?:  string;
  bins?:     DistributionBin[];
  error?:    string;
}

export interface AuditLogEntry {
  id: number;
  table_name: string;
  operation: string;
  params: string;
  rows_affected: number;
  applied_at_ms: number;
}
export interface AuditLogResponse { entries: AuditLogEntry[]; error?: string }

// ── Severity ranking for Smart Suggestions ──────────────────────────────────
//
// Map each backend anomaly_type → (rank, recommendedOp). Lower rank = higher
// priority (critical first). The recommendedOp is a Clean-section checkbox
// key that the "Apply" button will toggle on before triggering Apply.

export type CleanOpKey = 'drop_duplicates' | 'fix_ohlc' | 'fill_zero_streaks'
                | 'delete_by_timestamps' | 'fill_gaps';

export interface AnomalyTypeMeta {
  rank: number;          // 0 = critical, 1 = warning, 2 = info
  recommendedOp?: CleanOpKey;
}
