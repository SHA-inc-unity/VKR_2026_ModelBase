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

  // Anomaly / inspection
  CMD_DATA_DATASET_COLUMN_STATS:     'cmd.data.dataset.column_stats',
  CMD_DATA_DATASET_COLUMN_HISTOGRAM: 'cmd.data.dataset.column_histogram',
  CMD_DATA_DATASET_DETECT_ANOMALIES: 'cmd.data.dataset.detect_anomalies',
  CMD_DATA_DATASET_CLEAN:            'cmd.data.dataset.clean',

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
} as const;

export function replyInbox(service: string, instanceId: string): string {
  return `reply.${service}.${instanceId}`;
}
