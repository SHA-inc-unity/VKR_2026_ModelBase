from __future__ import annotations

import warnings
import pickle
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import product
from typing import Dict, Tuple, Any

import numpy as np
import pandas as pd
import torch
from statsmodels.tsa.arima.model import ARIMA
from statsmodels.tsa.statespace.sarimax import SARIMAX

try:
    from sklearn.base import BaseEstimator
    from sklearn.model_selection import GridSearchCV, ParameterGrid, TimeSeriesSplit

    _SKLEARN_AVAILABLE = True
except Exception:
    BaseEstimator = object
    GridSearchCV = None
    ParameterGrid = None
    TimeSeriesSplit = None
    _SKLEARN_AVAILABLE = False

try:
    # Use the base tqdm import to avoid noisy notebook-widget warnings
    # when ipywidgets is unavailable in some Jupyter/VS Code kernels.
    from tqdm import tqdm
except Exception:
    tqdm = None

from nbeats import GenericBasis, NBeats, NBeatsBlock


_PROGRESS_HOOK = None


def _progress_step(step: int = 1):
    """Advance active serial GridSearch progress bar if configured."""
    hook = _PROGRESS_HOOK
    if hook is None:
        return
    try:
        hook(max(0, int(step)))
    except Exception:
        pass


@contextmanager
def _tqdm_joblib(total: int, desc: str):
    """Attach tqdm progress bar to joblib backend used by GridSearchCV."""
    if tqdm is None:
        yield
        return

    try:
        from joblib import parallel
    except Exception:
        yield
        return

    pbar = tqdm(total=max(0, int(total)), desc=desc, unit="fit")
    old_batch_callback = parallel.BatchCompletionCallBack

    class _TqdmBatchCallback(parallel.BatchCompletionCallBack):
        def __call__(self, *args, **kwargs):
            pbar.update(self.batch_size)
            return super().__call__(*args, **kwargs)

    parallel.BatchCompletionCallBack = _TqdmBatchCallback
    try:
        yield
    finally:
        parallel.BatchCompletionCallBack = old_batch_callback
        # In some serial/fallback execution paths callbacks may not fire;
        # force final state to avoid misleading 0/total display.
        if pbar.n < pbar.total:
            pbar.update(pbar.total - pbar.n)
        pbar.close()


def _fit_grid_with_progress(grid, x_train, y_train, param_grid: dict, ts_splits: int, label: str):
    """Fit GridSearchCV with optional x/total progress output."""
    if ParameterGrid is None:
        grid.fit(x_train, y_train)
        return

    total_fits = int(len(ParameterGrid(param_grid)) * int(ts_splits))

    # tqdm+joblib callback is reliable with parallel backend. In serial mode
    # (n_jobs=1), use a lightweight explicit hook updated from estimator.score().
    try:
        jobs = int(getattr(grid, "n_jobs", 1) or 1)
    except Exception:
        jobs = 1

    if jobs == 1:
        global _PROGRESS_HOOK
        pbar = tqdm(total=max(0, int(total_fits)), desc=f"{label} GridSearchCV", unit="fit") if tqdm is not None else None
        old_hook = _PROGRESS_HOOK

        def _hook(step: int = 1):
            if pbar is not None:
                pbar.update(step)

        _PROGRESS_HOOK = _hook
        try:
            if int(getattr(grid, "verbose", 0) or 0) != 0:
                grid.verbose = 0
            grid.fit(x_train, y_train)
        finally:
            _PROGRESS_HOOK = old_hook
            if pbar is not None:
                if pbar.n < pbar.total:
                    pbar.update(pbar.total - pbar.n)
                pbar.close()
        return

    with _tqdm_joblib(total=total_fits, desc=f"{label} GridSearchCV"):
        grid.fit(x_train, y_train)


def _grid_verbose_level() -> int:
    # If tqdm is unavailable, use sklearn verbose>1 to show fit-by-fit progress lines.
    return 1 if tqdm is not None else 2


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


def fit_naive_inference_model(train: pd.Series) -> Dict[str, Any]:
    train_arr = pd.to_numeric(train, errors="coerce").dropna().astype(float).values
    if len(train_arr) < 1:
        raise RuntimeError("Пустой train для naive")
    return {"model_type": "naive", "last_value": float(train_arr[-1])}


def predict_naive_inference(model_obj: Dict[str, Any], test: pd.Series) -> Tuple[Dict[str, float], pd.DataFrame]:
    test_arr = pd.to_numeric(test, errors="coerce").dropna().astype(float).values
    last_value = float(model_obj["last_value"])
    preds = np.empty(len(test_arr), dtype=float)
    for i, true_val in enumerate(test_arr):
        preds[i] = last_value
        last_value = float(true_val)
    return metrics(test_arr, preds), pd.DataFrame({"y_true": test_arr, "y_pred": preds})


def fit_arima_inference_model(train: pd.Series, order=(1, 1, 1)) -> Dict[str, Any]:
    train_arr = pd.to_numeric(train, errors="coerce").dropna().astype(float).values
    if len(train_arr) < 40:
        raise RuntimeError("Слишком мало train для ARIMA")

    history_log = np.log(np.clip(train_arr, 1e-8, None)).tolist()
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=UserWarning)
        warnings.filterwarnings("ignore", category=RuntimeWarning)
        fitted = ARIMA(history_log, order=order, enforce_stationarity=False, enforce_invertibility=False).fit()

    return {
        "model_type": "arima",
        "order": tuple(order),
        "train_last": float(train_arr[-1]),
        "fitted_blob": pickle.dumps(fitted),
    }


def predict_arima_inference(model_obj: Dict[str, Any], test: pd.Series) -> Tuple[Dict[str, float], pd.DataFrame]:
    test_arr = pd.to_numeric(test, errors="coerce").dropna().astype(float).values
    fitted = pickle.loads(model_obj["fitted_blob"])

    fallback_log = float(np.log(max(float(model_obj["train_last"]), 1e-8)))
    n_steps = int(len(test_arr))
    if n_steps == 0:
        return metrics(test_arr, np.array([], dtype=float)), pd.DataFrame({"y_true": test_arr, "y_pred": np.array([], dtype=float)})

    # ARIMA quality is sensitive to long recursive horizons; default to
    # one-step walk-forward updates unless explicitly overridden.
    update_stride = int(max(1, model_obj.get("update_stride", 1)))

    preds_log_parts = []
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=UserWarning)
        warnings.filterwarnings("ignore", category=RuntimeWarning)
        i = 0
        while i < n_steps:
            block = int(min(update_stride, n_steps - i))

            try:
                block_pred = np.asarray(fitted.forecast(steps=block), dtype=float)
            except Exception:
                block_pred = np.full(shape=(block,), fill_value=fallback_log, dtype=float)
            preds_log_parts.append(block_pred)

            true_block = np.log(np.clip(test_arr[i : i + block], 1e-8, None)).tolist()
            try:
                fitted = fitted.append(true_block, refit=False)
            except Exception:
                pass

            i += block

    preds_log = np.concatenate(preds_log_parts) if preds_log_parts else np.array([], dtype=float)
    preds_log = np.nan_to_num(preds_log, nan=fallback_log, posinf=fallback_log, neginf=fallback_log)
    preds = np.exp(preds_log)
    preds = np.nan_to_num(preds, nan=float(model_obj["train_last"]), posinf=float(model_obj["train_last"]), neginf=float(model_obj["train_last"]))
    return metrics(test_arr, preds), pd.DataFrame({"y_true": test_arr, "y_pred": preds})


def fit_sarima_inference_model(
    train: pd.Series,
    order=(1, 1, 0),
    seasonal_order=(1, 1, 1, 24),
    maxiter: int = 60,
) -> Dict[str, Any]:
    train_arr = pd.to_numeric(train, errors="coerce").dropna().astype(float).values
    if len(train_arr) < 80:
        raise RuntimeError("Слишком мало train для SARIMA")

    history_log = np.log(np.clip(train_arr, 1e-8, None)).tolist()
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=UserWarning)
        warnings.filterwarnings("ignore", category=RuntimeWarning)
        fitted = SARIMAX(
            history_log,
            order=order,
            seasonal_order=seasonal_order,
            enforce_stationarity=False,
            enforce_invertibility=False,
            simple_differencing=False,
        ).fit(disp=False, maxiter=int(max(10, maxiter)))

    return {
        "model_type": "sarima",
        "order": tuple(order),
        "seasonal_order": tuple(seasonal_order),
        "train_last": float(train_arr[-1]),
        "fitted_blob": pickle.dumps(fitted),
    }


def predict_sarima_inference(model_obj: Dict[str, Any], test: pd.Series) -> Tuple[Dict[str, float], pd.DataFrame]:
    test_arr = pd.to_numeric(test, errors="coerce").dropna().astype(float).values
    fitted = pickle.loads(model_obj["fitted_blob"])

    fallback_log = float(np.log(max(float(model_obj["train_last"]), 1e-8)))
    n_steps = int(len(test_arr))
    if n_steps == 0:
        return metrics(test_arr, np.array([], dtype=float)), pd.DataFrame({"y_true": test_arr, "y_pred": np.array([], dtype=float)})

    update_stride = int(max(1, model_obj.get("update_stride", 24)))

    preds_log_parts = []
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=UserWarning)
        warnings.filterwarnings("ignore", category=RuntimeWarning)
        i = 0
        while i < n_steps:
            block = int(min(update_stride, n_steps - i))

            try:
                block_pred = np.asarray(fitted.forecast(steps=block), dtype=float)
            except Exception:
                block_pred = np.full(shape=(block,), fill_value=fallback_log, dtype=float)
            preds_log_parts.append(block_pred)

            true_block = np.log(np.clip(test_arr[i : i + block], 1e-8, None)).tolist()
            try:
                fitted = fitted.append(true_block, refit=False)
            except Exception:
                pass

            i += block

    preds_log = np.concatenate(preds_log_parts) if preds_log_parts else np.array([], dtype=float)
    preds_log = np.nan_to_num(preds_log, nan=fallback_log, posinf=fallback_log, neginf=fallback_log)
    preds = np.exp(preds_log)
    preds = np.nan_to_num(preds, nan=float(model_obj["train_last"]), posinf=float(model_obj["train_last"]), neginf=float(model_obj["train_last"]))
    return metrics(test_arr, preds), pd.DataFrame({"y_true": test_arr, "y_pred": preds})


def run_arima(train: pd.Series, test: pd.Series, order=(1, 1, 1), refit_every: int = 24, show_progress: bool = True):
    train_arr = pd.to_numeric(train, errors="coerce").dropna().astype(float).values
    test_arr = pd.to_numeric(test, errors="coerce").dropna().astype(float).values

    if len(train_arr) < 40 or len(test_arr) < 5:
        raise RuntimeError("Слишком мало данных для ARIMA")

    history = np.log(np.clip(train_arr, 1e-8, None)).tolist()
    preds_log = []

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=UserWarning)
        warnings.filterwarnings("ignore", category=RuntimeWarning)

        model = ARIMA(history, order=order, enforce_stationarity=False, enforce_invertibility=False).fit()

        for i, true_price in enumerate(test_arr, start=1):
            pred_log = float(model.forecast(steps=1)[0])
            preds_log.append(pred_log)

            history.append(float(np.log(max(true_price, 1e-8))))

            if i % max(1, refit_every) == 0:
                model = ARIMA(history, order=order, enforce_stationarity=False, enforce_invertibility=False).fit()
            else:
                model = model.append([history[-1]], refit=False)

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

        preds_log = np.asarray(preds_log, dtype=float)

    fallback_level = float(train_arr[-1])
    preds = np.exp(preds_log)
    preds = np.nan_to_num(preds, nan=fallback_level, posinf=fallback_level, neginf=fallback_level)
    y_true = test_arr.astype(float)
    return metrics(y_true, preds), pd.DataFrame({"y_true": y_true, "y_pred": preds})


def run_nbeats(
    train: pd.Series,
    test: pd.Series,
    context_len: int = 168,
    n_blocks: int = 4,
    layers: int = 4,
    layer_size: int = 256,
    epochs: int = 25,
    batch_size: int = 128,
    lr: float = 1e-3,
    weight_decay: float = 1e-4,
    use_cuda: bool = True,
    show_progress: bool = True,
):
    train_arr = pd.to_numeric(train, errors="coerce").dropna().astype(float).values
    test_arr = pd.to_numeric(test, errors="coerce").dropna().astype(float).values

    if len(train_arr) < 120 or len(test_arr) < 5:
        raise RuntimeError("Слишком мало данных для N-BEATS")

    train_log = np.log(np.clip(train_arr, 1e-8, None))
    mean = float(train_log.mean())
    std = float(train_log.std() + 1e-8)
    train_norm = (train_log - mean) / std

    context = int(min(max(24, context_len), max(24, len(train_norm) - 5)))
    n_samples = len(train_norm) - context
    if n_samples < 32:
        raise RuntimeError("Слишком мало окон для обучения N-BEATS")

    x_np = np.stack([train_norm[i : i + context] for i in range(n_samples)]).astype(np.float32)
    y_np = np.asarray([train_norm[i + context] for i in range(n_samples)], dtype=np.float32)[:, None]

    x_t = torch.from_numpy(x_np)
    y_t = torch.from_numpy(y_np)

    cuda_exist = torch.cuda.is_available() and bool(use_cuda)
    device = torch.device("cuda" if cuda_exist else "cpu")

    blocks = []
    for _ in range(max(1, n_blocks)):
        basis = GenericBasis(backcast_size=context, forecast_size=1)
        blocks.append(
            NBeatsBlock(
                input_size=context,
                theta_size=context + 1,
                basis_function=basis,
                layers=max(2, layers),
                layer_size=max(64, layer_size),
            )
        )

    model = NBeats(torch.nn.ModuleList(blocks)).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=weight_decay)
    loss_fn = torch.nn.MSELoss()

    ds = torch.utils.data.TensorDataset(x_t, y_t)
    dl = torch.utils.data.DataLoader(ds, batch_size=max(16, batch_size), shuffle=True, pin_memory=cuda_exist)

    model.train()
    for ep in range(1, max(1, epochs) + 1):
        for xb, yb in dl:
            xb = xb.to(device, non_blocking=cuda_exist)
            yb = yb.to(device, non_blocking=cuda_exist)
            mask = torch.ones_like(xb)

            opt.zero_grad(set_to_none=True)
            pred = model(xb, mask)
            loss = loss_fn(pred, yb)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()

    model.eval()
    history_norm = list(train_norm.astype(float))
    preds = []
    with torch.no_grad():
        for i, true_price in enumerate(test_arr, start=1):
            if len(history_norm) >= context:
                x_ctx = np.asarray(history_norm[-context:], dtype=np.float32)
            else:
                pad = np.full((context - len(history_norm),), history_norm[0], dtype=np.float32)
                x_ctx = np.concatenate([pad, np.asarray(history_norm, dtype=np.float32)])

            xb = torch.from_numpy(x_ctx[None, :]).to(device)
            mask = torch.ones_like(xb)
            pred_norm = float(model(xb, mask).squeeze().item())

            pred_log = pred_norm * std + mean
            pred_val = float(np.exp(pred_log))
            if not np.isfinite(pred_val):
                pred_val = float(train_arr[-1])
            preds.append(pred_val)

            true_log = float(np.log(max(true_price, 1e-8)))
            history_norm.append((true_log - mean) / std)

    preds = np.asarray(preds, dtype=float)
    y_true = test_arr.astype(float)
    return metrics(y_true, preds), pd.DataFrame({"y_true": y_true, "y_pred": preds})


def fit_nbeats_inference_model(
    train: pd.Series,
    context_len: int = 168,
    n_blocks: int = 4,
    layers: int = 4,
    layer_size: int = 256,
    epochs: int = 25,
    batch_size: int = 128,
    lr: float = 1e-3,
    weight_decay: float = 1e-4,
    use_cuda: bool = True,
) -> Dict[str, Any]:
    train_arr = pd.to_numeric(train, errors="coerce").dropna().astype(float).values
    if len(train_arr) < 120:
        raise RuntimeError("Слишком мало train для N-BEATS")

    train_log = np.log(np.clip(train_arr, 1e-8, None))
    mean = float(train_log.mean())
    std = float(train_log.std() + 1e-8)
    train_norm = (train_log - mean) / std

    context = int(min(max(24, context_len), max(24, len(train_norm) - 5)))
    n_samples = len(train_norm) - context
    if n_samples < 32:
        raise RuntimeError("Слишком мало окон для обучения N-BEATS")

    x_np = np.stack([train_norm[i : i + context] for i in range(n_samples)]).astype(np.float32)
    y_np = np.asarray([train_norm[i + context] for i in range(n_samples)], dtype=np.float32)[:, None]

    x_t = torch.from_numpy(x_np)
    y_t = torch.from_numpy(y_np)

    cuda_exist = torch.cuda.is_available() and bool(use_cuda)
    device = torch.device("cuda" if cuda_exist else "cpu")

    blocks = []
    for _ in range(max(1, n_blocks)):
        basis = GenericBasis(backcast_size=context, forecast_size=1)
        blocks.append(
            NBeatsBlock(
                input_size=context,
                theta_size=context + 1,
                basis_function=basis,
                layers=max(2, layers),
                layer_size=max(64, layer_size),
            )
        )

    model = NBeats(torch.nn.ModuleList(blocks)).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=weight_decay)
    loss_fn = torch.nn.MSELoss()
    ds = torch.utils.data.TensorDataset(x_t, y_t)
    dl = torch.utils.data.DataLoader(ds, batch_size=max(16, batch_size), shuffle=True, pin_memory=cuda_exist)

    model.train()
    for _ in range(1, max(1, epochs) + 1):
        for xb, yb in dl:
            xb = xb.to(device, non_blocking=cuda_exist)
            yb = yb.to(device, non_blocking=cuda_exist)
            mask = torch.ones_like(xb)

            opt.zero_grad(set_to_none=True)
            pred = model(xb, mask)
            loss = loss_fn(pred, yb)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()

    return {
        "model_type": "nbeats",
        "context": int(context),
        "mean": float(mean),
        "std": float(std),
        "train_last": float(train_arr[-1]),
        "state_dict": {k: v.detach().cpu().clone() for k, v in model.state_dict().items()},
        "arch": {
            "n_blocks": int(max(1, n_blocks)),
            "layers": int(max(2, layers)),
            "layer_size": int(max(64, layer_size)),
        },
        "use_cuda": bool(cuda_exist),
    }


def predict_nbeats_inference(model_obj: Dict[str, Any], train: pd.Series, test: pd.Series) -> Tuple[Dict[str, float], pd.DataFrame]:
    train_arr = pd.to_numeric(train, errors="coerce").dropna().astype(float).values
    test_arr = pd.to_numeric(test, errors="coerce").dropna().astype(float).values

    context = int(model_obj["context"])
    mean = float(model_obj["mean"])
    std = float(model_obj["std"])
    cuda_exist = bool(model_obj.get("use_cuda", False)) and torch.cuda.is_available()
    device = torch.device("cuda" if cuda_exist else "cpu")

    arch = model_obj["arch"]
    blocks = []
    for _ in range(int(arch["n_blocks"])):
        basis = GenericBasis(backcast_size=context, forecast_size=1)
        blocks.append(
            NBeatsBlock(
                input_size=context,
                theta_size=context + 1,
                basis_function=basis,
                layers=int(arch["layers"]),
                layer_size=int(arch["layer_size"]),
            )
        )
    model = NBeats(torch.nn.ModuleList(blocks)).to(device)
    model.load_state_dict(model_obj["state_dict"])
    model.eval()

    train_log = np.log(np.clip(train_arr, 1e-8, None))
    history_norm = list(((train_log - mean) / std).astype(float))

    preds = []
    with torch.no_grad():
        for true_price in test_arr:
            if len(history_norm) >= context:
                x_ctx = np.asarray(history_norm[-context:], dtype=np.float32)
            else:
                pad = np.full((context - len(history_norm),), history_norm[0], dtype=np.float32)
                x_ctx = np.concatenate([pad, np.asarray(history_norm, dtype=np.float32)])

            xb = torch.from_numpy(x_ctx[None, :]).to(device)
            mask = torch.ones_like(xb)
            pred_norm = float(model(xb, mask).squeeze().item())

            pred_log = pred_norm * std + mean
            pred_val = float(np.exp(pred_log))
            if not np.isfinite(pred_val):
                pred_val = float(model_obj["train_last"])
            preds.append(pred_val)

            true_log = float(np.log(max(float(true_price), 1e-8)))
            history_norm.append((true_log - mean) / std)

    preds = np.asarray(preds, dtype=float)
    return metrics(test_arr, preds), pd.DataFrame({"y_true": test_arr, "y_pred": preds})


class _LSTMForecaster(torch.nn.Module):
    def __init__(self, input_size: int = 1, hidden_size: int = 64, num_layers: int = 2, dropout: float = 0.1):
        super().__init__()
        self.lstm = torch.nn.LSTM(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0.0,
        )
        self.head = torch.nn.Linear(hidden_size, 1)

    def forward(self, x):
        out, _ = self.lstm(x)
        return self.head(out[:, -1, :])


class _LSTMGridSearchEstimator(BaseEstimator):
    def __init__(
        self,
        context_len: int = 96,
        hidden_size: int = 64,
        num_layers: int = 2,
        dropout: float = 0.1,
        epochs: int = 20,
        batch_size: int = 64,
        lr: float = 1e-3,
        weight_decay: float = 1e-4,
        use_cuda: bool = True,
        score_metric: str = "MAE",
    ):
        self.context_len = context_len
        self.hidden_size = hidden_size
        self.num_layers = num_layers
        self.dropout = dropout
        self.epochs = epochs
        self.batch_size = batch_size
        self.lr = lr
        self.weight_decay = weight_decay
        self.use_cuda = use_cuda
        self.score_metric = score_metric

    def fit(self, X, y):
        self._train_series_ = pd.Series(np.asarray(y, dtype=float))
        return self

    def score(self, X, y):
        _progress_step()
        if not hasattr(self, "_train_series_"):
            raise RuntimeError("Estimator is not fitted")

        valid = {"MAE", "RMSE", "MAPE"}
        if self.score_metric not in valid:
            raise ValueError(f"score_metric должен быть одним из {sorted(valid)}")

        test_series = pd.Series(np.asarray(y, dtype=float))
        m, _ = run_lstm(
            train=self._train_series_,
            test=test_series,
            context_len=int(self.context_len),
            hidden_size=int(self.hidden_size),
            num_layers=int(self.num_layers),
            dropout=float(self.dropout),
            epochs=int(self.epochs),
            batch_size=int(self.batch_size),
            lr=float(self.lr),
            weight_decay=float(self.weight_decay),
            use_cuda=bool(self.use_cuda),
            show_progress=False,
        )

        # GridSearchCV maximizes score; we minimize metric by returning negative value.
        return -float(m[self.score_metric])


class _ARIMAGridSearchEstimator(BaseEstimator):
    def __init__(self, p: int = 1, d: int = 1, q: int = 1, refit_every: int = 24, score_metric: str = "MAE"):
        self.p = p
        self.d = d
        self.q = q
        self.refit_every = refit_every
        self.score_metric = score_metric

    def fit(self, X, y):
        self._train_series_ = pd.Series(np.asarray(y, dtype=float))
        return self

    def score(self, X, y):
        _progress_step()
        if not hasattr(self, "_train_series_"):
            raise RuntimeError("Estimator is not fitted")

        test_series = pd.Series(np.asarray(y, dtype=float))
        m, _ = run_arima(
            train=self._train_series_,
            test=test_series,
            order=(int(self.p), int(self.d), int(self.q)),
            refit_every=int(self.refit_every),
            show_progress=False,
        )
        return -float(m[self.score_metric])


class _SARIMAGridSearchEstimator(BaseEstimator):
    def __init__(
        self,
        p: int = 1,
        d: int = 1,
        q: int = 0,
        sp: int = 1,
        sd: int = 1,
        sq: int = 1,
        s: int = 24,
        refit_every: int = 48,
        fit_window: int = 1000,
        maxiter: int = 50,
        score_metric: str = "MAE",
    ):
        self.p = p
        self.d = d
        self.q = q
        self.sp = sp
        self.sd = sd
        self.sq = sq
        self.s = s
        self.refit_every = refit_every
        self.fit_window = fit_window
        self.maxiter = maxiter
        self.score_metric = score_metric

    def fit(self, X, y):
        self._train_series_ = pd.Series(np.asarray(y, dtype=float))
        return self

    def score(self, X, y):
        _progress_step()
        if not hasattr(self, "_train_series_"):
            raise RuntimeError("Estimator is not fitted")

        test_series = pd.Series(np.asarray(y, dtype=float))
        m, _ = run_sarima(
            train=self._train_series_,
            test=test_series,
            order=(int(self.p), int(self.d), int(self.q)),
            seasonal_order=(int(self.sp), int(self.sd), int(self.sq), int(self.s)),
            refit_every=int(self.refit_every),
            fit_window=int(self.fit_window),
            maxiter=int(self.maxiter),
            show_progress=False,
            use_cuda=False,
        )
        return -float(m[self.score_metric])


class _NaiveGridSearchEstimator(BaseEstimator):
    def __init__(self, strategy: str = "last", score_metric: str = "MAE"):
        self.strategy = strategy
        self.score_metric = score_metric

    def fit(self, X, y):
        self._train_series_ = pd.Series(np.asarray(y, dtype=float))
        return self

    def score(self, X, y):
        _progress_step()
        if not hasattr(self, "_train_series_"):
            raise RuntimeError("Estimator is not fitted")
        if self.strategy != "last":
            raise ValueError("Для naive поддерживается только strategy='last'")

        test_series = pd.Series(np.asarray(y, dtype=float))
        m, _ = run_naive(self._train_series_, test_series)
        return -float(m[self.score_metric])


class _NBEATSGridSearchEstimator(BaseEstimator):
    def __init__(
        self,
        context_len: int = 168,
        n_blocks: int = 4,
        layers: int = 4,
        layer_size: int = 256,
        epochs: int = 25,
        batch_size: int = 128,
        lr: float = 1e-3,
        weight_decay: float = 1e-4,
        use_cuda: bool = True,
        score_metric: str = "MAE",
    ):
        self.context_len = context_len
        self.n_blocks = n_blocks
        self.layers = layers
        self.layer_size = layer_size
        self.epochs = epochs
        self.batch_size = batch_size
        self.lr = lr
        self.weight_decay = weight_decay
        self.use_cuda = use_cuda
        self.score_metric = score_metric

    def fit(self, X, y):
        self._train_series_ = pd.Series(np.asarray(y, dtype=float))
        return self

    def score(self, X, y):
        _progress_step()
        if not hasattr(self, "_train_series_"):
            raise RuntimeError("Estimator is not fitted")

        test_series = pd.Series(np.asarray(y, dtype=float))
        m, _ = run_nbeats(
            train=self._train_series_,
            test=test_series,
            context_len=int(self.context_len),
            n_blocks=int(self.n_blocks),
            layers=int(self.layers),
            layer_size=int(self.layer_size),
            epochs=int(self.epochs),
            batch_size=int(self.batch_size),
            lr=float(self.lr),
            weight_decay=float(self.weight_decay),
            use_cuda=bool(self.use_cuda),
            show_progress=False,
        )
        return -float(m[self.score_metric])


def run_lstm(
    train: pd.Series,
    test: pd.Series,
    context_len: int = 96,
    hidden_size: int = 64,
    num_layers: int = 2,
    dropout: float = 0.1,
    epochs: int = 20,
    batch_size: int = 64,
    lr: float = 1e-3,
    weight_decay: float = 1e-4,
    use_cuda: bool = True,
    show_progress: bool = True,
):
    train_arr = pd.to_numeric(train, errors="coerce").dropna().astype(float).values
    test_arr = pd.to_numeric(test, errors="coerce").dropna().astype(float).values

    if len(train_arr) < 80 or len(test_arr) < 5:
        raise RuntimeError("Слишком мало данных для LSTM")

    train_log = np.log(np.clip(train_arr, 1e-8, None))
    train_ret = np.diff(train_log)
    ret_mean = float(train_ret.mean())
    ret_std = float(train_ret.std() + 1e-8)
    train_ret_norm = (train_ret - ret_mean) / ret_std

    context = int(min(max(24, context_len), max(24, len(train_ret_norm) - 5)))
    n_samples = len(train_ret_norm) - context
    if n_samples < 24:
        raise RuntimeError("Слишком мало окон для обучения LSTM")

    x_np = np.stack([train_ret_norm[i : i + context] for i in range(n_samples)]).astype(np.float32)
    y_np = np.asarray([train_ret_norm[i + context] for i in range(n_samples)], dtype=np.float32)[:, None]

    x_t = torch.from_numpy(x_np)[:, :, None]
    y_t = torch.from_numpy(y_np)

    cuda_exist = torch.cuda.is_available() and bool(use_cuda)
    device = torch.device("cuda" if cuda_exist else "cpu")

    if show_progress:
        print(f"LSTM device: {device}")

    model = _LSTMForecaster(
        input_size=1,
        hidden_size=max(16, int(hidden_size)),
        num_layers=max(1, int(num_layers)),
        dropout=float(max(0.0, min(0.5, dropout))),
    ).to(device)

    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=weight_decay)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        opt,
        mode="min",
        factor=0.5,
        patience=max(2, int(max(1, epochs) // 6)),
        min_lr=1e-5,
    )
    loss_fn = torch.nn.SmoothL1Loss()

    ds = torch.utils.data.TensorDataset(x_t, y_t)
    val_size = max(8, int(len(ds) * 0.15))
    if len(ds) - val_size < 16:
        val_size = 0

    if val_size > 0:
        train_size = len(ds) - val_size
        train_ds, val_ds = torch.utils.data.random_split(ds, [train_size, val_size])
    else:
        train_ds, val_ds = ds, None

    dl = torch.utils.data.DataLoader(train_ds, batch_size=max(16, batch_size), shuffle=True, pin_memory=cuda_exist)
    val_dl = (
        torch.utils.data.DataLoader(val_ds, batch_size=max(16, batch_size), shuffle=False, pin_memory=cuda_exist)
        if val_ds is not None
        else None
    )

    total_epochs = max(1, int(epochs))
    patience = max(4, total_epochs // 4)
    no_improve = 0
    best_val = float("inf")
    best_state = None

    for ep in range(1, total_epochs + 1):
        model.train()
        for xb, yb in dl:
            xb = xb.to(device, non_blocking=cuda_exist)
            yb = yb.to(device, non_blocking=cuda_exist)

            opt.zero_grad(set_to_none=True)
            pred = model(xb)
            loss = loss_fn(pred, yb)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()

        if val_dl is not None:
            model.eval()
            val_loss_sum = 0.0
            val_count = 0
            with torch.no_grad():
                for vxb, vyb in val_dl:
                    vxb = vxb.to(device, non_blocking=cuda_exist)
                    vyb = vyb.to(device, non_blocking=cuda_exist)
                    vpred = model(vxb)
                    vloss = loss_fn(vpred, vyb)
                    val_loss_sum += float(vloss.item()) * int(vxb.shape[0])
                    val_count += int(vxb.shape[0])

            current_val = val_loss_sum / max(1, val_count)
            scheduler.step(current_val)

            if current_val + 1e-5 < best_val:
                best_val = current_val
                best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
                no_improve = 0
            else:
                no_improve += 1

        if show_progress and val_dl is not None:
            print(f"LSTM val_loss={best_val:.5f}")

        if val_dl is not None and no_improve >= patience:
            if show_progress:
                print(f"LSTM early stop: epoch={ep}")
            break

    if best_state is not None:
        model.load_state_dict(best_state)

    model.eval()
    history_log = list(train_log.astype(float))
    history_ret_norm = list(train_ret_norm.astype(float))
    preds = []
    with torch.no_grad():
        for i, true_price in enumerate(test_arr, start=1):
            if len(history_ret_norm) >= context:
                x_ctx = np.asarray(history_ret_norm[-context:], dtype=np.float32)
            else:
                first_val = history_ret_norm[0] if len(history_ret_norm) > 0 else 0.0
                pad = np.full((context - len(history_ret_norm),), first_val, dtype=np.float32)
                x_ctx = np.concatenate([pad, np.asarray(history_ret_norm, dtype=np.float32)])

            xb = torch.from_numpy(x_ctx[None, :, None]).to(device)
            pred_ret_norm = float(model(xb).squeeze().item())

            pred_ret = pred_ret_norm * ret_std + ret_mean
            pred_ret = float(np.clip(pred_ret, -0.20, 0.20))
            pred_log = float(history_log[-1] + pred_ret)
            pred_val = float(np.exp(pred_log))
            if not np.isfinite(pred_val):
                pred_val = float(train_arr[-1])
            preds.append(pred_val)

            true_log = float(np.log(max(true_price, 1e-8)))
            true_ret = float(true_log - history_log[-1])
            history_log.append(true_log)
            history_ret_norm.append((true_ret - ret_mean) / ret_std)

    preds = np.asarray(preds, dtype=float)
    y_true = test_arr.astype(float)
    return metrics(y_true, preds), pd.DataFrame({"y_true": y_true, "y_pred": preds})


def fit_lstm_inference_model(
    train: pd.Series,
    context_len: int = 96,
    hidden_size: int = 64,
    num_layers: int = 2,
    dropout: float = 0.1,
    epochs: int = 20,
    batch_size: int = 64,
    lr: float = 1e-3,
    weight_decay: float = 1e-4,
    use_cuda: bool = True,
) -> Dict[str, Any]:
    train_arr = pd.to_numeric(train, errors="coerce").dropna().astype(float).values
    if len(train_arr) < 80:
        raise RuntimeError("Слишком мало train для LSTM")

    train_log = np.log(np.clip(train_arr, 1e-8, None))
    train_ret = np.diff(train_log)
    ret_mean = float(train_ret.mean())
    ret_std = float(train_ret.std() + 1e-8)
    train_ret_norm = (train_ret - ret_mean) / ret_std

    context = int(min(max(24, context_len), max(24, len(train_ret_norm) - 5)))
    n_samples = len(train_ret_norm) - context
    if n_samples < 24:
        raise RuntimeError("Слишком мало окон для обучения LSTM")

    x_np = np.stack([train_ret_norm[i : i + context] for i in range(n_samples)]).astype(np.float32)
    y_np = np.asarray([train_ret_norm[i + context] for i in range(n_samples)], dtype=np.float32)[:, None]
    x_t = torch.from_numpy(x_np)[:, :, None]
    y_t = torch.from_numpy(y_np)

    cuda_exist = torch.cuda.is_available() and bool(use_cuda)
    device = torch.device("cuda" if cuda_exist else "cpu")

    model = _LSTMForecaster(
        input_size=1,
        hidden_size=max(16, int(hidden_size)),
        num_layers=max(1, int(num_layers)),
        dropout=float(max(0.0, min(0.5, dropout))),
    ).to(device)

    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=weight_decay)
    loss_fn = torch.nn.SmoothL1Loss()
    ds = torch.utils.data.TensorDataset(x_t, y_t)
    dl = torch.utils.data.DataLoader(ds, batch_size=max(16, batch_size), shuffle=True, pin_memory=cuda_exist)

    model.train()
    for _ in range(1, max(1, int(epochs)) + 1):
        for xb, yb in dl:
            xb = xb.to(device, non_blocking=cuda_exist)
            yb = yb.to(device, non_blocking=cuda_exist)
            opt.zero_grad(set_to_none=True)
            pred = model(xb)
            loss = loss_fn(pred, yb)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()

    return {
        "model_type": "lstm",
        "context": int(context),
        "ret_mean": float(ret_mean),
        "ret_std": float(ret_std),
        "train_last": float(train_arr[-1]),
        "state_dict": {k: v.detach().cpu().clone() for k, v in model.state_dict().items()},
        "arch": {
            "hidden_size": int(max(16, int(hidden_size))),
            "num_layers": int(max(1, int(num_layers))),
            "dropout": float(max(0.0, min(0.5, dropout))),
        },
        "use_cuda": bool(cuda_exist),
    }


def predict_lstm_inference(model_obj: Dict[str, Any], train: pd.Series, test: pd.Series) -> Tuple[Dict[str, float], pd.DataFrame]:
    train_arr = pd.to_numeric(train, errors="coerce").dropna().astype(float).values
    test_arr = pd.to_numeric(test, errors="coerce").dropna().astype(float).values

    context = int(model_obj["context"])
    ret_mean = float(model_obj["ret_mean"])
    ret_std = float(model_obj["ret_std"])
    cuda_exist = bool(model_obj.get("use_cuda", False)) and torch.cuda.is_available()
    device = torch.device("cuda" if cuda_exist else "cpu")

    arch = model_obj["arch"]
    model = _LSTMForecaster(
        input_size=1,
        hidden_size=int(arch["hidden_size"]),
        num_layers=int(arch["num_layers"]),
        dropout=float(arch["dropout"]),
    ).to(device)
    model.load_state_dict(model_obj["state_dict"])
    model.eval()

    history_log = list(np.log(np.clip(train_arr, 1e-8, None)).astype(float))
    history_ret_norm = list(((np.diff(np.log(np.clip(train_arr, 1e-8, None))) - ret_mean) / ret_std).astype(float))

    preds = []
    with torch.no_grad():
        for true_price in test_arr:
            if len(history_ret_norm) >= context:
                x_ctx = np.asarray(history_ret_norm[-context:], dtype=np.float32)
            else:
                first_val = history_ret_norm[0] if len(history_ret_norm) > 0 else 0.0
                pad = np.full((context - len(history_ret_norm),), first_val, dtype=np.float32)
                x_ctx = np.concatenate([pad, np.asarray(history_ret_norm, dtype=np.float32)])

            xb = torch.from_numpy(x_ctx[None, :, None]).to(device)
            pred_ret_norm = float(model(xb).squeeze().item())

            pred_ret = pred_ret_norm * ret_std + ret_mean
            pred_ret = float(np.clip(pred_ret, -0.20, 0.20))
            pred_log = float(history_log[-1] + pred_ret)
            pred_val = float(np.exp(pred_log))
            if not np.isfinite(pred_val):
                pred_val = float(model_obj["train_last"])
            preds.append(pred_val)

            true_log = float(np.log(max(float(true_price), 1e-8)))
            true_ret = float(true_log - history_log[-1])
            history_log.append(true_log)
            history_ret_norm.append((true_ret - ret_mean) / ret_std)

    preds = np.asarray(preds, dtype=float)
    return metrics(test_arr, preds), pd.DataFrame({"y_true": test_arr, "y_pred": preds})


def run_lstm_grid_search(
    train: pd.Series,
    param_grid: dict,
    validation_ratio: float = 0.2,
    scoring: str = "MAE",
    use_cuda: bool = True,
    n_jobs: int = 1,
):
    allowed_scoring = {"MAE", "RMSE", "MAPE"}
    if scoring not in allowed_scoring:
        raise ValueError(f"scoring должен быть одним из {sorted(allowed_scoring)}")

    train_arr = pd.to_numeric(train, errors="coerce").dropna().astype(float).values
    if len(train_arr) < 120:
        raise RuntimeError("Слишком мало данных для grid search LSTM")

    val_size = int(max(16, len(train_arr) * float(validation_ratio)))
    if val_size >= len(train_arr) - 24:
        val_size = max(16, len(train_arr) // 4)

    split_idx = len(train_arr) - val_size
    train_sub = pd.Series(train_arr[:split_idx])
    val_sub = pd.Series(train_arr[split_idx:])

    keys = list(param_grid.keys())
    value_lists = [list(param_grid[k]) for k in keys]
    if len(keys) == 0 or any(len(v) == 0 for v in value_lists):
        raise ValueError("param_grid должен содержать хотя бы один параметр и непустые списки значений")

    combinations = list(product(*value_lists))
    total = len(combinations)

    jobs = max(1, int(n_jobs))

    print(f"LSTM GridSearch: combinations={total} | scoring={scoring} | n_jobs={jobs}")

    rows = []

    def _evaluate_combo(idx: int, values_tuple):
        params = dict(zip(keys, values_tuple))
        run_params = {
            "context_len": int(params.get("context_len", 72)),
            "hidden_size": int(params.get("hidden_size", 96)),
            "num_layers": int(params.get("num_layers", 2)),
            "dropout": float(params.get("dropout", 0.05)),
            "epochs": int(params.get("epochs", 20)),
            "batch_size": int(params.get("batch_size", 32)),
            "lr": float(params.get("lr", 6e-4)),
            "weight_decay": float(params.get("weight_decay", 1e-4)),
            "use_cuda": bool(params.get("use_cuda", use_cuda)),
            "show_progress": False,
        }

        try:
            m, _ = run_lstm(train_sub, val_sub, **run_params)
            score = float(m[scoring])
            row = {
                "combination": idx,
                **params,
                "MAE": float(m["MAE"]),
                "RMSE": float(m["RMSE"]),
                "MAPE": float(m["MAPE"]),
                "score": score,
            }
        except Exception as ex:
            row = {
                "combination": idx,
                **params,
                "MAE": np.nan,
                "RMSE": np.nan,
                "MAPE": np.nan,
                "score": np.nan,
                "error": str(ex),
            }
        return row

    if jobs == 1:
        for idx, values in enumerate(combinations, start=1):
            row = _evaluate_combo(idx, values)
            rows.append(row)
            if np.isfinite(float(row.get("score", np.nan))):
                print(f"LSTM GridSearch: {idx}/{total} | {scoring}={float(row['score']):.6f}")
            else:
                print(f"LSTM GridSearch: {idx}/{total} | error")
    else:
        with ThreadPoolExecutor(max_workers=jobs) as ex:
            futures = {
                ex.submit(_evaluate_combo, idx, values): idx
                for idx, values in enumerate(combinations, start=1)
            }
            done = 0
            for fut in as_completed(futures):
                row = fut.result()
                rows.append(row)
                done += 1
                if np.isfinite(float(row.get("score", np.nan))):
                    print(f"LSTM GridSearch: {done}/{total} done | {scoring}={float(row['score']):.6f}")
                else:
                    print(f"LSTM GridSearch: {done}/{total} done | error")

    results_df = pd.DataFrame(rows).sort_values(["score"], na_position="last").reset_index(drop=True)
    valid_df = results_df[np.isfinite(results_df["score"])]
    if len(valid_df) == 0:
        raise RuntimeError("Grid search не нашёл валидных комбинаций для LSTM")

    best_row = valid_df.iloc[0]
    best_params = {}
    for k in keys:
        value = best_row[k]
        if isinstance(value, np.generic):
            value = value.item()
        template_values = param_grid.get(k, [])
        if len(template_values) > 0:
            template = template_values[0]
            if isinstance(template, bool):
                value = bool(value)
            elif isinstance(template, int) and not isinstance(template, bool):
                value = int(round(float(value)))
            elif isinstance(template, float):
                value = float(value)
        best_params[k] = value
    print(f"LSTM GridSearch: best {scoring}={float(best_row['score']):.6f} | {best_params}")

    return best_params, results_df


def run_lstm_chunked_cv_search(
    series: pd.Series,
    param_grid: dict,
    n_chunks: int = 10,
    chunk_train_ratio: float = 0.8,
    scoring: str = "MAE",
    use_cuda: bool = True,
    n_jobs: int = 1,
    min_chunk_size: int = 240,
):
    allowed_scoring = {"MAE", "RMSE", "MAPE"}
    if scoring not in allowed_scoring:
        raise ValueError(f"scoring должен быть одним из {sorted(allowed_scoring)}")

    values = pd.to_numeric(series, errors="coerce").dropna().astype(float).values
    if len(values) < max(1200, int(min_chunk_size) * 2):
        raise RuntimeError("Слишком мало данных для chunked CV LSTM")

    n_chunks = int(max(2, n_chunks))
    raw_chunks = np.array_split(values, n_chunks)

    folds = []
    fold_meta_rows = []
    for i, chunk_arr in enumerate(raw_chunks, start=1):
        chunk_arr = np.asarray(chunk_arr, dtype=float)
        if len(chunk_arr) < int(min_chunk_size):
            continue

        split_idx = int(len(chunk_arr) * float(chunk_train_ratio))
        split_idx = max(64, min(split_idx, len(chunk_arr) - 24))
        if split_idx <= 0 or split_idx >= len(chunk_arr):
            continue

        train_fold = pd.Series(chunk_arr[:split_idx])
        val_fold = pd.Series(chunk_arr[split_idx:])
        if len(train_fold) < 80 or len(val_fold) < 16:
            continue

        folds.append((train_fold, val_fold))
        fold_meta_rows.append(
            {
                "chunk": i,
                "chunk_points": int(len(chunk_arr)),
                "train_points": int(len(train_fold)),
                "val_points": int(len(val_fold)),
            }
        )

    if len(folds) < 2:
        raise RuntimeError("Недостаточно валидных chunk-folds для LSTM CV")

    keys = list(param_grid.keys())
    value_lists = [list(param_grid[k]) for k in keys]
    if len(keys) == 0 or any(len(v) == 0 for v in value_lists):
        raise ValueError("param_grid должен содержать хотя бы один параметр и непустые списки значений")

    combinations = list(product(*value_lists))
    total = len(combinations)
    jobs = max(1, int(n_jobs))
    if bool(use_cuda) and jobs > 1:
        print("LSTM chunked CV: use_cuda=True, n_jobs принудительно установлен в 1 (безопасный режим для GPU).")
        jobs = 1

    print(
        f"LSTM chunked CV: points={len(values)} | folds={len(folds)} | combinations={total} | scoring={scoring} | n_jobs={jobs}"
    )

    def _eval_combo(idx: int, combo_values):
        params = dict(zip(keys, combo_values))
        run_params = {
            "context_len": int(params.get("context_len", 72)),
            "hidden_size": int(params.get("hidden_size", 96)),
            "num_layers": int(params.get("num_layers", 2)),
            "dropout": float(params.get("dropout", 0.05)),
            "epochs": int(params.get("epochs", 20)),
            "batch_size": int(params.get("batch_size", 32)),
            "lr": float(params.get("lr", 6e-4)),
            "weight_decay": float(params.get("weight_decay", 1e-4)),
            "use_cuda": bool(params.get("use_cuda", use_cuda)),
            "show_progress": False,
        }

        fold_scores = []
        fold_mae = []
        fold_rmse = []
        fold_mape = []
        errors = []

        for fold_idx, (fold_train, fold_val) in enumerate(folds, start=1):
            try:
                m, _ = run_lstm(fold_train, fold_val, **run_params)
                fold_scores.append(float(m[scoring]))
                fold_mae.append(float(m["MAE"]))
                fold_rmse.append(float(m["RMSE"]))
                fold_mape.append(float(m["MAPE"]))
            except Exception as ex:
                errors.append(f"fold{fold_idx}: {ex}")

        valid = len(fold_scores)
        if valid == 0:
            return {
                "combination": idx,
                **params,
                "score": np.nan,
                "score_std": np.nan,
                "MAE": np.nan,
                "RMSE": np.nan,
                "MAPE": np.nan,
                "folds_used": 0,
                "error": " | ".join(errors) if errors else "all folds failed",
            }

        return {
            "combination": idx,
            **params,
            "score": float(np.mean(fold_scores)),
            "score_std": float(np.std(fold_scores)),
            "MAE": float(np.mean(fold_mae)),
            "RMSE": float(np.mean(fold_rmse)),
            "MAPE": float(np.mean(fold_mape)),
            "folds_used": int(valid),
            "error": " | ".join(errors) if errors else "",
        }

    rows = []
    if jobs == 1:
        for idx, combo_values in enumerate(combinations, start=1):
            row = _eval_combo(idx, combo_values)
            rows.append(row)
            if np.isfinite(float(row.get("score", np.nan))):
                print(
                    f"LSTM chunked CV: {idx}/{total} | {scoring}={float(row['score']):.6f} +- {float(row['score_std']):.6f} | folds={int(row['folds_used'])}"
                )
            else:
                print(f"LSTM chunked CV: {idx}/{total} | error")
    else:
        with ThreadPoolExecutor(max_workers=jobs) as ex:
            futures = {
                ex.submit(_eval_combo, idx, combo_values): idx
                for idx, combo_values in enumerate(combinations, start=1)
            }
            done = 0
            for fut in as_completed(futures):
                row = fut.result()
                rows.append(row)
                done += 1
                if np.isfinite(float(row.get("score", np.nan))):
                    print(
                        f"LSTM chunked CV: {done}/{total} done | {scoring}={float(row['score']):.6f} +- {float(row['score_std']):.6f}"
                    )
                else:
                    print(f"LSTM chunked CV: {done}/{total} done | error")

    results_df = pd.DataFrame(rows).sort_values(["score", "score_std"], na_position="last").reset_index(drop=True)
    valid_df = results_df[np.isfinite(results_df["score"])]
    if len(valid_df) == 0:
        raise RuntimeError("Chunked CV не нашёл валидных комбинаций для LSTM")

    best_row = valid_df.iloc[0]
    best_params = {}
    for k in keys:
        value = best_row[k]
        if isinstance(value, np.generic):
            value = value.item()
        template_values = param_grid.get(k, [])
        if len(template_values) > 0:
            template = template_values[0]
            if isinstance(template, bool):
                value = bool(value)
            elif isinstance(template, int) and not isinstance(template, bool):
                value = int(round(float(value)))
            elif isinstance(template, float):
                value = float(value)
        best_params[k] = value

    fold_meta_df = pd.DataFrame(fold_meta_rows)
    print(
        f"LSTM chunked CV: best {scoring}={float(best_row['score']):.6f} +- {float(best_row['score_std']):.6f} | {best_params}"
    )
    return best_params, results_df, fold_meta_df


def run_lstm_chunked_cv_pipeline(
    full_series: pd.Series,
    param_grid: dict,
    test_ratio: float = 0.2,
    n_chunks: int = 10,
    chunk_train_ratio: float = 0.8,
    scoring: str = "MAE",
    use_cuda: bool = True,
    n_jobs: int = 1,
    min_chunk_size: int = 240,
):
    values = pd.to_numeric(full_series, errors="coerce").dropna().astype(float).values
    if len(values) < 400:
        raise RuntimeError("Слишком мало данных для LSTM chunked CV pipeline")

    split_idx = int(len(values) * (1.0 - float(test_ratio)))
    split_idx = max(80, min(split_idx, len(values) - 24))
    if split_idx <= 0 or split_idx >= len(values):
        raise RuntimeError("Некорректное разбиение train/test для LSTM pipeline")

    train = pd.Series(values[:split_idx]).reset_index(drop=True)
    test = pd.Series(values[split_idx:]).reset_index(drop=True)

    best_params, cv_results_df, folds_df = run_lstm_chunked_cv_search(
        series=train,
        param_grid=param_grid,
        n_chunks=n_chunks,
        chunk_train_ratio=chunk_train_ratio,
        scoring=scoring,
        use_cuda=use_cuda,
        n_jobs=n_jobs,
        min_chunk_size=min_chunk_size,
    )

    model_metrics, pred_df = run_lstm(
        train,
        test,
        context_len=best_params["context_len"],
        hidden_size=best_params["hidden_size"],
        num_layers=best_params["num_layers"],
        dropout=best_params["dropout"],
        epochs=best_params["epochs"],
        batch_size=best_params["batch_size"],
        lr=best_params["lr"],
        use_cuda=use_cuda,
        show_progress=True,
    )

    split_info = {
        "full_points": int(len(values)),
        "train_points": int(len(train)),
        "test_points": int(len(test)),
        "test_ratio": float(test_ratio),
    }

    return best_params, cv_results_df, folds_df, model_metrics, pred_df, split_info


def run_lstm_gridsearchcv_native_pipeline(
    full_series: pd.Series,
    param_grid: dict,
    test_ratio: float = 0.2,
    n_splits: int = 5,
    scoring: str = "MAE",
    use_cuda: bool = True,
    n_jobs: int = 1,
):
    if not _SKLEARN_AVAILABLE:
        raise RuntimeError("Для native GridSearchCV нужен scikit-learn (pip install scikit-learn)")

    valid_scoring = {"MAE", "RMSE", "MAPE"}
    if scoring not in valid_scoring:
        raise ValueError(f"scoring должен быть одним из {sorted(valid_scoring)}")

    values = pd.to_numeric(full_series, errors="coerce").dropna().astype(float).values
    if len(values) < 400:
        raise RuntimeError("Слишком мало данных для LSTM GridSearchCV pipeline")

    split_idx = int(len(values) * (1.0 - float(test_ratio)))
    split_idx = max(80, min(split_idx, len(values) - 24))
    if split_idx <= 0 or split_idx >= len(values):
        raise RuntimeError("Некорректное разбиение train/test для LSTM GridSearchCV pipeline")

    train = pd.Series(values[:split_idx]).reset_index(drop=True)
    test = pd.Series(values[split_idx:]).reset_index(drop=True)

    jobs = max(1, int(n_jobs))
    if bool(use_cuda) and jobs > 1:
        print("LSTM GridSearchCV: use_cuda=True, n_jobs принудительно установлен в 1 (безопасный режим для GPU).")
        jobs = 1

    # GridSearchCV needs feature matrix; for this sequence model we use dummy time indices.
    x_train = np.arange(len(train), dtype=np.float32)[:, None]
    y_train = train.values.astype(float)

    ts_splits = max(2, min(int(n_splits), max(2, len(train) // 120)))
    cv = TimeSeriesSplit(n_splits=ts_splits)

    estimator = _LSTMGridSearchEstimator(use_cuda=bool(use_cuda), score_metric=scoring)

    grid = GridSearchCV(
        estimator=estimator,
        param_grid=param_grid,
        scoring=None,
        cv=cv,
        n_jobs=jobs,
        refit=True,
        verbose=_grid_verbose_level(),
        return_train_score=False,
    )

    print(f"LSTM GridSearchCV: train_points={len(train)} | test_points={len(test)} | n_splits={ts_splits}")
    _fit_grid_with_progress(grid, x_train, y_train, param_grid, ts_splits, "LSTM")

    cv_results_df = pd.DataFrame(grid.cv_results_)
    cv_results_df["score"] = -cv_results_df["mean_test_score"]
    cv_results_df["score_std"] = cv_results_df["std_test_score"]
    cv_results_df = cv_results_df.sort_values(["score", "score_std"], na_position="last").reset_index(drop=True)

    best_params = dict(grid.best_params_)
    print(f"LSTM GridSearchCV: best {scoring}={float(-grid.best_score_):.6f} | {best_params}")

    model_metrics, pred_df = run_lstm(
        train,
        test,
        context_len=int(best_params["context_len"]),
        hidden_size=int(best_params["hidden_size"]),
        num_layers=int(best_params["num_layers"]),
        dropout=float(best_params["dropout"]),
        epochs=int(best_params["epochs"]),
        batch_size=int(best_params["batch_size"]),
        lr=float(best_params["lr"]),
        weight_decay=float(best_params.get("weight_decay", 1e-4)),
        use_cuda=bool(use_cuda),
        show_progress=True,
    )

    split_info = {
        "full_points": int(len(values)),
        "train_points": int(len(train)),
        "test_points": int(len(test)),
        "test_ratio": float(test_ratio),
        "cv_n_splits": int(ts_splits),
    }

    return best_params, cv_results_df, model_metrics, pred_df, split_info


def _split_series_for_gridsearch(full_series: pd.Series, test_ratio: float, min_train: int = 80, min_test: int = 24):
    values = pd.to_numeric(full_series, errors="coerce").dropna().astype(float).values
    if len(values) < (min_train + min_test + 20):
        raise RuntimeError("Слишком мало данных для GridSearchCV pipeline")

    split_idx = int(len(values) * (1.0 - float(test_ratio)))
    split_idx = max(int(min_train), min(split_idx, len(values) - int(min_test)))
    if split_idx <= 0 or split_idx >= len(values):
        raise RuntimeError("Некорректное разбиение train/test для GridSearchCV pipeline")

    train = pd.Series(values[:split_idx]).reset_index(drop=True)
    test = pd.Series(values[split_idx:]).reset_index(drop=True)
    return values, train, test


def run_arima_gridsearchcv_native_pipeline(
    full_series: pd.Series,
    param_grid: dict,
    test_ratio: float = 0.2,
    n_splits: int = 5,
    scoring: str = "MAE",
    n_jobs: int = 1,
):
    if not _SKLEARN_AVAILABLE:
        raise RuntimeError("Для native GridSearchCV нужен scikit-learn (pip install scikit-learn)")

    valid_scoring = {"MAE", "RMSE", "MAPE"}
    if scoring not in valid_scoring:
        raise ValueError(f"scoring должен быть одним из {sorted(valid_scoring)}")

    values, train, test = _split_series_for_gridsearch(full_series, test_ratio, min_train=80, min_test=24)

    x_train = np.arange(len(train), dtype=np.float32)[:, None]
    y_train = train.values.astype(float)

    ts_splits = max(2, min(int(n_splits), max(2, len(train) // 100)))
    cv = TimeSeriesSplit(n_splits=ts_splits)

    estimator = _ARIMAGridSearchEstimator(score_metric=scoring)
    grid = GridSearchCV(
        estimator=estimator,
        param_grid=param_grid,
        scoring=None,
        cv=cv,
        n_jobs=max(1, int(n_jobs)),
        refit=True,
        verbose=_grid_verbose_level(),
        return_train_score=False,
    )

    print(f"ARIMA GridSearchCV: train_points={len(train)} | test_points={len(test)} | n_splits={ts_splits}")
    _fit_grid_with_progress(grid, x_train, y_train, param_grid, ts_splits, "ARIMA")

    cv_results_df = pd.DataFrame(grid.cv_results_)
    cv_results_df["score"] = -cv_results_df["mean_test_score"]
    cv_results_df["score_std"] = cv_results_df["std_test_score"]
    cv_results_df = cv_results_df.sort_values(["score", "score_std"], na_position="last").reset_index(drop=True)

    best_params = dict(grid.best_params_)
    order = (int(best_params["p"]), int(best_params["d"]), int(best_params["q"]))
    refit_every = int(best_params.get("refit_every", 24))

    print(f"ARIMA GridSearchCV: best {scoring}={float(-grid.best_score_):.6f} | order={order}, refit_every={refit_every}")
    model_metrics, pred_df = run_arima(train, test, order=order, refit_every=refit_every, show_progress=True)

    split_info = {
        "full_points": int(len(values)),
        "train_points": int(len(train)),
        "test_points": int(len(test)),
        "test_ratio": float(test_ratio),
        "cv_n_splits": int(ts_splits),
    }
    return best_params, cv_results_df, model_metrics, pred_df, split_info


def run_sarima_gridsearchcv_native_pipeline(
    full_series: pd.Series,
    param_grid: dict,
    test_ratio: float = 0.2,
    n_splits: int = 4,
    scoring: str = "MAE",
    n_jobs: int = 1,
):
    if not _SKLEARN_AVAILABLE:
        raise RuntimeError("Для native GridSearchCV нужен scikit-learn (pip install scikit-learn)")

    valid_scoring = {"MAE", "RMSE", "MAPE"}
    if scoring not in valid_scoring:
        raise ValueError(f"scoring должен быть одним из {sorted(valid_scoring)}")

    values, train, test = _split_series_for_gridsearch(full_series, test_ratio, min_train=120, min_test=24)

    x_train = np.arange(len(train), dtype=np.float32)[:, None]
    y_train = train.values.astype(float)

    ts_splits = max(2, min(int(n_splits), max(2, len(train) // 120)))
    cv = TimeSeriesSplit(n_splits=ts_splits)

    estimator = _SARIMAGridSearchEstimator(score_metric=scoring)
    grid = GridSearchCV(
        estimator=estimator,
        param_grid=param_grid,
        scoring=None,
        cv=cv,
        n_jobs=max(1, int(n_jobs)),
        refit=True,
        verbose=_grid_verbose_level(),
        return_train_score=False,
    )

    print(f"SARIMA GridSearchCV: train_points={len(train)} | test_points={len(test)} | n_splits={ts_splits}")
    _fit_grid_with_progress(grid, x_train, y_train, param_grid, ts_splits, "SARIMA")

    cv_results_df = pd.DataFrame(grid.cv_results_)
    cv_results_df["score"] = -cv_results_df["mean_test_score"]
    cv_results_df["score_std"] = cv_results_df["std_test_score"]
    cv_results_df = cv_results_df.sort_values(["score", "score_std"], na_position="last").reset_index(drop=True)

    best_params = dict(grid.best_params_)
    order = (int(best_params["p"]), int(best_params["d"]), int(best_params["q"]))
    seasonal_order = (int(best_params["sp"]), int(best_params["sd"]), int(best_params["sq"]), int(best_params["s"]))
    refit_every = int(best_params.get("refit_every", 48))
    fit_window = int(best_params.get("fit_window", 1000))
    maxiter = int(best_params.get("maxiter", 50))

    print(
        f"SARIMA GridSearchCV: best {scoring}={float(-grid.best_score_):.6f} | order={order}, seasonal={seasonal_order}"
    )
    model_metrics, pred_df = run_sarima(
        train,
        test,
        order=order,
        seasonal_order=seasonal_order,
        refit_every=refit_every,
        fit_window=fit_window,
        maxiter=maxiter,
        show_progress=True,
        use_cuda=False,
    )

    split_info = {
        "full_points": int(len(values)),
        "train_points": int(len(train)),
        "test_points": int(len(test)),
        "test_ratio": float(test_ratio),
        "cv_n_splits": int(ts_splits),
    }
    return best_params, cv_results_df, model_metrics, pred_df, split_info


def run_naive_gridsearchcv_native_pipeline(
    full_series: pd.Series,
    param_grid: dict | None = None,
    test_ratio: float = 0.2,
    n_splits: int = 5,
    scoring: str = "MAE",
    n_jobs: int = 1,
):
    if not _SKLEARN_AVAILABLE:
        raise RuntimeError("Для native GridSearchCV нужен scikit-learn (pip install scikit-learn)")

    valid_scoring = {"MAE", "RMSE", "MAPE"}
    if scoring not in valid_scoring:
        raise ValueError(f"scoring должен быть одним из {sorted(valid_scoring)}")

    if param_grid is None:
        param_grid = {"strategy": ["last"]}

    values, train, test = _split_series_for_gridsearch(full_series, test_ratio, min_train=40, min_test=10)
    x_train = np.arange(len(train), dtype=np.float32)[:, None]
    y_train = train.values.astype(float)

    ts_splits = max(2, min(int(n_splits), max(2, len(train) // 80)))
    cv = TimeSeriesSplit(n_splits=ts_splits)
    estimator = _NaiveGridSearchEstimator(score_metric=scoring)

    grid = GridSearchCV(
        estimator=estimator,
        param_grid=param_grid,
        scoring=None,
        cv=cv,
        n_jobs=max(1, int(n_jobs)),
        refit=True,
        verbose=_grid_verbose_level(),
        return_train_score=False,
    )

    print(f"NAIVE GridSearchCV: train_points={len(train)} | test_points={len(test)} | n_splits={ts_splits}")
    _fit_grid_with_progress(grid, x_train, y_train, param_grid, ts_splits, "NAIVE")

    cv_results_df = pd.DataFrame(grid.cv_results_)
    cv_results_df["score"] = -cv_results_df["mean_test_score"]
    cv_results_df["score_std"] = cv_results_df["std_test_score"]
    cv_results_df = cv_results_df.sort_values(["score", "score_std"], na_position="last").reset_index(drop=True)

    best_params = dict(grid.best_params_)
    model_metrics, pred_df = run_naive(train, test)

    split_info = {
        "full_points": int(len(values)),
        "train_points": int(len(train)),
        "test_points": int(len(test)),
        "test_ratio": float(test_ratio),
        "cv_n_splits": int(ts_splits),
    }
    return best_params, cv_results_df, model_metrics, pred_df, split_info


def run_nbeats_gridsearchcv_native_pipeline(
    full_series: pd.Series,
    param_grid: dict,
    test_ratio: float = 0.2,
    n_splits: int = 4,
    scoring: str = "MAE",
    use_cuda: bool = True,
    n_jobs: int = 1,
):
    if not _SKLEARN_AVAILABLE:
        raise RuntimeError("Для native GridSearchCV нужен scikit-learn (pip install scikit-learn)")

    valid_scoring = {"MAE", "RMSE", "MAPE"}
    if scoring not in valid_scoring:
        raise ValueError(f"scoring должен быть одним из {sorted(valid_scoring)}")

    values, train, test = _split_series_for_gridsearch(full_series, test_ratio, min_train=160, min_test=24)

    jobs = max(1, int(n_jobs))
    if bool(use_cuda) and jobs > 1:
        print("NBEATS GridSearchCV: use_cuda=True, n_jobs принудительно установлен в 1 (безопасный режим для GPU).")
        jobs = 1

    x_train = np.arange(len(train), dtype=np.float32)[:, None]
    y_train = train.values.astype(float)

    ts_splits = max(2, min(int(n_splits), max(2, len(train) // 140)))
    cv = TimeSeriesSplit(n_splits=ts_splits)
    estimator = _NBEATSGridSearchEstimator(use_cuda=bool(use_cuda), score_metric=scoring)

    grid = GridSearchCV(
        estimator=estimator,
        param_grid=param_grid,
        scoring=None,
        cv=cv,
        n_jobs=jobs,
        refit=True,
        verbose=_grid_verbose_level(),
        return_train_score=False,
    )

    print(f"NBEATS GridSearchCV: train_points={len(train)} | test_points={len(test)} | n_splits={ts_splits}")
    _fit_grid_with_progress(grid, x_train, y_train, param_grid, ts_splits, "NBEATS")

    cv_results_df = pd.DataFrame(grid.cv_results_)
    cv_results_df["score"] = -cv_results_df["mean_test_score"]
    cv_results_df["score_std"] = cv_results_df["std_test_score"]
    cv_results_df = cv_results_df.sort_values(["score", "score_std"], na_position="last").reset_index(drop=True)

    best_params = dict(grid.best_params_)
    print(f"NBEATS GridSearchCV: best {scoring}={float(-grid.best_score_):.6f} | {best_params}")

    model_metrics, pred_df = run_nbeats(
        train,
        test,
        context_len=int(best_params["context_len"]),
        n_blocks=int(best_params["n_blocks"]),
        layers=int(best_params["layers"]),
        layer_size=int(best_params["layer_size"]),
        epochs=int(best_params["epochs"]),
        batch_size=int(best_params["batch_size"]),
        lr=float(best_params["lr"]),
        weight_decay=float(best_params.get("weight_decay", 1e-4)),
        use_cuda=bool(use_cuda),
        show_progress=True,
    )

    split_info = {
        "full_points": int(len(values)),
        "train_points": int(len(train)),
        "test_points": int(len(test)),
        "test_ratio": float(test_ratio),
        "cv_n_splits": int(ts_splits),
    }
    return best_params, cv_results_df, model_metrics, pred_df, split_info
