from __future__ import annotations

from typing import Any

from catboost_floader.frontend_api.action_requests import dispatch_action_request, get_action_catalog


def build_action_catalog(selected_model_key: str | None) -> dict[str, dict[str, Any]]:
    return get_action_catalog(selected_model_key)


def register_action_request(action_id: str, selected_model_key: str | None) -> dict[str, Any]:
    response = dispatch_action_request(action_id, selected_model_key)
    catalog = get_action_catalog(selected_model_key)
    action = dict(catalog.get(action_id, {}) or {})
    payload: dict[str, Any] = {
        "id": action_id,
        "label": action.get("label", action_id),
        "tone": response.tone,
        "mode": action.get("mode", "unknown"),
        "message": response.message,
        "control_path": action.get("control_path"),
        "job": response.job.to_dict() if response.job else None,
        "accepted": response.accepted,
    }
    if response.job is not None:
        payload["timestamp"] = response.job.created_at
    return payload