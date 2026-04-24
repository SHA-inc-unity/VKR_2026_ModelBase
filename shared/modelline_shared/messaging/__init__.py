"""Kafka-based messaging primitives used by every ModelLine service.

Public API:
    KafkaClient        — async producer + consumer + request/reply coordinator
    Envelope           — uniform Pydantic message envelope
    HealthReply        — typed reply for health commands
    Topics             — constants + helpers for topic names

The rule of this codebase: services communicate via Kafka ONLY. No HTTP
clients, no direct database sharing between services. Large binary
payloads (CSV, model files) go through the claim-check pattern: upload to
S3/MinIO, publish the URL over Kafka.
"""
from .client import KafkaClient
from .schemas import Envelope, HealthReply
from . import topics
from .topics import (
    CMD_ANALYTICS_HEALTH,
    CMD_DATA_HEALTH,
    reply_inbox,
)

__all__ = [
    "KafkaClient",
    "Envelope",
    "HealthReply",
    "topics",
    "CMD_DATA_HEALTH",
    "CMD_ANALYTICS_HEALTH",
    "reply_inbox",
]
