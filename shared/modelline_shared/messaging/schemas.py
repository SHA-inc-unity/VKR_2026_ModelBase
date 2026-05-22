"""Pydantic schemas shared between services over Kafka."""
from __future__ import annotations

import time
import uuid
from typing import Any

from pydantic import BaseModel, Field


class Envelope(BaseModel):
    """Uniform message envelope for every Kafka payload.

    Fields:
        message_id     — unique id of this message
        correlation_id — links request ↔ reply (None for fire-and-forget events)
        reply_to       — Kafka topic the handler should answer on (only for commands)
        issued_at      — unix timestamp
        type           — short discriminator ("health", "health.reply", "dataset.coverage", ...)
        payload        — free-form dict; validated by handlers
    """

    message_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    correlation_id: str | None = None
    reply_to: str | None = None
    issued_at: float = Field(default_factory=time.time)
    type: str = "message"
    payload: dict[str, Any] = Field(default_factory=dict)


class HealthReply(BaseModel):
    """Typed payload returned by any `cmd.*.health` handler."""

    status: str
    service: str
    version: str
