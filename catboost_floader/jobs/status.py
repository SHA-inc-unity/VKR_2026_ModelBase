from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    FINISHED = "finished"
    FAILED = "failed"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class JobRecord:
    job_id: str
    action_type: str
    status: str
    created_at: str
    label: str
    started_at: str | None = None
    finished_at: str | None = None
    target_model: str | None = None
    target_models: list[str] = field(default_factory=list)
    summary: str | None = None
    error_message: str | None = None
    log_path: str | None = None
    pid: int | None = None
    command: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "JobRecord":
        return cls(
            job_id=str(payload.get("job_id", "")),
            action_type=str(payload.get("action_type", "unknown")),
            status=str(payload.get("status", JobStatus.QUEUED.value)),
            created_at=str(payload.get("created_at", utc_now_iso())),
            label=str(payload.get("label", payload.get("action_type", "Job"))),
            started_at=payload.get("started_at"),
            finished_at=payload.get("finished_at"),
            target_model=payload.get("target_model"),
            target_models=list(payload.get("target_models", []) or []),
            summary=payload.get("summary"),
            error_message=payload.get("error_message"),
            log_path=payload.get("log_path"),
            pid=payload.get("pid"),
            command=list(payload.get("command", []) or []),
            metadata=dict(payload.get("metadata", {}) or {}),
            result=dict(payload.get("result", {}) or {}),
        )