from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Dict, Tuple

import numpy as np
import pandas as pd
import torch

try:
    from catboost import CatBoostRegressor
except Exception:
    CatBoostRegressor = None

try:
    from sklearn.experimental import enable_halving_search_cv  # noqa: F401
    from sklearn.base import BaseEstimator
    from sklearn.model_selection import HalvingGridSearchCV, ParameterGrid, TimeSeriesSplit

    _SKLEARN_AVAILABLE = True
except Exception:
    BaseEstimator = object
    HalvingGridSearchCV = None
    ParameterGrid = None
    TimeSeriesSplit = None
    _SKLEARN_AVAILABLE = False

try:
    from tqdm import tqdm
except Exception:
    tqdm = None


_PROGRESS_HOOK = None


def _progress_step(step: int = 1):
    hook = _PROGRESS_HOOK
    if hook is None:
        return
    try:
        hook(max(0, int(step)))
    except Exception:
        pass


@contextmanager
def _tqdm_joblib(total: int, desc: str):
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
        if pbar.n < pbar.total:
            pbar.update(pbar.total - pbar.n)
        pbar.close()


def _grid_verbose_level() -> int:
    return 1 if tqdm is not None else 2


def _estimate_halving_total_fits(param_grid: dict | list[dict], ts_splits: int, min_resources: int, max_resources: int, factor: int) -> int:
    if ParameterGrid is None:
        return 0

    total_candidates = int(len(ParameterGrid(param_grid)))
    if total_candidates <= 0:
        return 0

    candidates = total_candidates
    resource = max(1, int(min_resources))
    max_resources = max(resource, int(max_resources))
    factor = max(2, int(factor))
    total_fits = 0

    while True:
        total_fits += candidates * int(ts_splits)
        if candidates <= 1 or resource >= max_resources:
            break
        next_resource = resource * factor
        if next_resource > max_resources:
            break
        resource = next_resource
        candidates = max(1, (candidates + factor - 1) // factor)

    return int(total_fits)


def _fit_grid_with_progress(grid, x_train, y_train, param_grid: dict | list[dict], ts_splits: int, label: str):
    if ParameterGrid is None:
        grid.fit(x_train, y_train)
        return

    factor = int(getattr(grid, "factor", 3) or 3)
    min_resources = int(getattr(grid, "min_resources_", getattr(grid, "min_resources", 1)) or 1)
    max_resources = int(getattr(grid, "max_resources_", getattr(grid, "max_resources", len(y_train))) or len(y_train))
    total_fits = _estimate_halving_total_fits(param_grid, ts_splits, min_resources, max_resources, factor)

    try:
        jobs = int(getattr(grid, "n_jobs", 1) or 1)
    except Exception:
        jobs = 1

    if jobs == 1:
        global _PROGRESS_HOOK
        pbar = tqdm(total=max(0, int(total_fits)), desc=f"{label} HalvingGridSearchCV", unit="fit") if tqdm is not None else None
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

    with _tqdm_joblib(total=total_fits, desc=f"{label} HalvingGridSearchCV"):
        grid.fit(x_train, y_train)


def _make_halving_search(
    *,
    estimator,
    param_grid: dict | list[dict],
    cv,
    n_jobs: int,
    min_resources: int,
    max_resources: int,
    factor: int = 3,
    aggressive_elimination: bool = False,
):
    if HalvingGridSearchCV is None:
        raise RuntimeError("HalvingGridSearchCV недоступен: нужен scikit-learn с experimental halving search")

    return HalvingGridSearchCV(
        estimator=estimator,
        param_grid=param_grid,
        factor=max(2, int(factor)),
        resource="resource_points",
        min_resources=max(1, int(min_resources)),
        max_resources=max(int(min_resources), int(max_resources)),
        scoring=None,
        cv=cv,
        n_jobs=max(1, int(n_jobs)),
        refit=True,
        verbose=_grid_verbose_level(),
        return_train_score=False,
        aggressive_elimination=bool(aggressive_elimination),
    )


def _slice_train_for_resource(train_series: pd.Series, resource_points: int, min_points: int) -> pd.Series:
    resource_points = int(max(min_points, resource_points))
    if len(train_series) <= resource_points:
        return train_series.reset_index(drop=True)
    return train_series.iloc[-resource_points:].reset_index(drop=True)


def _public_search_params(best_params: dict) -> dict:
    return {key: value for key, value in dict(best_params).items() if key != "resource_points"}


def metrics(y_true: np.ndarray, y_pred: np.ndarray) -> Dict[str, float]:
    eps = 1e-8
    mae = float(np.mean(np.abs(y_true - y_pred)))
    rmse = float(np.sqrt(np.mean((y_true - y_pred) ** 2)))
    mape = float(np.mean(np.abs((y_true - y_pred) / np.maximum(np.abs(y_true), eps))) * 100.0)
    return {"MAE": mae, "RMSE": rmse, "MAPE": mape}


def _to_float_array(values: pd.Series | np.ndarray | list[float]) -> np.ndarray:
    if isinstance(values, pd.Series):
        return pd.to_numeric(values, errors="coerce").dropna().astype(float).to_numpy(copy=False)

    arr = np.asarray(values, dtype=float)
    if arr.ndim != 1:
        arr = arr.reshape(-1)
    return arr[np.isfinite(arr)]


def _build_sliding_windows(values: np.ndarray, context: int) -> np.ndarray:
    window_size = int(context) + 1
    windows = np.lib.stride_tricks.sliding_window_view(values, window_shape=window_size)
    return np.asarray(windows, dtype=np.float32)


def _prepare_catboost_training(train: pd.Series | np.ndarray | list[float], context_len: int) -> Dict[str, Any]:
    train_arr = _to_float_array(train)
    if len(train_arr) < 40:
        raise RuntimeError("Слишком мало train для CatBoost")

    train_log = np.log(np.clip(train_arr, 1e-8, None))
    train_ret = np.diff(train_log)
    ret_mean = float(train_ret.mean())
    ret_std = float(train_ret.std() + 1e-8)
    train_ret_norm = (train_ret - ret_mean) / ret_std

    max_context = max(12, len(train_ret_norm) - 8)
    context = int(min(max(12, context_len), max_context))
    n_samples = len(train_ret_norm) - context
    if n_samples < 8:
        raise RuntimeError("Слишком мало окон для обучения CatBoost")

    windows = _build_sliding_windows(train_ret_norm, context)
    x_np = windows[:, :-1]
    y_np = windows[:, -1]

    return {
        "train_arr": train_arr,
        "train_log": train_log,
        "train_ret_norm": train_ret_norm,
        "ret_mean": ret_mean,
        "ret_std": ret_std,
        "context": int(context),
        "x_np": x_np,
        "y_np": y_np,
    }


def _make_catboost_regressor(
    *,
    depth: int,
    learning_rate: float,
    iterations: int,
    l2_leaf_reg: float,
    use_cuda: bool,
):
    if CatBoostRegressor is None:
        raise RuntimeError("CatBoost недоступен: установи пакет catboost")

    cuda_exist = bool(use_cuda) and torch.cuda.is_available()
    params = {
        "depth": int(depth),
        "learning_rate": float(learning_rate),
        "iterations": int(iterations),
        "l2_leaf_reg": float(l2_leaf_reg),
        "loss_function": "RMSE",
        "eval_metric": "RMSE",
        "random_seed": 42,
        "allow_writing_files": False,
        "verbose": False,
    }
    if cuda_exist:
        params["task_type"] = "GPU"
        params["devices"] = "0"
    else:
        params["task_type"] = "CPU"
    return CatBoostRegressor(**params)


def run_catboost(
    train: pd.Series,
    test: pd.Series,
    context_len: int = 120,
    depth: int = 8,
    learning_rate: float = 0.05,
    iterations: int = 400,
    l2_leaf_reg: float = 3.0,
    use_cuda: bool = True,
    show_progress: bool = True,
) -> Tuple[Dict[str, float], pd.DataFrame]:
    train_arr = _to_float_array(train)
    test_arr = _to_float_array(test)

    if len(train_arr) < 40 or len(test_arr) < 5:
        raise RuntimeError("Слишком мало данных для CatBoost")

    prep = _prepare_catboost_training(train=train_arr, context_len=context_len)
    ret_mean = float(prep["ret_mean"])
    ret_std = float(prep["ret_std"])
    context = int(prep["context"])
    train_log = prep["train_log"]
    train_ret_norm = prep["train_ret_norm"]
    model = _make_catboost_regressor(
        depth=depth,
        learning_rate=learning_rate,
        iterations=iterations,
        l2_leaf_reg=l2_leaf_reg,
        use_cuda=use_cuda,
    )
    model.fit(prep["x_np"], prep["y_np"])

    if show_progress:
        device_label = "GPU" if (bool(use_cuda) and torch.cuda.is_available()) else "CPU"
        print(f"CatBoost device: {device_label}")

    history_log = list(train_log.astype(float))
    history_ret_norm = list(train_ret_norm.astype(float))
    preds = []
    for true_price in test_arr:
        if len(history_ret_norm) >= context:
            x_ctx = np.asarray(history_ret_norm[-context:], dtype=np.float32)
        else:
            first_val = history_ret_norm[0] if len(history_ret_norm) > 0 else 0.0
            pad = np.full((context - len(history_ret_norm),), first_val, dtype=np.float32)
            x_ctx = np.concatenate([pad, np.asarray(history_ret_norm, dtype=np.float32)])

        pred_ret_norm = float(model.predict(x_ctx.reshape(1, -1))[0])
        pred_ret = pred_ret_norm * ret_std + ret_mean
        pred_ret = float(np.clip(pred_ret, -0.20, 0.20))
        pred_log = float(history_log[-1] + pred_ret)
        pred_val = float(np.exp(pred_log))
        if not np.isfinite(pred_val):
            pred_val = float(train_arr[-1])
        preds.append(pred_val)

        true_log = float(np.log(max(float(true_price), 1e-8)))
        true_ret = float(true_log - history_log[-1])
        history_log.append(true_log)
        history_ret_norm.append((true_ret - ret_mean) / ret_std)

    preds = np.asarray(preds, dtype=float)
    return metrics(test_arr, preds), pd.DataFrame({"y_true": test_arr, "y_pred": preds})


def fit_catboost_inference_model(
    train: pd.Series,
    context_len: int = 120,
    depth: int = 8,
    learning_rate: float = 0.05,
    iterations: int = 400,
    l2_leaf_reg: float = 3.0,
    use_cuda: bool = True,
) -> Dict[str, Any]:
    prep = _prepare_catboost_training(train=train, context_len=context_len)
    model = _make_catboost_regressor(
        depth=depth,
        learning_rate=learning_rate,
        iterations=iterations,
        l2_leaf_reg=l2_leaf_reg,
        use_cuda=use_cuda,
    )
    model.fit(prep["x_np"], prep["y_np"])

    return {
        "model_type": "catboost",
        "context": int(prep["context"]),
        "ret_mean": float(prep["ret_mean"]),
        "ret_std": float(prep["ret_std"]),
        "train_last": float(prep["train_arr"][-1]),
        "model": model,
        "use_cuda": bool(use_cuda) and torch.cuda.is_available(),
    }


def predict_catboost_inference(model_obj: Dict[str, Any], train: pd.Series, test: pd.Series) -> Tuple[Dict[str, float], pd.DataFrame]:
    train_arr = _to_float_array(train)
    test_arr = _to_float_array(test)

    context = int(model_obj["context"])
    ret_mean = float(model_obj["ret_mean"])
    ret_std = float(model_obj["ret_std"])
    model = model_obj["model"]

    history_log = list(np.log(np.clip(train_arr, 1e-8, None)).astype(float))
    history_ret_norm = list(((np.diff(np.log(np.clip(train_arr, 1e-8, None))) - ret_mean) / ret_std).astype(float))

    preds = []
    for true_price in test_arr:
        if len(history_ret_norm) >= context:
            x_ctx = np.asarray(history_ret_norm[-context:], dtype=np.float32)
        else:
            first_val = history_ret_norm[0] if len(history_ret_norm) > 0 else 0.0
            pad = np.full((context - len(history_ret_norm),), first_val, dtype=np.float32)
            x_ctx = np.concatenate([pad, np.asarray(history_ret_norm, dtype=np.float32)])

        pred_ret_norm = float(model.predict(x_ctx.reshape(1, -1))[0])
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


def fit_catboost_multi_horizon_inference_model(
    train: pd.Series,
    horizons: tuple[int, ...] = (1, 5, 10, 15, 30, 60),
    context_len: int = 120,
    depth: int = 8,
    learning_rate: float = 0.05,
    iterations: int = 400,
    l2_leaf_reg: float = 3.0,
    use_cuda: bool = True,
) -> Dict[str, Any]:
    """Trains direct multi-horizon CatBoost models on a shared return context."""

    prep = _prepare_catboost_training(train=train, context_len=context_len)
    train_arr = np.asarray(prep["train_arr"], dtype=float)
    train_log = np.log(np.clip(train_arr, 1e-8, None))
    ret_mean = float(prep["ret_mean"])
    ret_std = float(prep["ret_std"])
    safe_ret_std = ret_std if abs(ret_std) > 1e-12 else 1.0
    context = int(prep["context"])

    horizons_clean = sorted({int(h) for h in horizons if int(h) > 0})
    if len(horizons_clean) == 0:
        raise ValueError("horizons must contain at least one positive integer")

    ret_norm = ((np.diff(train_log) - ret_mean) / safe_ret_std).astype(np.float32)

    models_by_horizon: Dict[int, Any] = {}
    target_mean_by_horizon: Dict[int, float] = {}
    target_std_by_horizon: Dict[int, float] = {}

    for horizon in horizons_clean:
        x_rows = []
        y_rows = []
        max_t = len(train_arr) - int(horizon)
        for t in range(context, max_t):
            x_rows.append(ret_norm[t - context:t])
            y_rows.append(float(train_log[t + horizon] - train_log[t]))

        if len(x_rows) < 16:
            continue

        x_np = np.asarray(x_rows, dtype=np.float32)
        y_np = np.asarray(y_rows, dtype=np.float32)
        y_mean = float(np.mean(y_np))
        y_std = float(np.std(y_np) + 1e-8)
        y_norm = (y_np - y_mean) / y_std

        model = _make_catboost_regressor(
            depth=depth,
            learning_rate=learning_rate,
            iterations=iterations,
            l2_leaf_reg=l2_leaf_reg,
            use_cuda=use_cuda,
        )
        model.fit(x_np, y_norm)

        models_by_horizon[int(horizon)] = model
        target_mean_by_horizon[int(horizon)] = y_mean
        target_std_by_horizon[int(horizon)] = y_std

    if len(models_by_horizon) == 0:
        raise RuntimeError("Failed to train multi-horizon models: insufficient samples for requested horizons")

    trained_horizons = sorted(models_by_horizon.keys())
    return {
        "model_type": "catboost_multi_horizon",
        "context": context,
        "ret_mean": ret_mean,
        "ret_std": safe_ret_std,
        "train_last": float(train_arr[-1]),
        "horizons": trained_horizons,
        "models_by_horizon": models_by_horizon,
        "target_mean_by_horizon": target_mean_by_horizon,
        "target_std_by_horizon": target_std_by_horizon,
        "use_cuda": bool(use_cuda) and torch.cuda.is_available(),
    }


def predict_catboost_multi_horizon_path(
    model_obj: Dict[str, Any],
    history_series: pd.Series,
    horizon: int,
) -> pd.DataFrame:
    """Builds a minute-level path from direct multi-horizon predictions."""

    history_arr = pd.to_numeric(history_series, errors="coerce").dropna().astype(float).to_numpy()
    if len(history_arr) < 10:
        raise RuntimeError("Insufficient history for multi-horizon forecast")

    horizon = int(max(1, horizon))
    context = int(model_obj["context"])
    ret_mean = float(model_obj["ret_mean"])
    ret_std = float(model_obj["ret_std"])
    safe_ret_std = ret_std if abs(ret_std) > 1e-12 else 1.0

    history_log = np.log(np.clip(history_arr, 1e-8, None)).astype(float)
    history_ret_norm = ((np.diff(history_log) - ret_mean) / safe_ret_std).astype(np.float32)

    if len(history_ret_norm) >= context:
        x_ctx = np.asarray(history_ret_norm[-context:], dtype=np.float32)
    else:
        first_val = float(history_ret_norm[0]) if len(history_ret_norm) > 0 else 0.0
        pad = np.full((context - len(history_ret_norm),), first_val, dtype=np.float32)
        x_ctx = np.concatenate([pad, np.asarray(history_ret_norm, dtype=np.float32)])

    models_by_horizon = dict(model_obj["models_by_horizon"])
    target_mean_by_horizon = dict(model_obj["target_mean_by_horizon"])
    target_std_by_horizon = dict(model_obj["target_std_by_horizon"])

    anchor_prices: Dict[int, float] = {0: float(history_arr[-1])}
    for h in sorted(int(v) for v in models_by_horizon.keys() if int(v) > 0 and int(v) <= horizon):
        model = models_by_horizon[h]
        pred_norm = float(model.predict(x_ctx.reshape(1, -1))[0])
        pred_cum_ret = pred_norm * float(target_std_by_horizon[h]) + float(target_mean_by_horizon[h])
        clip_bound = float(np.clip(8.0 * safe_ret_std * np.sqrt(max(1.0, float(h))), 0.01, 0.40))
        pred_cum_ret = float(np.clip(pred_cum_ret, -clip_bound, clip_bound))
        pred_log = float(history_log[-1] + pred_cum_ret)
        anchor_prices[h] = float(np.exp(pred_log))

    if len(anchor_prices) <= 1:
        raise RuntimeError("No multi-horizon anchors available for prediction")

    anchor_steps = sorted(anchor_prices.keys())
    if anchor_steps[-1] < horizon:
        anchor_prices[horizon] = float(anchor_prices[anchor_steps[-1]])
        anchor_steps = sorted(anchor_prices.keys())

    rows = []
    for step in range(1, horizon + 1):
        if step in anchor_prices:
            pred_price = float(anchor_prices[step])
        else:
            left_candidates = [s for s in anchor_steps if s < step]
            right_candidates = [s for s in anchor_steps if s > step]
            if len(left_candidates) == 0 or len(right_candidates) == 0:
                pred_price = float(anchor_prices[anchor_steps[-1]])
            else:
                left = left_candidates[-1]
                right = right_candidates[0]
                left_log = float(np.log(max(anchor_prices[left], 1e-8)))
                right_log = float(np.log(max(anchor_prices[right], 1e-8)))
                alpha = float((step - left) / max(1, right - left))
                interp_log = float(left_log + alpha * (right_log - left_log))
                pred_price = float(np.exp(interp_log))
        rows.append({"step_minute": int(step), "pred_price": pred_price})

    return pd.DataFrame(rows)


class _CatBoostGridSearchEstimator(BaseEstimator):
    def __init__(
        self,
        context_len: int = 120,
        depth: int = 8,
        learning_rate: float = 0.05,
        iterations: int = 400,
        l2_leaf_reg: float = 3.0,
        use_cuda: bool = True,
        score_metric: str = "MAE",
        time_weight: float = 0.10,
        resource_points: int = 160,
    ):
        self.context_len = context_len
        self.depth = depth
        self.learning_rate = learning_rate
        self.iterations = iterations
        self.l2_leaf_reg = l2_leaf_reg
        self.use_cuda = use_cuda
        self.score_metric = score_metric
        self.time_weight = time_weight
        self.resource_points = resource_points

    def fit(self, X, y):
        self._train_series_ = pd.Series(np.asarray(y, dtype=float))
        return self

    def score(self, X, y):
        _progress_step()
        if not hasattr(self, "_train_series_"):
            raise RuntimeError("Estimator is not fitted")

        valid = {"MAE", "RMSE", "MAPE", "MAE_TIME"}
        if self.score_metric not in valid:
            raise ValueError(f"score_metric должен быть одним из {sorted(valid)}")

        train_series = _slice_train_for_resource(self._train_series_, self.resource_points, min_points=80)
        test_series = pd.Series(np.asarray(y, dtype=float))
        t0 = time.perf_counter()
        metric_values, _ = run_catboost(
            train=train_series,
            test=test_series,
            context_len=int(self.context_len),
            depth=int(self.depth),
            learning_rate=float(self.learning_rate),
            iterations=int(self.iterations),
            l2_leaf_reg=float(self.l2_leaf_reg),
            use_cuda=bool(self.use_cuda),
            show_progress=False,
        )
        elapsed_sec = float(time.perf_counter() - t0)
        if self.score_metric == "MAE_TIME":
            mae = float(metric_values["MAE"])
            elapsed_per_point = elapsed_sec / max(1, len(test_series))
            composite = mae * (1.0 + float(self.time_weight) * np.log1p(max(0.0, elapsed_per_point)))
            return -float(composite)
        return -float(metric_values[self.score_metric])


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


def run_catboost_gridsearchcv_native_pipeline(
    full_series: pd.Series,
    param_grid: dict | list[dict],
    test_ratio: float = 0.2,
    n_splits: int = 4,
    scoring: str = "MAE",
    use_cuda: bool = True,
    n_jobs: int = 1,
    min_resource_points: int | None = None,
    max_resource_points: int | None = None,
    halving_factor: int = 3,
    aggressive_elimination: bool = True,
    time_weight: float = 0.10,
):
    if not _SKLEARN_AVAILABLE:
        raise RuntimeError("Для native HalvingGridSearchCV нужен scikit-learn (pip install scikit-learn)")

    valid_scoring = {"MAE", "RMSE", "MAPE", "MAE_TIME"}
    if scoring not in valid_scoring:
        raise ValueError(f"scoring должен быть одним из {sorted(valid_scoring)}")

    values = pd.to_numeric(full_series, errors="coerce").dropna().astype(float).values
    if len(values) < 160:
        raise RuntimeError("Слишком мало данных для CatBoost GridSearchCV pipeline")

    split_idx = int(len(values) * (1.0 - float(test_ratio)))
    split_idx = max(80, min(split_idx, len(values) - 24))
    if split_idx <= 0 or split_idx >= len(values):
        raise RuntimeError("Некорректное разбиение train/test для CatBoost GridSearchCV pipeline")

    train = pd.Series(values[:split_idx]).reset_index(drop=True)
    test = pd.Series(values[split_idx:]).reset_index(drop=True)

    jobs = max(1, int(n_jobs))
    if bool(use_cuda) and jobs > 1:
        print("CatBoost GridSearchCV: use_cuda=True, n_jobs принудительно установлен в 1 (безопасный режим для GPU).")
        jobs = 1

    x_train = np.arange(len(train), dtype=np.float32)[:, None]
    y_train = train.values.astype(float)

    ts_splits = max(2, min(int(n_splits), max(2, len(train) // 120)))
    cv = TimeSeriesSplit(n_splits=ts_splits)

    default_min_resources = 160
    requested_min_resources = default_min_resources if min_resource_points is None else int(min_resource_points)
    min_resources = max(80, requested_min_resources)

    default_max_resources = len(train)
    requested_max_resources = default_max_resources if max_resource_points is None else int(max_resource_points)
    max_resources = max(min_resources, min(int(len(train)), requested_max_resources))

    factor = max(2, int(halving_factor))
    estimator = _CatBoostGridSearchEstimator(use_cuda=bool(use_cuda), score_metric=scoring, time_weight=float(time_weight))

    grid = _make_halving_search(
        estimator=estimator,
        param_grid=param_grid,
        cv=cv,
        n_jobs=jobs,
        min_resources=min_resources,
        max_resources=max_resources,
        factor=factor,
        aggressive_elimination=aggressive_elimination,
    )

    print(
        "CatBoost HalvingGridSearchCV: "
        f"train_points={len(train)} | test_points={len(test)} | n_splits={ts_splits} | "
        f"min_resources={min_resources} | max_resources={max_resources} | factor={factor} | "
        f"aggressive_elimination={bool(aggressive_elimination)} | scoring={scoring}"
    )
    _fit_grid_with_progress(grid, x_train, y_train, param_grid, ts_splits, "CATBOOST")

    cv_results_df = pd.DataFrame(grid.cv_results_)
    cv_results_df["score"] = -cv_results_df["mean_test_score"]
    cv_results_df["score_std"] = cv_results_df["std_test_score"]
    cv_results_df = cv_results_df.sort_values(["score", "score_std"], na_position="last").reset_index(drop=True)

    best_params = dict(grid.best_params_)
    best_resource_points = int(best_params.get("resource_points", len(train)))
    final_context_len = int(best_params["context_len"])
    final_min_points = max(80, final_context_len + 8)
    final_train = _slice_train_for_resource(train, best_resource_points, min_points=final_min_points)
    public_best_params = _public_search_params(best_params)

    print(f"CatBoost HalvingGridSearchCV: best {scoring}={float(-grid.best_score_):.6f} | {public_best_params}")
    print(
        "CatBoost final fit: "
        f"train_points={len(final_train)} (best halving resource window) | test_points={len(test)}"
    )

    model_metrics, pred_df = run_catboost(
        final_train,
        test,
        context_len=final_context_len,
        depth=int(best_params["depth"]),
        learning_rate=float(best_params["learning_rate"]),
        iterations=int(best_params["iterations"]),
        l2_leaf_reg=float(best_params.get("l2_leaf_reg", 3.0)),
        use_cuda=bool(best_params.get("use_cuda", use_cuda)),
        show_progress=True,
    )

    split_info = {
        "full_points": int(len(values)),
        "train_points": int(len(train)),
        "test_points": int(len(test)),
        "test_ratio": float(test_ratio),
        "cv_n_splits": int(ts_splits),
        "min_resource_points": int(min_resources),
        "max_resource_points": int(max_resources),
        "best_resource_points": int(best_resource_points),
        "halving_factor": int(factor),
        "aggressive_elimination": bool(aggressive_elimination),
        "time_weight": float(time_weight),
    }
    return public_best_params, cv_results_df, model_metrics, pred_df, split_info
