"""Генерация отчётов: feature importance TOP-20, actual vs predicted, P&L curve, grid results.

Этот модуль сохранён как тонкий фасад: реализация разнесена по когезивным
подмодулям, а публичные имена ре-экспортируются здесь, чтобы существующие
импорты ``from backend.model.report import NAME`` продолжали работать без изменений.

Подмодули:
  report_charts.py    — графики (matplotlib): feature importance, actual vs predicted, P&L
  report_results.py   — grid-таблица, summary, results.json, predictions.json
  report_search.py    — сессии, Grid Search и Optuna (save/load best params и результатов)
  report_shap.py      — SHAP-анализ (CatBoost нативный)
  report_registry.py  — реестр версий моделей (registry.json)
"""
from __future__ import annotations

import logging

_LOG = logging.getLogger(__name__)

from .report_charts import (
    plot_actual_vs_predicted,
    plot_cumulative_pnl,
    plot_feature_importance,
)
from .report_registry import (
    _REGISTRY_FILE,
    _registry_path,
    delete_registry_version,
    load_registry,
    register_model_version,
)
from .report_results import (
    print_summary,
    save_grid_results,
    save_predictions_json,
    save_results_json,
)
from .report_search import (
    load_grid_best_params,
    load_grid_session_result,
    load_optuna_best_params,
    load_optuna_session_result,
    load_session_result,
    save_grid_best_params,
    save_optuna_best_params,
    save_optuna_results,
    save_session_result,
)
from .report_shap import (
    compute_shap_values,
    load_shap_summary,
    save_shap_summary,
)

__all__ = [
    "plot_feature_importance",
    "plot_actual_vs_predicted",
    "plot_cumulative_pnl",
    "save_grid_results",
    "print_summary",
    "save_results_json",
    "save_predictions_json",
    "save_grid_best_params",
    "load_grid_best_params",
    "save_session_result",
    "load_session_result",
    "load_grid_session_result",
    "save_optuna_results",
    "save_optuna_best_params",
    "load_optuna_best_params",
    "load_optuna_session_result",
    "compute_shap_values",
    "save_shap_summary",
    "load_shap_summary",
    "register_model_version",
    "load_registry",
    "delete_registry_version",
]
