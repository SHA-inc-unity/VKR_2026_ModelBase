from __future__ import annotations

import warnings
from typing import Dict, Tuple

import numpy as np
import pandas as pd
from statsmodels.tsa.arima.model import ARIMA
from statsmodels.tsa.statespace.sarimax import SARIMAX


def metrics(y_true: np.ndarray, y_pred: np.ndarray) -> Dict[str, float]:
    eps = 1e-8
    mae = float(np.mean(np.abs(y_true - y_pred)))
    rmse = float(np.sqrt(np.mean((y_true - y_pred) ** 2)))
    mape = float(np.mean(np.abs((y_true - y_pred) / np.maximum(np.abs(y_true), eps))) * 100.0)
    return {"MAE": mae, "RMSE": rmse, "MAPE": mape}


def run_naive(train: pd.Series, test: pd.Series) -> Tuple[Dict[str, float], pd.DataFrame]:
    preds = np.empty(len(test), dtype=float)
    last_value = float(train.iloc[-1])
    for i in range(len(test)):
        preds[i] = last_value
        last_value = float(test.iloc[i])
    y_true = test.values.astype(float)
    return metrics(y_true, preds), pd.DataFrame({"y_true": y_true, "y_pred": preds})


def _print_progress(prefix: str, current: int, total: int, last_shown: int) -> int:
    if total <= 0:
        return last_shown
    percent = int((current / total) * 100)
    if percent >= last_shown + 5 or percent == 100:
        print(f"{prefix}: {percent}%")
        return percent
    return last_shown


def run_arima(train: pd.Series, test: pd.Series, order=(1, 1, 1), refit_every: int = 24, show_progress: bool = True):
    train_arr = pd.to_numeric(train, errors="coerce").dropna().astype(float).values
    test_arr = pd.to_numeric(test, errors="coerce").dropna().astype(float).values

    if len(train_arr) < 40 or len(test_arr) < 5:
        raise RuntimeError("Слишком мало данных для ARIMA")

    history = np.log(np.clip(train_arr, 1e-8, None)).tolist()
    preds_log = []

    if show_progress:
        print("ARIMA: 0%")

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=UserWarning)
        warnings.filterwarnings("ignore", category=RuntimeWarning)

        model = ARIMA(history, order=order, enforce_stationarity=False, enforce_invertibility=False).fit()

        last_shown = 0
        total = len(test_arr)
        for i, true_price in enumerate(test_arr, start=1):
            pred_log = float(model.forecast(steps=1)[0])
            preds_log.append(pred_log)

            history.append(float(np.log(max(true_price, 1e-8))))

            if i % max(1, refit_every) == 0:
                model = ARIMA(history, order=order, enforce_stationarity=False, enforce_invertibility=False).fit()
            else:
                model = model.append([history[-1]], refit=False)

            if show_progress:
                last_shown = _print_progress("ARIMA", i, total, last_shown)

    preds = np.exp(np.asarray(preds_log, dtype=float))
    y_true = test_arr.astype(float)
    return metrics(y_true, preds), pd.DataFrame({"y_true": y_true, "y_pred": preds})


def run_sarima(
    train: pd.Series,
    test: pd.Series,
    order=(1, 1, 0),
    seasonal_order=(1, 1, 1, 24),
    refit_every: int = 48,
    show_progress: bool = True,
    use_cuda: bool = False,
    fit_window: int = 1200,
    maxiter: int = 60,
):
    train_arr = pd.to_numeric(train, errors="coerce").dropna().astype(float).values
    test_arr = pd.to_numeric(test, errors="coerce").dropna().astype(float).values

    if len(train_arr) < 80 or len(test_arr) < 5:
        raise RuntimeError("Слишком мало данных для SARIMA")

    if use_cuda:
        print("SARIMA (statsmodels) работает на CPU; CUDA для него не поддерживается.")

    history_log = np.log(np.clip(train_arr, 1e-8, None)).tolist()

    if show_progress:
        print("SARIMA: 0%")

    def _fit_model(history_values):
        return SARIMAX(
            history_values,
            order=order,
            seasonal_order=seasonal_order,
            enforce_stationarity=False,
            enforce_invertibility=False,
            simple_differencing=False,
        ).fit(disp=False, maxiter=int(max(10, maxiter)))

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=UserWarning)
        warnings.filterwarnings("ignore", category=RuntimeWarning)
        preds_log = []
        model = _fit_model(history_log[-int(max(200, fit_window)) :])

        last_shown = 0
        total = len(test_arr)
        for i, true_price in enumerate(test_arr, start=1):
            try:
                pred_log = float(model.forecast(steps=1)[0])
            except Exception:
                pred_log = float(history_log[-1])

            if not np.isfinite(pred_log):
                pred_log = float(history_log[-1])
            preds_log.append(pred_log)

            history_log.append(float(np.log(max(true_price, 1e-8))))

            need_refit = (i % max(1, refit_every) == 0)
            if need_refit:
                model = _fit_model(history_log[-int(max(200, fit_window)) :])
            else:
                try:
                    model = model.append([history_log[-1]], refit=False)
                except Exception:
                    model = _fit_model(history_log[-int(max(200, fit_window)) :])

            if show_progress:
                last_shown = _print_progress("SARIMA", i, total, last_shown)

        preds_log = np.asarray(preds_log, dtype=float)

        if show_progress:
            print("SARIMA: 100%")

    fallback_level = float(train_arr[-1])
    preds = np.exp(preds_log)
    preds = np.nan_to_num(preds, nan=fallback_level, posinf=fallback_level, neginf=fallback_level)
    y_true = test_arr.astype(float)
    return metrics(y_true, preds), pd.DataFrame({"y_true": y_true, "y_pred": preds})
