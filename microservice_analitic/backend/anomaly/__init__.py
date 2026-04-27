"""Anomaly + dataset-session subpackage for microservice_analitic.

The package owns one process-wide :class:`DatasetSession` that holds metadata
about the currently loaded dataset (symbol/timeframe, parquet path, row count)
plus the DBSCAN handler that runs on top of it.
"""
from .session import DatasetSession, get_session, MAX_SESSION_ROWS

__all__ = ["DatasetSession", "get_session", "MAX_SESSION_ROWS"]
