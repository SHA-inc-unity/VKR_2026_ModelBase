import numpy as np
import pandas as pd

from catboost_floader.diagnostics.overfitting_diagnostics import compute_direct_overfitting_diagnostics


class _FakeDirectModel:
    def __init__(self, preds_by_length):
        self._preds_by_length = {int(k): np.asarray(v, dtype=float) for k, v in preds_by_length.items()}

    def predict_details(self, X: pd.DataFrame):
        pred = self._preds_by_length[len(X)]
        return {"pred_return": np.asarray(pred, dtype=float)}


def _build_split(target_return: np.ndarray) -> tuple[pd.DataFrame, pd.DataFrame]:
    n = len(target_return)
    close = np.full(n, 100.0, dtype=float)
    X = pd.DataFrame(
        {
            "close": close,
            "feat": np.arange(n, dtype=float),
            "return_1": np.zeros(n, dtype=float),
        }
    )
    y = pd.DataFrame(
        {
            "target_return": np.asarray(target_return, dtype=float),
            "target_future_close": close * (1.0 + np.asarray(target_return, dtype=float)),
        }
    )
    return X, y


def test_overfitting_diagnostics_reports_none_for_stable_metrics():
    train_target = np.array([0.01, -0.01, 0.02, -0.02], dtype=float)
    val_target = np.array([0.01, -0.01, 0.015], dtype=float)

    X_train, y_train = _build_split(train_target)
    X_val, y_val = _build_split(val_target)

    model = _FakeDirectModel(
        {
            len(X_train): np.array([0.0095, -0.0105, 0.0195, -0.0205], dtype=float),
            len(X_val): np.array([0.0096, -0.0104, 0.0146], dtype=float),
        }
    )

    diagnostics = compute_direct_overfitting_diagnostics(
        direct_model=model,
        X_train_full=X_train,
        y_train=y_train,
        X_val_full=X_val,
        y_val=y_val,
        holdout_backtest_summary={
            "direct_model": {"MAE": 0.051, "sign_accuracy": 1.0},
            "direct_baselines": {"persistence": {"MAE": 1.0}},
        },
    )

    assert diagnostics["overfit_status"] == "none"
    assert diagnostics["overfit_reason"] == "within_thresholds"
    assert diagnostics["train_MAE"] is not None
    assert diagnostics["val_MAE"] is not None
    assert diagnostics["holdout_MAE"] is not None
    assert diagnostics["train_delta_vs_baseline"] is not None
    assert diagnostics["val_delta_vs_baseline"] is not None
    assert diagnostics["holdout_delta_vs_baseline"] is not None
    assert diagnostics["train_sign_acc_pct"] == 100.0
    assert diagnostics["val_sign_acc_pct"] == 100.0
    assert diagnostics["holdout_sign_acc_pct"] == 100.0


def test_overfitting_diagnostics_reports_severe_when_holdout_degrades():
    train_target = np.array([0.01, -0.01, 0.02, -0.02], dtype=float)
    val_target = np.array([0.01, -0.01, 0.015], dtype=float)

    X_train, y_train = _build_split(train_target)
    X_val, y_val = _build_split(val_target)

    model = _FakeDirectModel(
        {
            len(X_train): np.array([0.009, -0.011, 0.019, -0.021], dtype=float),
            len(X_val): np.array([0.0, 0.0, 0.0], dtype=float),
        }
    )

    diagnostics = compute_direct_overfitting_diagnostics(
        direct_model=model,
        X_train_full=X_train,
        y_train=y_train,
        X_val_full=X_val,
        y_val=y_val,
        holdout_backtest_summary={
            "direct_model": {"MAE": 0.5, "sign_accuracy": 0.25},
            "direct_baselines": {"persistence": {"MAE": 1.0}},
        },
    )

    assert diagnostics["overfit_status"] == "severe"
    assert str(diagnostics["overfit_reason"]).startswith("holdout_overfit_ratio")
    assert float(diagnostics["holdout_overfit_ratio"]) >= 1.3
    assert float(diagnostics["mae_gap_train_holdout"]) > 0.0
    assert float(diagnostics["sign_gap_train_holdout"]) > 0.0
    assert diagnostics["train_sign_acc_pct"] == 100.0
    assert diagnostics["holdout_sign_acc_pct"] == 25.0
