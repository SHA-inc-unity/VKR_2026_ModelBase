"""Round-trip-тест: save_session_result → load_session_result сохраняет все поля."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backend.model.report import load_session_result, save_session_result


catboost = pytest.importorskip("catboost")
CatBoostRegressor = catboost.CatBoostRegressor


@pytest.fixture
def tiny_fitted_model():
    """Обучает минимальную CatBoost-модель на синтетических данных."""
    rng = np.random.default_rng(0)
    X = rng.normal(size=(60, 4))
    y = X[:, 0] * 0.3 - X[:, 1] * 0.2 + rng.normal(size=60) * 0.01
    model = CatBoostRegressor(
        iterations=10,
        depth=3,
        learning_rate=0.3,
        verbose=False,
        allow_writing_files=False,
    )
    model.fit(X, y)
    return model, X, y


def _make_timestamps(n: int) -> pd.Series:
    start = pd.Timestamp("2024-01-01", tz="UTC")
    return pd.Series(pd.date_range(start=start, periods=n, freq="h"))


def test_session_roundtrip_preserves_all_fields(tmp_path: Path, tiny_fitted_model):
    model, X, y = tiny_fitted_model
    y_pred = np.asarray(model.predict(X), dtype=float)
    y_test = pd.Series(y, name="target_return_1")
    ts_test = _make_timestamps(len(y))

    feature_cols = [f"f{i}" for i in range(X.shape[1])]
    metrics = {"MAE": 0.1, "RMSE": 0.2, "R2": 0.5, "Sharpe": 1.23}
    best_params = {"iterations": 10, "depth": 3, "learning_rate": 0.3}
    overfit = {"r2_gap": 0.05, "train_r2": 0.9, "test_r2": 0.85}

    save_session_result(
        model, metrics, y_pred, y_test, ts_test,
        feature_cols, best_params, overfit,
        output_dir=tmp_path, prefix="test_prefix",
    )

    assert (tmp_path / "test_prefix_session.cbm").exists()
    assert (tmp_path / "test_prefix_session.json").exists()
    assert (tmp_path / "test_prefix_session_arrays.npz").exists()

    loaded = load_session_result("test_prefix", models_dir=tmp_path)
    assert loaded is not None

    assert loaded["prefix"] == "test_prefix"
    assert loaded["metrics"] == metrics
    assert loaded["best_params"] == best_params
    assert loaded["feature_cols"] == feature_cols
    assert loaded["overfit_diagnostics"] == overfit

    np.testing.assert_allclose(loaded["y_pred"], y_pred)
    np.testing.assert_allclose(loaded["y_test"].to_numpy(), y_test.to_numpy())

    # Timestamps должны вернуться как datetime (UTC), без смещения в эпоху 1970.
    loaded_ts = loaded["ts_test"]
    assert pd.api.types.is_datetime64_any_dtype(loaded_ts)
    assert loaded_ts.iloc[0].year == 2024

    # Модель должна выдавать те же предсказания после загрузки.
    reloaded_pred = np.asarray(loaded["model"].predict(X), dtype=float)
    np.testing.assert_allclose(reloaded_pred, y_pred, rtol=1e-6)


def test_load_session_returns_none_when_files_missing(tmp_path: Path):
    assert load_session_result("nope", models_dir=tmp_path) is None


def test_session_roundtrip_without_overfit_diagnostics(tmp_path: Path, tiny_fitted_model):
    model, X, y = tiny_fitted_model
    y_pred = np.asarray(model.predict(X), dtype=float)
    y_test = pd.Series(y)
    ts_test = _make_timestamps(len(y))

    save_session_result(
        model, {"MAE": 0.1}, y_pred, y_test, ts_test,
        ["f0", "f1", "f2", "f3"], {"iterations": 10}, None,
        output_dir=tmp_path, prefix="noover",
    )
    loaded = load_session_result("noover", models_dir=tmp_path)
    assert loaded is not None
    assert loaded["overfit_diagnostics"] is None
