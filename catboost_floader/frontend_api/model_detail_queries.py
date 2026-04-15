from __future__ import annotations

from catboost_floader.diagnostics.model_snapshot import build_model_snapshots

from catboost_floader.frontend_api.dto import ModelDetailDTO


def get_model_detail(model_key: str) -> ModelDetailDTO | None:
    snapshot = dict(build_model_snapshots().get(model_key, {}) or {})
    if not snapshot:
        return None
    return ModelDetailDTO(
        model_key=str(snapshot.get("model_key", model_key)),
        model_name=str(snapshot.get("model_name", model_key)),
        is_main=bool(snapshot.get("is_main", False)),
        summary=dict(snapshot.get("summary", {}) or {}),
        raw_model=dict(snapshot.get("raw_model", {}) or {}),
        overfitting=dict(snapshot.get("overfitting", {}) or {}),
        robustness=dict(snapshot.get("robustness", {}) or {}),
        selection=dict(snapshot.get("selection", {}) or {}),
        registry=dict(snapshot.get("registry", {}) or {}),
        artifact_paths=dict(snapshot.get("artifact_paths", {}) or {}),
        artifacts=dict(snapshot.get("artifacts", {}) or {}),
    )