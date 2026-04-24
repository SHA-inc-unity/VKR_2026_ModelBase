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
CMD_DATA_DATASET_INGEST        = "cmd.data.dataset.ingest"
CMD_DATA_DATASET_NORMALIZE_TF  = "cmd.data.dataset.normalize_timeframe"
CMD_DATA_DATASET_MAKE_TABLE    = "cmd.data.dataset.make_table_name"
CMD_DATA_DATASET_INSTRUMENT    = "cmd.data.dataset.instrument_details"
CMD_DATA_DATASET_SCHEMA        = "cmd.data.dataset.table_schema"
CMD_DATA_DATASET_MISSING       = "cmd.data.dataset.find_missing"
CMD_DATA_DATASET_TIMESTAMPS    = "cmd.data.dataset.timestamps"
CMD_DATA_DATASET_CONSTANTS     = "cmd.data.dataset.constants"

CMD_DATA_DB_PING               = "cmd.data.db.ping"

EVT_DATA_DATASET_UPDATED       = "events.data.dataset.updated"

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
