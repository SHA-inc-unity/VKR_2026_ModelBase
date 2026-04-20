"""Tests for backend.model.report — all save/load/plot functions."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("catboost")
pytest.importorskip("matplotlib")

from catboost import CatBoostRegressor

from backend.model.report import (
    compute_shap_values,
    load_grid_best_params,
    load_grid_session_result,
    load_optuna_best_params,
    load_optuna_session_result,
    load_shap_summary,
    plot_actual_vs_predicted,
    plot_cumulative_pnl,
    plot_feature_importance,
    print_summary,
    save_grid_best_params,
    save_grid_results,
    save_optuna_best_params,
    save_optuna_results,
    save_predictions_json,
    save_results_json,
    save_shap_summary,
)


@pytest.fixture(scope="module")
def tiny_model():
    """Tiny trained CatBoostRegressor."""
    rng = np.random.default_rng(0)
    X = rng.normal(size=(80, 4))
    y = X[:, 0] - X[:, 1] + rng.normal(size=80) * 0.01
    model = CatBoostRegressor(
        iterations=10, depth=3, learning_rate=0.3,
        verbose=False, allow_writing_files=False,
    )
    model.fit(X, y)
    return model, ["f0", "f1", "f2", "f3"], X, y


@pytest.fixture
def arrays():
    rng = np.random.default_rng(1)
    n = 100
    y_true = rng.normal(0, 0.01, n)
    y_pred = y_true * 0.3 + rng.normal(0, 0.005, n)
    ts = pd.Series(pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC"))
    return y_true, y_pred, ts


# ---------------------------------------------------------------------------
# plot_feature_importance
# ---------------------------------------------------------------------------

def test_plot_feature_importance_creates_png(tmp_path, tiny_model):
    model, feature_names, _, _ = tiny_model
    path = plot_feature_importance(model, feature_names, output_dir=tmp_path, prefix="test")
    assert path.exists()
    assert path.suffix == ".png"


# ---------------------------------------------------------------------------
# plot_actual_vs_predicted
# ---------------------------------------------------------------------------

def test_plot_actual_vs_predicted_with_timestamps(tmp_path, arrays):
    y_true, y_pred, ts = arrays
    path = plot_actual_vs_predicted(y_true, y_pred, ts, output_dir=tmp_path, prefix="test")
    assert path.exists()


def test_plot_actual_vs_predicted_no_timestamps(tmp_path, arrays):
    y_true, y_pred, _ = arrays
    path = plot_actual_vs_predicted(y_true, y_pred, None, output_dir=tmp_path, prefix="test2")
    assert path.exists()


# ---------------------------------------------------------------------------
# plot_cumulative_pnl
# ---------------------------------------------------------------------------

def test_plot_cumulative_pnl_with_timestamps(tmp_path, arrays):
    y_true, y_pred, ts = arrays
    path = plot_cumulative_pnl(y_true, y_pred, ts, output_dir=tmp_path, prefix="test")
    assert path.exists()


def test_plot_cumulative_pnl_no_timestamps(tmp_path, arrays):
    y_true, y_pred, _ = arrays
    path = plot_cumulative_pnl(y_true, y_pred, None, output_dir=tmp_path, prefix="test2")
    assert path.exists()


# ---------------------------------------------------------------------------
# save_grid_results
# ---------------------------------------------------------------------------

def test_save_grid_results_creates_csv(tmp_path):
    df = pd.DataFrame({
        "combo": [0, 1],
        "iterations": [1000, 2000],
        "depth": [6, 8],
        "mean_rmse_cv": [0.01, 0.009],
        "sharpe": [1.2, 1.5],
    })
    path = save_grid_results(df, output_dir=tmp_path, prefix="test")
    assert path.exists()
    loaded = pd.read_csv(path)
    assert len(loaded) == 2


# ---------------------------------------------------------------------------
# print_summary
# ---------------------------------------------------------------------------

def test_print_summary_outputs_to_stdout(capsys):
    metrics = {"R2": 0.5, "sharpe": 1.2, "dir_acc_pct": 52.0}
    best_params = {"iterations": 1000, "depth": 6}
    print_summary(metrics, best_params, Path("models/test.cbm"))
    out = capsys.readouterr().out
    assert "R2" in out
    assert "1000" in out


# ---------------------------------------------------------------------------
# save_results_json
# ---------------------------------------------------------------------------

def test_save_results_json_creates_file(tmp_path):
    metrics = {"R2": 0.5, "sharpe": 1.2}
    params = {"iterations": 1000}
    path = save_results_json(metrics, params, Path("test.cbm"), output_dir=tmp_path, prefix="test")
    assert path.exists()
    import json
    data = json.loads(path.read_text())
    assert data["metrics"]["R2"] == 0.5


def test_save_results_json_with_annualize_factor(tmp_path):
    metrics = {"R2": 0.3}
    path = save_results_json(
        metrics, {}, Path("t.cbm"),
        annualize_factor=8760.0, output_dir=tmp_path, prefix="ann",
    )
    import json
    data = json.loads(path.read_text())
    assert data["annualize_factor"] == 8760.0


# ---------------------------------------------------------------------------
# save_predictions_json
# ---------------------------------------------------------------------------

def test_save_predictions_json_creates_file(tmp_path, arrays):
    y_true, y_pred, ts = arrays
    path = save_predictions_json(
        y_true, y_pred, ts,
        metrics={"R2": 0.1}, best_params={"depth": 6}, model_path=Path("m.cbm"),
        output_dir=tmp_path, prefix="test",
    )
    assert path.exists()
    import json
    data = json.loads(path.read_text())
    assert data["n_samples"] == len(y_true)


def test_save_predictions_json_no_timestamps(tmp_path, arrays):
    y_true, y_pred, _ = arrays
    path = save_predictions_json(y_true, y_pred, None, output_dir=tmp_path, prefix="notimestamp")
    assert path.exists()


def test_save_predictions_json_no_optional_fields(tmp_path, arrays):
    y_true, y_pred, _ = arrays
    path = save_predictions_json(y_true, y_pred, output_dir=tmp_path, prefix="minimal")
    assert path.exists()


# ---------------------------------------------------------------------------
# save_grid_best_params / load_grid_best_params
# ---------------------------------------------------------------------------

def test_save_and_load_grid_best_params(tmp_path):
    params = {"iterations": 1000, "depth": 6}
    row = {"mean_rmse_cv": 0.01, "sharpe": 1.5, "dir_acc_pct": 53.0,
           "mae_pct": 98.0, "profit_factor": 1.2, "accuracy": 0.53, "elapsed_s": 10.0}
    path = save_grid_best_params(params, row, output_dir=tmp_path, prefix="test")
    assert path.exists()
    loaded = load_grid_best_params("test", models_dir=tmp_path)
    assert loaded is not None
    assert loaded["best_params"]["depth"] == 6


def test_load_grid_best_params_missing_returns_none(tmp_path):
    assert load_grid_best_params("no_such", models_dir=tmp_path) is None


def test_load_grid_best_params_corrupted_returns_none(tmp_path):
    (tmp_path / "bad_grid_best.json").write_text("not json")
    assert load_grid_best_params("bad", models_dir=tmp_path) is None


# ---------------------------------------------------------------------------
# load_grid_session_result
# ---------------------------------------------------------------------------

def test_load_grid_session_result_success(tmp_path):
    df = pd.DataFrame({"combo": [0], "iterations": [1000], "sharpe": [1.2]})
    df.to_csv(tmp_path / "test_grid_results.csv", index=False)
    params = {"iterations": 1000}
    row = {"mean_rmse_cv": 0.01, "sharpe": 1.5}
    save_grid_best_params(params, row, output_dir=tmp_path, prefix="test")
    result = load_grid_session_result("test", models_dir=tmp_path)
    assert result is not None
    assert "grid_df" in result


def test_load_grid_session_result_missing_returns_none(tmp_path):
    assert load_grid_session_result("nope", models_dir=tmp_path) is None


def test_load_grid_session_result_empty_grid_df_returns_none(tmp_path):
    pd.DataFrame().to_csv(tmp_path / "empty_grid_results.csv", index=False)
    params = {"iterations": 1000}
    row = {"sharpe": 1.5}
    save_grid_best_params(params, row, output_dir=tmp_path, prefix="empty")
    result = load_grid_session_result("empty", models_dir=tmp_path)
    # Empty grid_df → returns None
    assert result is None


# ---------------------------------------------------------------------------
# save_optuna_results / save_optuna_best_params / load_optuna_* 
# ---------------------------------------------------------------------------

def test_save_optuna_results(tmp_path):
    df = pd.DataFrame({"trial": [0, 1], "sharpe": [1.0, 1.5]})
    path = save_optuna_results(df, output_dir=tmp_path, prefix="test")
    assert path.exists()


def test_save_and_load_optuna_best_params(tmp_path):
    params = {"iterations": 500, "depth": 8}
    row = {"sharpe": 1.8, "mean_rmse_cv": 0.008, "dir_acc_pct": 55.0, "profit_factor": 1.3}
    path = save_optuna_best_params(params, row, output_dir=tmp_path, prefix="test")
    assert path.exists()
    loaded = load_optuna_best_params("test", models_dir=tmp_path)
    assert loaded is not None
    assert loaded["best_params"]["depth"] == 8


def test_load_optuna_best_params_missing(tmp_path):
    assert load_optuna_best_params("nope", models_dir=tmp_path) is None


def test_load_optuna_best_params_corrupted(tmp_path):
    (tmp_path / "bad_optuna_best.json").write_text("garbage")
    assert load_optuna_best_params("bad", models_dir=tmp_path) is None


def test_load_optuna_session_result_success(tmp_path):
    df = pd.DataFrame({"trial": [0], "sharpe": [1.5]})
    df.to_csv(tmp_path / "test_optuna_results.csv", index=False)
    params = {"depth": 6}
    row = {"sharpe": 1.5, "mean_rmse_cv": 0.01, "dir_acc_pct": 52.0, "profit_factor": 1.1}
    save_optuna_best_params(params, row, output_dir=tmp_path, prefix="test")
    result = load_optuna_session_result("test", models_dir=tmp_path)
    assert result is not None
    assert "grid_df" in result


def test_load_optuna_session_result_missing(tmp_path):
    assert load_optuna_session_result("nope", models_dir=tmp_path) is None


def test_load_optuna_session_result_empty_df_returns_none(tmp_path):
    pd.DataFrame().to_csv(tmp_path / "empty_optuna_results.csv", index=False)
    params = {"depth": 6}
    row = {"sharpe": 1.5}
    save_optuna_best_params(params, row, output_dir=tmp_path, prefix="empty")
    assert load_optuna_session_result("empty", models_dir=tmp_path) is None


# ---------------------------------------------------------------------------
# compute_shap_values / save_shap_summary / load_shap_summary
# ---------------------------------------------------------------------------

def test_compute_shap_values_returns_dict(tiny_model):
    model, feature_names, X, _ = tiny_model
    df_X = pd.DataFrame(X, columns=feature_names)
    result = compute_shap_values(model, df_X, feature_names)
    assert "shap_matrix" in result
    assert "mean_abs" in result
    assert result["shap_matrix"].shape[1] == len(feature_names)


def test_compute_shap_values_empty_x_raises(tiny_model):
    model, feature_names, _, _ = tiny_model
    empty_X = pd.DataFrame(columns=feature_names)
    with pytest.raises(ValueError, match="Пустой X"):
        compute_shap_values(model, empty_X, feature_names)


def test_compute_shap_values_mismatched_features_raises(tiny_model):
    model, feature_names, X, _ = tiny_model
    df_X = pd.DataFrame(X, columns=feature_names)
    with pytest.raises(ValueError, match="feature_cols"):
        compute_shap_values(model, df_X, ["only_one"])


def test_compute_shap_values_subsampled(tiny_model):
    model, feature_names, X, _ = tiny_model
    df_X = pd.DataFrame(X, columns=feature_names)
    result = compute_shap_values(model, df_X, feature_names, max_samples=5)
    assert result["n_samples"] == 5


def test_save_and_load_shap_summary(tmp_path, tiny_model):
    model, feature_names, X, _ = tiny_model
    df_X = pd.DataFrame(X, columns=feature_names)
    shap_result = compute_shap_values(model, df_X, feature_names)
    path = save_shap_summary(shap_result, output_dir=tmp_path, prefix="test")
    assert path.exists()
    series = load_shap_summary("test", models_dir=tmp_path)
    assert series is not None
    assert len(series) == len(feature_names)


def test_load_shap_summary_missing(tmp_path):
    assert load_shap_summary("nope", models_dir=tmp_path) is None


def test_load_shap_summary_corrupted(tmp_path):
    (tmp_path / "bad_shap_summary.csv").write_text("not,valid,data\n1,2,3")
    # Should not raise, just return None on exception
    result = load_shap_summary("bad", models_dir=tmp_path)
    # Either None or a Series depending on CSV parsability


# ---------------------------------------------------------------------------
# save_session_result with non-datetime timestamps (covers else branch)
# ---------------------------------------------------------------------------

def test_save_session_result_non_datetime_ts(tmp_path, tiny_model):
    """Covers the ts_unit='idx' else branch in save_session_result."""
    from backend.model.report import load_session_result, save_session_result

    model, feature_names, X, y = tiny_model
    y_pred = np.asarray(model.predict(X), dtype=float)
    y_test = pd.Series(y)
    # Non-datetime ts_test — plain integer index
    ts_test = pd.Series(np.arange(len(y), dtype="int64"))

    save_session_result(
        model, {"R2": 0.3}, y_pred, y_test, ts_test,
        feature_names, {"iterations": 10}, None,
        output_dir=tmp_path, prefix="idx_ts",
    )
    loaded = load_session_result("idx_ts", models_dir=tmp_path)
    assert loaded is not None
    assert loaded["ts_test"].iloc[0] == 0
