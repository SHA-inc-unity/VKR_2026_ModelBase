"""Shared package across ModelLine microservices (data, analytics, admin).

Keeps the cross-service contract in one place:
- Pydantic schemas for REST request/response bodies
- Common constants (timeframes, column names)
- Small pure-Python utilities (no heavy deps)
"""

__version__ = "0.1.0"
