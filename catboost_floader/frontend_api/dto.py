from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class ModelRegistryEntry:
    model_key: str
    model_name: str
    is_main: bool
    robustness_status: str | None = None
    selection_eligibility: bool | None = None
    delta_vs_baseline: float | None = None
    mean_delta_vs_baseline: float | None = None
    std_delta_vs_baseline: float | None = None
    win_rate_vs_baseline: float | None = None
    sign_acc_pct: float | None = None
    direction_acc_pct: float | None = None
    overfit_status: str | None = None
    overfit_reason: str | None = None
    raw_model_delta_vs_baseline: float | None = None
    raw_model_sign_acc_pct: float | None = None
    raw_model_direction_acc_pct: float | None = None
    raw_model_candidate_type: str | None = None
    raw_model_used_before_guard: bool | None = None
    guarded_candidate_type: str | None = None
    guarded_candidate_after_guard: bool | None = None
    recommendation_bucket: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DashboardOverviewDTO:
    total_models: int
    eligible_count: int
    robust_count: int
    positive_delta_count: int
    overfit_risk_count: int
    suppressed_edge_count: int
    main_model_key: str | None = None
    registry: list[ModelRegistryEntry] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["registry"] = [entry.to_dict() for entry in self.registry]
        return payload


@dataclass(frozen=True)
class ModelDetailDTO:
    model_key: str
    model_name: str
    is_main: bool
    summary: dict[str, Any]
    raw_model: dict[str, Any]
    overfitting: dict[str, Any]
    robustness: dict[str, Any]
    selection: dict[str, Any]
    registry: dict[str, Any]
    artifact_paths: dict[str, str]
    artifacts: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ComparisonRowDTO:
    model_key: str
    model_name: str
    fields: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        payload = {"model_key": self.model_key, "model_name": self.model_name}
        payload.update(self.fields)
        return payload