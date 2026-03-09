from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Dict, Tuple

import numpy as np
import pandas as pd
import torch

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


def _estimate_halving_total_fits(param_grid: dict, ts_splits: int, min_resources: int, max_resources: int, factor: int) -> int:
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


def _fit_grid_with_progress(grid, x_train, y_train, param_grid: dict, ts_splits: int, label: str):
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
    param_grid: dict,
    cv,
    n_jobs: int,
    min_resources: int,
    max_resources: int,
    factor: int,
    aggressive_elimination: bool,
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


def _prepare_lstm_training(train: pd.Series, context_len: int, use_cuda: bool) -> Dict[str, Any]:
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

    return {
        "train_arr": train_arr,
        "train_log": train_log,
        "train_ret_norm": train_ret_norm,
        "ret_mean": ret_mean,
        "ret_std": ret_std,
        "context": int(context),
        "x_t": x_t,
        "y_t": y_t,
        "cuda_exist": bool(cuda_exist),
        "device": device,
    }


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
        resource_points: int = 160,
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
        self.resource_points = resource_points

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

        train_series = _slice_train_for_resource(self._train_series_, self.resource_points, min_points=80)
        test_series = pd.Series(np.asarray(y, dtype=float))
        metric_values, _ = run_lstm(
            train=train_series,
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
        return -float(metric_values[self.score_metric])


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
) -> Tuple[Dict[str, float], pd.DataFrame]:
    train_arr = pd.to_numeric(train, errors="coerce").dropna().astype(float).values
    test_arr = pd.to_numeric(test, errors="coerce").dropna().astype(float).values

    if len(train_arr) < 80 or len(test_arr) < 5:
        raise RuntimeError("Слишком мало данных для LSTM")

    prep = _prepare_lstm_training(train=train, context_len=context_len, use_cuda=use_cuda)
    train_log = prep["train_log"]
    train_ret_norm = prep["train_ret_norm"]
    ret_mean = float(prep["ret_mean"])
    ret_std = float(prep["ret_std"])
    context = int(prep["context"])
    x_t = prep["x_t"]
    y_t = prep["y_t"]
    cuda_exist = bool(prep["cuda_exist"])
    device = prep["device"]

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
    prep = _prepare_lstm_training(train=train, context_len=context_len, use_cuda=use_cuda)
    train_arr = prep["train_arr"]
    ret_mean = float(prep["ret_mean"])
    ret_std = float(prep["ret_std"])
    context = int(prep["context"])
    x_t = prep["x_t"]
    y_t = prep["y_t"]
    cuda_exist = bool(prep["cuda_exist"])
    device = prep["device"]

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


def run_lstm_gridsearchcv_native_pipeline(
    full_series: pd.Series,
    param_grid: dict,
    test_ratio: float = 0.2,
    n_splits: int = 5,
    scoring: str = "MAE",
    use_cuda: bool = True,
    n_jobs: int = 1,
    min_resource_points: int | None = None,
    max_resource_points: int | None = None,
    halving_factor: int = 4,
    aggressive_elimination: bool = True,
):
    if not _SKLEARN_AVAILABLE:
        raise RuntimeError("Для native HalvingGridSearchCV нужен scikit-learn (pip install scikit-learn)")

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

    x_train = np.arange(len(train), dtype=np.float32)[:, None]
    y_train = train.values.astype(float)

    ts_splits = max(2, min(int(n_splits), max(2, len(train) // 120)))
    cv = TimeSeriesSplit(n_splits=ts_splits)

    default_min_resources = 256
    requested_min_resources = default_min_resources if min_resource_points is None else int(min_resource_points)
    min_resources = max(80, requested_min_resources)

    default_max_resources = min(len(train), max(min_resources, 12_000))
    requested_max_resources = default_max_resources if max_resource_points is None else int(max_resource_points)
    max_resources = max(min_resources, min(int(len(train)), requested_max_resources))

    factor = max(2, int(halving_factor))

    estimator = _LSTMGridSearchEstimator(use_cuda=bool(use_cuda), score_metric=scoring)
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
        "LSTM HalvingGridSearchCV: "
        f"train_points={len(train)} | test_points={len(test)} | n_splits={ts_splits} | "
        f"min_resources={min_resources} | max_resources={max_resources} | factor={factor} | "
        f"aggressive_elimination={bool(aggressive_elimination)}"
    )
    _fit_grid_with_progress(grid, x_train, y_train, param_grid, ts_splits, "LSTM")

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

    print(f"LSTM HalvingGridSearchCV: best {scoring}={float(-grid.best_score_):.6f} | {public_best_params}")
    print(
        "LSTM final fit: "
        f"train_points={len(final_train)} (best halving resource window) | test_points={len(test)}"
    )

    model_metrics, pred_df = run_lstm(
        final_train,
        test,
        context_len=final_context_len,
        hidden_size=int(best_params["hidden_size"]),
        num_layers=int(best_params["num_layers"]),
        dropout=float(best_params["dropout"]),
        epochs=int(best_params["epochs"]),
        batch_size=int(best_params["batch_size"]),
        lr=float(best_params["lr"]),
        weight_decay=float(best_params.get("weight_decay", 1e-4)),
        use_cuda=bool(use_cuda),
        show_progress=False,
    )

    split_info = {
        "full_points": int(len(values)),
        "train_points": int(len(train)),
        "test_points": int(len(test)),
        "test_ratio": float(test_ratio),
        "cv_n_splits": int(ts_splits),
        "min_resources": int(min_resources),
        "max_resources": int(max_resources),
        "halving_factor": int(factor),
        "aggressive_elimination": bool(aggressive_elimination),
    }
    return public_best_params, cv_results_df, model_metrics, pred_df, split_info
