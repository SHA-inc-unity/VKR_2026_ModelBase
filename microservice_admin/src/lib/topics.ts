// Kafka topic constants — mirrors Python shared/modelline_shared/messaging/topics.py
export const Topics = {
  // Health
  CMD_DATA_HEALTH: 'cmd.data.health',
  CMD_ANALYTICS_HEALTH: 'cmd.analytics.health',

  // Dataset
  CMD_DATA_DATASET_LIST_TABLES:  'cmd.data.dataset.list_tables',
  CMD_DATA_DATASET_COVERAGE:     'cmd.data.dataset.coverage',
  CMD_DATA_DATASET_ROWS:         'cmd.data.dataset.rows',
  CMD_DATA_DATASET_EXPORT:       'cmd.data.dataset.export',
  CMD_DATA_DATASET_INGEST:       'cmd.data.dataset.ingest',
  CMD_DATA_DATASET_NORMALIZE_TF: 'cmd.data.dataset.normalize_timeframe',
  CMD_DATA_DATASET_MAKE_TABLE:   'cmd.data.dataset.make_table_name',
  CMD_DATA_DATASET_INSTRUMENT:   'cmd.data.dataset.instrument_details',
  CMD_DATA_DATASET_SCHEMA:       'cmd.data.dataset.table_schema',
  CMD_DATA_DATASET_MISSING:      'cmd.data.dataset.find_missing',
  CMD_DATA_DATASET_TIMESTAMPS:   'cmd.data.dataset.timestamps',
  CMD_DATA_DATASET_CONSTANTS:    'cmd.data.dataset.constants',
  CMD_DATA_DATASET_DELETE_ROWS:  'cmd.data.dataset.delete_rows',
  CMD_DATA_DATASET_IMPORT_CSV:   'cmd.data.dataset.import_csv',
  CMD_DATA_DATASET_UPSERT_OHLCV: 'cmd.data.dataset.upsert_ohlcv',

  // Anomaly / inspection
  CMD_DATA_DATASET_COLUMN_STATS:     'cmd.data.dataset.column_stats',
  CMD_DATA_DATASET_COLUMN_HISTOGRAM: 'cmd.data.dataset.column_histogram',
  CMD_DATA_DATASET_BROWSE:           'cmd.data.dataset.browse',
  CMD_DATA_DATASET_COMPUTE_FEATURES: 'cmd.data.dataset.compute_features',
  CMD_DATA_DATASET_DETECT_ANOMALIES: 'cmd.data.dataset.detect_anomalies',
  CMD_DATA_DATASET_CLEAN_PREVIEW:    'cmd.data.dataset.clean.preview',
  CMD_DATA_DATASET_CLEAN_APPLY:      'cmd.data.dataset.clean.apply',
  CMD_DATA_DATASET_AUDIT_LOG:        'cmd.data.dataset.audit_log',

  // Background-job control plane (Phase A — runner lands in Phase B)
  CMD_DATA_DATASET_JOBS_START:  'cmd.data.dataset.jobs.start',
  CMD_DATA_DATASET_JOBS_CANCEL: 'cmd.data.dataset.jobs.cancel',
  CMD_DATA_DATASET_JOBS_GET:    'cmd.data.dataset.jobs.get',
  CMD_DATA_DATASET_JOBS_LIST:   'cmd.data.dataset.jobs.list',

  // Analitic-side dataset session + ML anomaly + distribution
  CMD_ANALITIC_DATASET_LOAD:              'cmd.analitic.dataset.load',
  CMD_ANALITIC_DATASET_UNLOAD:            'cmd.analitic.dataset.unload',
  CMD_ANALITIC_DATASET_STATUS:            'cmd.analitic.dataset.status',
  CMD_ANALITIC_ANOMALY_DBSCAN:            'cmd.analitic.anomaly.dbscan',
  CMD_ANALITIC_ANOMALY_ISOLATION_FOREST:  'cmd.analitic.anomaly.isolation_forest',
  CMD_ANALITIC_DATASET_DISTRIBUTION:      'cmd.analitic.dataset.distribution',

  // Analitic-side dataset quality audit + repair
  CMD_ANALITIC_DATASET_QUALITY_CHECK:      'cmd.analitic.dataset.quality_check',
  CMD_ANALITIC_DATASET_LOAD_OHLCV:         'cmd.analitic.dataset.load_ohlcv',
  CMD_ANALITIC_DATASET_RECOMPUTE_FEATURES: 'cmd.analitic.dataset.recompute_features',

  // DB
  CMD_DATA_DB_PING: 'cmd.data.db.ping',

  // Analytics
  CMD_ANALYTICS_TRAIN_START:  'cmd.analytics.train.start',
  CMD_ANALYTICS_TRAIN_STATUS: 'cmd.analytics.train.status',
  CMD_ANALYTICS_MODEL_LIST:   'cmd.analytics.model.list',
  CMD_ANALYTICS_MODEL_LOAD:   'cmd.analytics.model.load',
  CMD_ANALYTICS_PREDICT:      'cmd.analytics.predict',

  // Events
  EVT_ANALYTICS_TRAIN_PROGRESS: 'events.analytics.train.progress',
  EVT_ANALYTICS_MODEL_READY:    'events.analytics.model.ready',
  EVT_DATA_INGEST_PROGRESS:     'events.data.ingest.progress',
  EVT_ANALITIC_DATASET_REPAIR_PROGRESS: 'events.analitic.dataset.repair.progress',
  EVT_DATA_DATASET_JOB_PROGRESS:  'events.data.dataset.job.progress',
  EVT_DATA_DATASET_JOB_COMPLETED: 'events.data.dataset.job.completed',
} as const;

export function replyInbox(service: string, instanceId: string): string {
  return `reply.${service}.${instanceId}`;
}
