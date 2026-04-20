"""Юнит-тесты для backend.model.metrics."""
from __future__ import annotations

import math

import numpy as np

from backend.model.metrics import (
    _build_confusion_matrix,
    _mcc_from_confusion,
    _per_class_prf,
    compute_direction_metrics,
    compute_metrics,
    compute_signal_metrics,
    compute_trading_metrics,
)


# ---------------------------------------------------------------------------
# compute_metrics
# ---------------------------------------------------------------------------

def test_compute_metrics_perfect_prediction():
    y = np.array([0.1, -0.2, 0.3, -0.05, 0.15])
    m = compute_metrics(y, y, annualize_factor=1.0)
    assert m["MAE"] == 0.0
    assert m["RMSE"] == 0.0
    assert m["R2"] == 1.0
    # Стратегия «знак прогноза» на идеальных предсказаниях = |y|, std > 0 → Sharpe > 0.
    assert m["Sharpe"] > 0.0


def test_compute_metrics_constant_truth_r2_zero():
    y_true = np.array([0.5, 0.5, 0.5, 0.5])
    y_pred = np.array([0.4, 0.6, 0.5, 0.5])
    m = compute_metrics(y_true, y_pred)
    # Деноминатор R² = 0 → функция возвращает 0.0 по контракту.
    assert m["R2"] == 0.0


def test_compute_metrics_mae_rmse_arithmetic():
    y_true = np.array([1.0, 2.0, 3.0])
    y_pred = np.array([1.0, 2.0, 5.0])
    m = compute_metrics(y_true, y_pred)
    # Ошибки: 0, 0, 2 → MAE=2/3, RMSE=sqrt(4/3)
    assert math.isclose(m["MAE"], 2.0 / 3.0, rel_tol=1e-9)
    assert math.isclose(m["RMSE"], math.sqrt(4.0 / 3.0), rel_tol=1e-9)


def test_compute_metrics_sharpe_zero_when_constant_strategy_returns():
    # y_pred положителен всюду → strategy = y_true; если y_true константа — std=0 → Sharpe=0.
    y_true = np.array([0.1, 0.1, 0.1, 0.1])
    y_pred = np.array([0.2, 0.2, 0.2, 0.2])
    m = compute_metrics(y_true, y_pred)
    assert m["Sharpe"] == 0.0


# ---------------------------------------------------------------------------
# compute_direction_metrics
# ---------------------------------------------------------------------------

def test_direction_metrics_confusion_counts():
    y_true = np.array([0.1, -0.1, 0.1, -0.1])
    y_pred = np.array([0.2, -0.2, -0.2, 0.2])  # TP, TN, FN, FP
    d = compute_direction_metrics(y_true, y_pred)
    assert d == {"TP": 1, "TN": 1, "FP": 1, "FN": 1, "accuracy": 0.5}


def test_direction_metrics_all_correct():
    y_true = np.array([1.0, -1.0, 2.0, -3.0])
    y_pred = np.array([0.5, -0.5, 0.1, -0.1])
    d = compute_direction_metrics(y_true, y_pred)
    assert d["accuracy"] == 1.0
    assert d["FP"] == 0 and d["FN"] == 0


# ---------------------------------------------------------------------------
# compute_trading_metrics
# ---------------------------------------------------------------------------

def test_trading_metrics_keys_and_types():
    rng = np.random.default_rng(42)
    y_true = rng.normal(size=200) * 0.01
    y_pred = y_true + rng.normal(size=200) * 0.002
    t = compute_trading_metrics(y_true, y_pred, annualize_factor=365.0)
    assert set(t.keys()) == {"sharpe", "dir_acc_pct", "mae_pct", "profit_factor"}
    for k, v in t.items():
        assert isinstance(v, float), f"{k} не float: {type(v)}"


def test_trading_metrics_profit_factor_huge_when_no_losses():
    # Все strategy_returns >= 0 → код подставляет gross_loss = 1e-12,
    # поэтому PF должен быть очень большим (но конечным) числом.
    y = np.array([0.01, 0.02, 0.03, 0.04])
    t = compute_trading_metrics(y, y)
    assert math.isfinite(t["profit_factor"])
    assert t["profit_factor"] > 1e9


def test_trading_metrics_dir_acc_half_on_opposite_signs():
    y_true = np.array([0.1, -0.1, 0.1, -0.1])
    y_pred = np.array([-0.1, 0.1, 0.1, -0.1])
    t = compute_trading_metrics(y_true, y_pred)
    assert math.isclose(t["dir_acc_pct"], 50.0, rel_tol=1e-9)


def test_trading_metrics_mae_pct_matches_definition():
    y_true = np.array([0.1, -0.1, 0.2])
    y_pred = np.array([0.0, 0.0, 0.0])
    t = compute_trading_metrics(y_true, y_pred)
    # MAE = mean(|y|), denom = mean(|y|) → MAE% = 100.
    assert math.isclose(t["mae_pct"], 100.0, rel_tol=1e-9)


# ---------------------------------------------------------------------------
# _build_confusion_matrix
# ---------------------------------------------------------------------------

def test_build_confusion_matrix_binary():
    y_true = np.array([-1, -1,  1,  1])
    y_pred = np.array([-1,  1, -1,  1])
    cm = _build_confusion_matrix(y_true, y_pred, labels=[-1, 1])
    # rows=actual, cols=predicted
    assert cm[0, 0] == 1  # TN: actual=-1, pred=-1
    assert cm[0, 1] == 1  # FP: actual=-1, pred=+1
    assert cm[1, 0] == 1  # FN: actual=+1, pred=-1
    assert cm[1, 1] == 1  # TP: actual=+1, pred=+1


def test_build_confusion_matrix_ignores_unknown_labels():
    y_true = np.array([-1, 99, 1])
    y_pred = np.array([-1, -1, 1])
    cm = _build_confusion_matrix(y_true, y_pred, labels=[-1, 1])
    # label 99 is not in labels list → ignored
    assert cm.sum() == 2


# ---------------------------------------------------------------------------
# _mcc_from_confusion
# ---------------------------------------------------------------------------

def test_mcc_from_confusion_perfect():
    # Perfect classifier: diagonal matrix → MCC = 1
    cm = np.array([[3, 0], [0, 3]])
    assert math.isclose(_mcc_from_confusion(cm), 1.0, rel_tol=1e-9)


def test_mcc_from_confusion_all_wrong():
    # All wrong: anti-diagonal → MCC = -1
    cm = np.array([[0, 3], [3, 0]])
    assert math.isclose(_mcc_from_confusion(cm), -1.0, rel_tol=1e-9)


def test_mcc_from_confusion_random():
    cm = np.array([[1, 1], [1, 1]])
    # TP=1 TN=1 FP=1 FN=1 → MCC=0
    assert math.isclose(_mcc_from_confusion(cm), 0.0, abs_tol=1e-9)


def test_mcc_from_confusion_empty():
    cm = np.zeros((2, 2), dtype=int)
    assert _mcc_from_confusion(cm) == 0.0


# ---------------------------------------------------------------------------
# _per_class_prf
# ---------------------------------------------------------------------------

def test_per_class_prf_perfect():
    cm = np.array([[2, 0], [0, 3]])
    result = _per_class_prf(cm, labels=[-1, 1])
    short_cls = result[0]
    long_cls  = result[1]
    assert short_cls["precision"] == 1.0
    assert short_cls["recall"]    == 1.0
    assert long_cls["f1"]         == 1.0
    assert long_cls["support"]    == 3


# ---------------------------------------------------------------------------
# compute_signal_metrics — binary mode (pos_threshold == neg_threshold == 0)
# ---------------------------------------------------------------------------

def test_signal_metrics_binary_perfect():
    # pred perfectly matches sign of y_true
    y_true = np.array([ 0.1,  0.2, -0.1, -0.2])
    y_pred = np.array([ 0.5,  0.3, -0.5, -0.3])
    m = compute_signal_metrics(y_true, y_pred)
    assert m["binary_accuracy"]  == 1.0
    assert m["binary_precision"] == 1.0
    assert m["binary_recall"]    == 1.0
    assert m["binary_f1"]        == 1.0
    assert math.isclose(m["binary_mcc"], 1.0, rel_tol=1e-6)
    assert m["binary_n"] == 4
    # No hold zone → no signal_mcc key
    assert "signal_mcc" not in m


def test_signal_metrics_binary_all_wrong():
    y_true = np.array([ 0.1, -0.1])
    y_pred = np.array([-0.5,  0.5])
    m = compute_signal_metrics(y_true, y_pred)
    assert m["binary_accuracy"]  == 0.0
    assert math.isclose(m["binary_mcc"], -1.0, rel_tol=1e-6)


def test_signal_metrics_binary_confusion_shape():
    y_true = np.array([0.1, -0.1, 0.1, -0.1])
    y_pred = np.array([0.2, -0.2, -0.2, 0.2])
    m = compute_signal_metrics(y_true, y_pred)
    cm = m["binary_confusion"]
    assert len(cm) == 2 and len(cm[0]) == 2
    # TP=1 TN=1 FP=1 FN=1 → accuracy=0.5
    assert math.isclose(m["binary_accuracy"], 0.5, rel_tol=1e-9)


def test_signal_metrics_keys_present_binary():
    y = np.array([0.1, -0.1, 0.2])
    m = compute_signal_metrics(y, y)
    required = {"binary_n", "binary_accuracy", "binary_precision",
                "binary_recall", "binary_f1", "binary_mcc", "binary_confusion"}
    assert required.issubset(m.keys())


# ---------------------------------------------------------------------------
# compute_signal_metrics — 3-class hold zone
# ---------------------------------------------------------------------------

def test_signal_metrics_hold_zone_keys():
    y_true = np.array([ 0.02, -0.02,  0.0005, -0.0005,  0.05, -0.05])
    y_pred = np.array([ 0.03, -0.03,  0.001,  -0.001,   0.04, -0.04])
    m = compute_signal_metrics(y_true, y_pred, pos_threshold=0.01, neg_threshold=-0.01)
    required_signal = {"signal_n", "signal_mcc", "signal_macro_f1",
                       "signal_weighted_f1", "signal_confusion", "signal_per_class"}
    assert required_signal.issubset(m.keys())
    cm = m["signal_confusion"]
    assert len(cm) == 3 and len(cm[0]) == 3


def test_signal_metrics_hold_zone_excludes_hold_from_binary():
    # y_true hold → pred classified into hold → excluded from binary metrics
    y_true = np.array([0.05, -0.05,  0.0,   0.0  ])
    y_pred = np.array([0.05, -0.05,  0.001, -0.001])
    m = compute_signal_metrics(y_true, y_pred, pos_threshold=0.01, neg_threshold=-0.01)
    # y_true[2] and y_true[3] are hold (0.0 is in [-0.01, +0.01]) → excluded from binary
    assert m["binary_n"] == 2


def test_signal_metrics_per_class_has_three_entries():
    y_true = np.array([0.1, -0.1, 0.005, -0.005, 0.2, -0.2])
    y_pred = np.array([0.1, -0.1, 0.005, -0.005, 0.2, -0.2])
    m = compute_signal_metrics(y_true, y_pred, pos_threshold=0.01, neg_threshold=-0.01)
    assert len(m["signal_per_class"]) == 3
    class_names = {c["class_name"] for c in m["signal_per_class"]}
    assert class_names == {"short", "hold", "long"}

