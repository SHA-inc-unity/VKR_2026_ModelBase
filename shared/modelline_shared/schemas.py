"""Pydantic schemas shared between services.

Populated in Step 2 (when REST endpoints of data-service are defined).
For now only a health-check schema used by all services.
"""
from __future__ import annotations

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
