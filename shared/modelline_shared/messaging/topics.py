"""Kafka topic constants and naming helpers.

Naming convention:
    cmd.<service>.<action>        — request that expects a reply
    reply.<requester>.<instance>  — private reply inbox per client instance
    events.<service>.<event>      — fire-and-forget domain events
"""
from __future__ import annotations

# ── Health (request/reply) ───────────────────────────────────────────────────
CMD_DATA_HEALTH = "cmd.data.health"
CMD_ANALYTICS_HEALTH = "cmd.analytics.health"

# ── data: dataset (Step 2) ───────────────────────────────────────────────────
CMD_DATA_DATASET_LIST_TABLES   = "cmd.data.dataset.list_tables"
CMD_DATA_DATASET_COVERAGE      = "cmd.data.dataset.coverage"
CMD_DATA_DATASET_ROWS          = "cmd.data.dataset.rows"
CMD_DATA_DATASET_EXPORT        = "cmd.data.dataset.export"
CMD_DATA_DATASET_EXPORT_FULL   = "cmd.data.dataset.export_full"
CMD_DATA_DATASET_INGEST        = "cmd.data.dataset.ingest"
CMD_DATA_DATASET_NORMALIZE_TF  = "cmd.data.dataset.normalize_timeframe"
CMD_DATA_DATASET_MAKE_TABLE    = "cmd.data.dataset.make_table_name"
CMD_DATA_DATASET_INSTRUMENT    = "cmd.data.dataset.instrument_details"
CMD_DATA_DATASET_SCHEMA        = "cmd.data.dataset.table_schema"
CMD_DATA_DATASET_MISSING       = "cmd.data.dataset.find_missing"
CMD_DATA_DATASET_TIMESTAMPS    = "cmd.data.dataset.timestamps"
CMD_DATA_DATASET_CONSTANTS     = "cmd.data.dataset.constants"
CMD_DATA_DATASET_DELETE_ROWS   = "cmd.data.dataset.delete_rows"
CMD_DATA_DATASET_IMPORT_CSV    = "cmd.data.dataset.import_csv"
CMD_DATA_DATASET_UPSERT_OHLCV  = "cmd.data.dataset.upsert_ohlcv"

# ── data: dataset inspection / anomaly / clean ───────────────────────────────
CMD_DATA_DATASET_COLUMN_STATS      = "cmd.data.dataset.column_stats"
CMD_DATA_DATASET_COLUMN_HISTOGRAM  = "cmd.data.dataset.column_histogram"
CMD_DATA_DATASET_BROWSE            = "cmd.data.dataset.browse"
CMD_DATA_DATASET_COMPUTE_FEATURES  = "cmd.data.dataset.compute_features"
CMD_DATA_DATASET_DETECT_ANOMALIES  = "cmd.data.dataset.detect_anomalies"
CMD_DATA_DATASET_CLEAN_PREVIEW     = "cmd.data.dataset.clean.preview"
CMD_DATA_DATASET_CLEAN_APPLY       = "cmd.data.dataset.clean.apply"
CMD_DATA_DATASET_AUDIT_LOG         = "cmd.data.dataset.audit_log"

# ── analitic: dataset session + anomaly (Python-side) ────────────────────────
CMD_ANALITIC_DATASET_LOAD     = "cmd.analitic.dataset.load"
CMD_ANALITIC_DATASET_UNLOAD   = "cmd.analitic.dataset.unload"
CMD_ANALITIC_DATASET_STATUS   = "cmd.analitic.dataset.status"
CMD_ANALITIC_ANOMALY_DBSCAN            = "cmd.analitic.anomaly.dbscan"
CMD_ANALITIC_ANOMALY_ISOLATION_FOREST  = "cmd.analitic.anomaly.isolation_forest"
CMD_ANALITIC_DATASET_DISTRIBUTION      = "cmd.analitic.dataset.distribution"

# ── analitic: dataset quality audit + repair operations ──────────────────────
CMD_ANALITIC_DATASET_QUALITY_CHECK       = "cmd.analitic.dataset.quality_check"
CMD_ANALITIC_DATASET_LOAD_OHLCV          = "cmd.analitic.dataset.load_ohlcv"
CMD_ANALITIC_DATASET_RECOMPUTE_FEATURES  = "cmd.analitic.dataset.recompute_features"

EVT_ANALITIC_DATASET_REPAIR_PROGRESS     = "events.analitic.dataset.repair.progress"

CMD_DATA_DB_PING               = "cmd.data.db.ping"

EVT_DATA_DATASET_UPDATED       = "events.data.dataset.updated"
EVT_DATA_INGEST_PROGRESS       = "events.data.ingest.progress"

# ── analytics (Step 3) ───────────────────────────────────────────────────────
CMD_ANALYTICS_TRAIN_START      = "cmd.analytics.train.start"
CMD_ANALYTICS_TRAIN_STATUS     = "cmd.analytics.train.status"
CMD_ANALYTICS_MODEL_LIST       = "cmd.analytics.model.list"
CMD_ANALYTICS_MODEL_LOAD       = "cmd.analytics.model.load"
CMD_ANALYTICS_PREDICT          = "cmd.analytics.predict"

EVT_ANALYTICS_TRAIN_PROGRESS   = "events.analytics.train.progress"
EVT_ANALYTICS_MODEL_READY      = "events.analytics.model.ready"


def reply_inbox(service: str, instance_id: str) -> str:
    """Return the private reply topic name for a given service+instance."""
    return f"reply.{service}.{instance_id}"
