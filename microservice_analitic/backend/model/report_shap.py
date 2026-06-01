"""SHAP-анализ (CatBoost нативный, без внешнего пакета shap).

Выделено из ``report.py`` без изменения логики. Публичные имена ре-экспортируются
из ``report`` для обратной совместимости импортов.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from backend.dataset.core import log

from .config import MODELS_DIR

_LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SHAP-анализ (CatBoost нативный, без внешнего пакета shap)
# ---------------------------------------------------------------------------

def compute_shap_values(
    model: object,
    X: pd.DataFrame,
    feature_cols: list[str],
    *,
    max_samples: int = 2000,
    seed: int = 42,
) -> dict:
    """Вычисляет SHAP-значения через CatBoost get_feature_importance(type='ShapValues').

    Для ускорения используется случайная подвыборка до max_samples строк
    (без повторений, фиксированный seed → воспроизводимо).

    Возвращает словарь:
        shap_matrix — np.ndarray (n_samples, n_features), SHAP без bias-колонки
        mean_abs    — pd.Series индекс=feature_cols, значения=mean(|SHAP|)
        sample_X    — pd.DataFrame отобранной подвыборки
        bias        — float, baseline-предсказание модели (expected value)
        n_samples   — int, фактический размер подвыборки
    """
    import catboost as cb  # lazy import

    if len(X) == 0:
        raise ValueError("[shap] Пустой X — нечего объяснять")
    if len(feature_cols) != X.shape[1]:
        raise ValueError(
            f"[shap] feature_cols ({len(feature_cols)}) != колонок X ({X.shape[1]})"
        )

    n = len(X)
    if n > max_samples:
        rng = np.random.default_rng(seed)
        idx = np.sort(rng.choice(n, size=max_samples, replace=False))
        # Fancy-indexing already returns a fresh frame; an extra .copy() here
        # would just duplicate the SHAP sample for no reason.
        X_sample = X.iloc[idx]
    else:
        # Read-only path below (.values / .columns) — view is safe.
        X_sample = X

    pool = cb.Pool(data=X_sample.values, feature_names=list(X_sample.columns))
    raw = model.get_feature_importance(pool, type="ShapValues")  # type: ignore[attr-defined]
    raw = np.asarray(raw)
    if raw.ndim != 2 or raw.shape[1] != len(feature_cols) + 1:
        raise RuntimeError(
            f"[shap] Неожиданная форма ShapValues: {raw.shape}, "
            f"ожидалось (n, {len(feature_cols) + 1})"
        )
    shap_matrix = raw[:, :-1]
    bias        = float(raw[0, -1])

    mean_abs = pd.Series(
        np.abs(shap_matrix).mean(axis=0),
        index=feature_cols,
    ).sort_values(ascending=False)

    log(
        f"[report] SHAP: n_samples={len(X_sample)} bias={bias:.6f} "
        f"top={mean_abs.index[0]}={mean_abs.iloc[0]:.4f}"
    )
    return {
        "shap_matrix": shap_matrix,
        "mean_abs":    mean_abs,
        "sample_X":    X_sample,
        "bias":        bias,
        "n_samples":   len(X_sample),
    }


def save_shap_summary(
    shap_result: dict,
    *,
    output_dir: Path = MODELS_DIR,
    prefix: str = "catboost",
) -> Path:
    """Сохраняет сводку SHAP (mean |SHAP| по признакам) в {prefix}_shap_summary.csv."""
    from backend.csv_io import save_csv
    series = shap_result["mean_abs"]
    df = pd.DataFrame({
        "feature":       series.index.tolist(),
        "mean_abs_shap": series.values.tolist(),
    })
    path = save_csv(df, output_dir / f"{prefix}_shap_summary.csv")
    log(f"[report] SHAP summary → {path}")
    return path


def load_shap_summary(
    prefix: str,
    *,
    models_dir: Path = MODELS_DIR,
) -> "pd.Series | None":
    """Загружает сохранённую сводку SHAP. Возвращает pd.Series или None."""
    from backend.csv_io import load_csv, CsvLoadError
    path = models_dir / f"{prefix}_shap_summary.csv"
    try:
        df = load_csv(path, required_columns=["feature", "mean_abs_shap"])
    except CsvLoadError:
        return None
    if df is None:
        return None
    return pd.Series(df["mean_abs_shap"].values, index=df["feature"].values)
