"""Реестр моделей (registry.json): регистрация/чтение/удаление версий.

Выделено из ``report.py`` без изменения логики. Публичные имена ре-экспортируются
из ``report`` для обратной совместимости импортов.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from backend.utils import to_json_safe as _to_json_safe

from .config import MODELS_DIR

_LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Реестр моделей (registry.json)
# ---------------------------------------------------------------------------

_REGISTRY_FILE = "registry.json"


def _registry_path(models_dir: Path) -> Path:
    return models_dir / _REGISTRY_FILE


def register_model_version(
    prefix: str,
    metrics: dict,
    best_params: dict,
    feature_cols: list[str],
    *,
    models_dir: Path = MODELS_DIR,
    mlflow_run_id: "str | None" = None,
    target_col: "str | None" = None,
    n_train: int = 0,
    n_test: int = 0,
) -> str:
    """Добавляет запись о новой версии модели в registry.json.

    Каждая запись содержит:
        version_id  — уникальный ID вида ``{prefix}_{YYYYmmdd_HHMMSS}``
        prefix, trained_at, target_col, n_train, n_test,
        metrics (числовые), best_params, n_features, mlflow_run_id.

    Возвращает version_id добавленной записи.
    Реестр хранится в ``models_dir/registry.json`` (список записей, новейшие первые).
    """
    import datetime as _dt

    now_str = _dt.datetime.now(_dt.UTC).strftime("%Y%m%d_%H%M%S")
    version_id = f"{prefix}_{now_str}"

    entry: dict = {
        "version_id":    version_id,
        "prefix":        prefix,
        "trained_at":    _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "target_col":    target_col,
        "n_train":       n_train,
        "n_test":        n_test,
        "n_features":    len(feature_cols),
        "metrics":       {k: v for k, v in _to_json_safe(metrics).items() if isinstance(v, (int, float))},
        "best_params":   _to_json_safe(best_params),
        "mlflow_run_id": mlflow_run_id,
    }

    reg_path = _registry_path(models_dir)
    models_dir.mkdir(parents=True, exist_ok=True)

    registry: list[dict] = []
    if reg_path.exists():
        try:
            registry = json.loads(reg_path.read_text(encoding="utf-8"))
            if not isinstance(registry, list):
                registry = []
        except Exception:
            registry = []

    registry.insert(0, entry)
    reg_path.write_text(json.dumps(registry, indent=2, ensure_ascii=False), encoding="utf-8")
    _LOG.info("[registry] version_id=%s  mlflow_run_id=%s", version_id, mlflow_run_id)
    return version_id


def load_registry(
    *,
    models_dir: Path = MODELS_DIR,
    prefix_filter: "str | None" = None,
    limit: int = 50,
) -> list[dict]:
    """Загружает реестр моделей из registry.json.

    Parameters
    ----------
    prefix_filter:  если задан, возвращает только записи с совпадающим prefix.
    limit:          максимальное количество возвращаемых записей (новейшие первые).

    Возвращает список словарей (может быть пустым).
    """
    reg_path = _registry_path(models_dir)
    if not reg_path.exists():
        return []
    try:
        registry: list[dict] = json.loads(reg_path.read_text(encoding="utf-8"))
        if not isinstance(registry, list):
            return []
        if prefix_filter:
            registry = [e for e in registry if e.get("prefix") == prefix_filter]
        return registry[:limit]
    except Exception:
        return []


def delete_registry_version(
    version_id: str,
    *,
    models_dir: Path = MODELS_DIR,
) -> bool:
    """Удаляет запись с указанным version_id из реестра.

    Возвращает True если запись была найдена и удалена, False иначе.
    """
    reg_path = _registry_path(models_dir)
    if not reg_path.exists():
        return False
    try:
        registry: list[dict] = json.loads(reg_path.read_text(encoding="utf-8"))
        before = len(registry)
        registry = [e for e in registry if e.get("version_id") != version_id]
        if len(registry) == before:
            return False
        reg_path.write_text(json.dumps(registry, indent=2, ensure_ascii=False), encoding="utf-8")
        return True
    except Exception:
        return False
