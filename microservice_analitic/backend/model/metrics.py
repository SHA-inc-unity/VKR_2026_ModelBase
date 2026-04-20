"""Метрики качества модели: MAE, RMSE, R², Sharpe ratio, сигнальные метрики."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def compute_metrics(
    y_true: "np.ndarray | pd.Series",
    y_pred: "np.ndarray | pd.Series",
    *,
    annualize_factor: float = 1.0,
) -> dict[str, float]:
    """Вычисляет MAE, RMSE, R² и Sharpe ratio стратегии «знак прогноза».

    Sharpe ratio считается для стратегии long/short:
        strategy_return[i] = sign(y_pred[i]) * y_true[i]

    Аргументы:
        annualize_factor — количество баров в году для аннуализации Sharpe.
                           Передавать SECONDS_PER_YEAR * 1000 / step_ms.
    """
    y_true_arr = np.asarray(y_true, dtype=float)
    y_pred_arr = np.asarray(y_pred, dtype=float)

    mae = float(np.mean(np.abs(y_true_arr - y_pred_arr)))
    rmse = float(np.sqrt(np.mean((y_true_arr - y_pred_arr) ** 2)))

    ss_res = np.sum((y_true_arr - y_pred_arr) ** 2)
    ss_tot = np.sum((y_true_arr - np.mean(y_true_arr)) ** 2)
    r2 = float(1.0 - ss_res / ss_tot) if ss_tot > 1e-15 else 0.0

    # Sharpe ratio стратегии
    strategy_returns = np.sign(y_pred_arr) * y_true_arr
    mean_ret = float(np.mean(strategy_returns))
    std_ret = float(np.std(strategy_returns, ddof=1))
    sharpe = (mean_ret / std_ret * float(np.sqrt(annualize_factor))) if std_ret > 1e-12 else 0.0

    return {"MAE": mae, "RMSE": rmse, "R2": r2, "Sharpe": sharpe}


def compute_direction_metrics(
    y_true: "np.ndarray | pd.Series",
    y_pred: "np.ndarray | pd.Series",
) -> dict:
    """Вычисляет TP, TN, FP, FN и accuracy по знаку прогноза.

    Положительный класс — рост цены (target_return_1 > 0).
    Матрица собирается на агрегированных предсказаниях (не per-fold).
    """
    y_true_arr = np.asarray(y_true, dtype=float)
    y_pred_arr = np.asarray(y_pred, dtype=float)

    actual_up = y_true_arr > 0
    pred_up   = y_pred_arr > 0

    tp = int(np.sum( pred_up &  actual_up))
    tn = int(np.sum(~pred_up & ~actual_up))
    fp = int(np.sum( pred_up & ~actual_up))
    fn = int(np.sum(~pred_up &  actual_up))
    total = tp + tn + fp + fn
    accuracy = (tp + tn) / total if total > 0 else 0.0

    return {"TP": tp, "TN": tn, "FP": fp, "FN": fn, "accuracy": accuracy}


def compute_trading_metrics(
    y_true: "np.ndarray | pd.Series",
    y_pred: "np.ndarray | pd.Series",
    *,
    annualize_factor: float = 1.0,
) -> dict[str, float]:
    """Торговые метрики для CV-строки и финального теста.

    Возвращает:
        sharpe        — Sharpe Ratio стратегии «знак прогноза» (аннуализированный)
        dir_acc_pct   — Directional Accuracy, % (доля правильных знаков)
        mae_pct       — MAE в процентах от среднего |y_true|
        profit_factor — сумма прибыли / сумма убытка по стратегии
    """
    y_true_arr = np.asarray(y_true, dtype=float)
    y_pred_arr = np.asarray(y_pred, dtype=float)

    # Стратегия: лонг если pred > 0, шорт если pred < 0
    strategy_returns = np.sign(y_pred_arr) * y_true_arr

    # Sharpe
    mean_ret = float(np.mean(strategy_returns))
    std_ret  = float(np.std(strategy_returns, ddof=1))
    sharpe   = (mean_ret / std_ret * float(np.sqrt(annualize_factor))) if std_ret > 1e-12 else 0.0

    # Directional Accuracy %
    correct = np.sign(y_pred_arr) == np.sign(y_true_arr)
    dir_acc_pct = float(np.mean(correct) * 100.0)

    # MAE %
    denom = float(np.mean(np.abs(y_true_arr)))
    mae   = float(np.mean(np.abs(y_true_arr - y_pred_arr)))
    mae_pct = (mae / denom * 100.0) if denom > 1e-15 else 0.0

    # Profit Factor
    profits = strategy_returns[strategy_returns > 0]
    losses  = strategy_returns[strategy_returns < 0]
    gross_profit = float(np.sum(profits)) if len(profits) > 0 else 0.0
    gross_loss   = float(np.abs(np.sum(losses))) if len(losses) > 0 else 1e-12
    profit_factor = gross_profit / gross_loss if gross_loss > 1e-15 else float("inf")

    return {
        "sharpe":        sharpe,
        "dir_acc_pct":   dir_acc_pct,
        "mae_pct":       mae_pct,
        "profit_factor": profit_factor,
    }


# ---------------------------------------------------------------------------
# Вспомогательные функции для сигнальных метрик
# ---------------------------------------------------------------------------

def _build_confusion_matrix(
    y_true_cls: np.ndarray,
    y_pred_cls: np.ndarray,
    labels: list[int],
) -> np.ndarray:
    """Confusion matrix (rows=actual, cols=predicted) для целочисленных классов."""
    n = len(labels)
    lbl_idx = {lbl: i for i, lbl in enumerate(labels)}
    cm = np.zeros((n, n), dtype=int)
    for t, p in zip(y_true_cls.tolist(), y_pred_cls.tolist()):
        ti = lbl_idx.get(int(t))
        pi = lbl_idx.get(int(p))
        if ti is not None and pi is not None:
            cm[ti, pi] += 1
    return cm


def _mcc_from_confusion(cm: np.ndarray) -> float:
    """Matthews Correlation Coefficient из confusion matrix (2×2 или N×N).

    Использует обобщённую формулу MCC, корректно работающую при любом числе классов
    и устойчивую к дисбалансу.
    """
    n = float(cm.sum())
    if n == 0.0:
        return 0.0
    sum_row = cm.sum(axis=1).astype(float)
    sum_col = cm.sum(axis=0).astype(float)
    correct = float(np.trace(cm))
    numer = n * correct - float(np.dot(sum_row, sum_col))
    denom = float(
        np.sqrt(
            (n ** 2 - float(np.dot(sum_row, sum_row)))
            * (n ** 2 - float(np.dot(sum_col, sum_col)))
        )
    )
    return numer / denom if abs(denom) > 1e-15 else 0.0


def _per_class_prf(
    cm: np.ndarray,
    labels: list[int],
) -> list[dict[str, Any]]:
    """Per-class precision, recall, F1 и support из confusion matrix."""
    out: list[dict[str, Any]] = []
    for i, lbl in enumerate(labels):
        tp = int(cm[i, i])
        fp = int(cm[:, i].sum()) - tp
        fn = int(cm[i, :].sum()) - tp
        prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        rec  = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1   = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0.0
        out.append({
            "label":     lbl,
            "precision": round(prec, 6),
            "recall":    round(rec, 6),
            "f1":        round(f1, 6),
            "support":   int(cm[i, :].sum()),
        })
    return out


# ---------------------------------------------------------------------------
# Сигнальные метрики классификации
# ---------------------------------------------------------------------------

def compute_signal_metrics(
    y_true: "np.ndarray | pd.Series",
    y_pred: "np.ndarray | pd.Series",
    *,
    pos_threshold: float = 0.0,
    neg_threshold: float = 0.0,
) -> dict[str, Any]:
    """Метрики классификации на уровне торговых сигналов поверх регрессии.

    Переводит y_pred и y_true в дискретные классы:
        value > pos_threshold  → long  (+1)
        value < neg_threshold  → short (−1)
        иначе                  → hold  ( 0)   ← только при ненулевой deadband-зоне

    Если pos_threshold == neg_threshold == 0.0 — бинарный режим:
        pred > 0 → long (+1), pred ≤ 0 → short (−1).

    Возвращает плоский словарь:

    Всегда присутствуют (binary up/down, hold-строки из y_true исключаются):
        binary_n, binary_accuracy, binary_precision, binary_recall,
        binary_f1, binary_mcc, binary_confusion (list[list[int]]).

    При ненулевой deadband-зоне дополнительно:
        signal_n, signal_mcc,
        signal_macro_precision, signal_macro_recall, signal_macro_f1,
        signal_weighted_precision, signal_weighted_recall, signal_weighted_f1,
        signal_confusion (3×3 list[list[int]]),
        signal_per_class (list[dict] с ключами label/class_name/precision/recall/f1/support).

    MCC — ключевая метрика: устойчива к дисбалансу классов.
    """
    has_hold_zone = not (pos_threshold == 0.0 and neg_threshold == 0.0)

    y_true_arr = np.asarray(y_true, dtype=float)
    y_pred_arr = np.asarray(y_pred, dtype=float)

    def _classify(arr: np.ndarray, p_th: float, n_th: float) -> np.ndarray:
        out = np.zeros(len(arr), dtype=int)
        out[arr > p_th] =  1
        out[arr < n_th] = -1
        return out

    if has_hold_zone:
        pred_cls = _classify(y_pred_arr, pos_threshold, neg_threshold)
        true_cls = _classify(y_true_arr, pos_threshold, neg_threshold)
    else:
        pred_cls = np.where(y_pred_arr > 0, 1, -1).astype(int)
        true_cls = np.where(y_true_arr > 0, 1, -1).astype(int)

    result: dict[str, Any] = {}

    # ---- Binary метрики (long / short; hold-строки y_true исключаются) -----
    if has_hold_zone:
        mask   = true_cls != 0
        tc_bin = true_cls[mask]
        pc_bin = pred_cls[mask]
    else:
        tc_bin = true_cls
        pc_bin = pred_cls

    n_bin = len(tc_bin)
    result["binary_n"] = n_bin

    if n_bin > 0:
        cm_bin    = _build_confusion_matrix(tc_bin, pc_bin, labels=[-1, 1])
        tn = int(cm_bin[0, 0])
        fp = int(cm_bin[0, 1])
        fn = int(cm_bin[1, 0])
        tp = int(cm_bin[1, 1])
        accuracy  = (tp + tn) / n_bin
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall    = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
        mcc = _mcc_from_confusion(cm_bin)
        result.update({
            "binary_accuracy":  round(accuracy,  6),
            "binary_precision": round(precision, 6),
            "binary_recall":    round(recall,    6),
            "binary_f1":        round(f1,        6),
            "binary_mcc":       round(mcc,       6),
            "binary_confusion": cm_bin.tolist(),
        })
    else:
        result.update({
            "binary_accuracy": 0.0, "binary_precision": 0.0,
            "binary_recall":   0.0, "binary_f1":        0.0,
            "binary_mcc":      0.0,
            "binary_confusion": [[0, 0], [0, 0]],
        })

    # ---- 3-class сигнальные метрики (только при ненулевой deadband-зоне) ----
    if has_hold_zone:
        n_sig   = len(true_cls)
        cm_sig  = _build_confusion_matrix(true_cls, pred_cls, labels=[-1, 0, 1])
        mcc_sig = _mcc_from_confusion(cm_sig)
        per_cls = _per_class_prf(cm_sig, labels=[-1, 0, 1])

        macro_p  = float(np.mean([c["precision"] for c in per_cls]))
        macro_r  = float(np.mean([c["recall"]    for c in per_cls]))
        macro_f1 = float(np.mean([c["f1"]        for c in per_cls]))

        total_sup = sum(c["support"] for c in per_cls)
        if total_sup > 0:
            w_p  = sum(c["precision"] * c["support"] for c in per_cls) / total_sup
            w_r  = sum(c["recall"]    * c["support"] for c in per_cls) / total_sup
            w_f1 = sum(c["f1"]        * c["support"] for c in per_cls) / total_sup
        else:
            w_p = w_r = w_f1 = 0.0

        label_names: dict[int, str] = {-1: "short", 0: "hold", 1: "long"}
        named_per_cls = [
            {**c, "class_name": label_names[c["label"]]} for c in per_cls
        ]

        result.update({
            "signal_n":                  n_sig,
            "signal_mcc":                round(mcc_sig, 6),
            "signal_macro_precision":    round(macro_p,  6),
            "signal_macro_recall":       round(macro_r,  6),
            "signal_macro_f1":           round(macro_f1, 6),
            "signal_weighted_precision": round(w_p,  6),
            "signal_weighted_recall":    round(w_r,  6),
            "signal_weighted_f1":        round(w_f1, 6),
            "signal_confusion":          cm_sig.tolist(),
            "signal_per_class":          named_per_cls,
        })

    return result
