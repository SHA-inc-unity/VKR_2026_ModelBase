from __future__ import annotations

import random
import time
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

import numpy as np
import pandas as pd
import requests
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, Dataset

try:
    from sklearn.base import BaseEstimator
    from sklearn.model_selection import GridSearchCV, TimeSeriesSplit

    _SKLEARN_AVAILABLE = True
except Exception:
    BaseEstimator = object
    GridSearchCV = None
    TimeSeriesSplit = None
    _SKLEARN_AVAILABLE = False


class _AstSeriesDataset(Dataset):
    def __init__(self, series_scaled: np.ndarray, starts: np.ndarray, context_len: int, pred_len: int):
        self.series = series_scaled.astype(np.float32)
        self.starts = starts.astype(np.int64)
        self.context_len = context_len
        self.pred_len = pred_len

    def __len__(self):
        return len(self.starts)

    def __getitem__(self, idx):
        s = int(self.starts[idx])
        x = self.series[s:s + self.context_len].astype(np.float32)
        y = self.series[s + self.context_len:s + self.context_len + self.pred_len].astype(np.float32)

        scale = float(np.mean(np.abs(x)) + 1.0)
        x = x / scale
        y = y / scale
        return torch.from_numpy(x[:, None]), torch.from_numpy(y), torch.tensor(scale, dtype=torch.float32)


class _SparseAttention(nn.Module):
    def __init__(self, d_model: int, n_heads: int, topk: int = 32, dropout: float = 0.1):
        super().__init__()
        assert d_model % n_heads == 0
        self.d_model = d_model
        self.n_heads = n_heads
        self.d_head = d_model // n_heads
        self.topk = topk

        self.q = nn.Linear(d_model, d_model)
        self.k = nn.Linear(d_model, d_model)
        self.v = nn.Linear(d_model, d_model)
        self.o = nn.Linear(d_model, d_model)
        self.drop = nn.Dropout(dropout)

    def forward(self, x):
        b, t, _ = x.shape
        q = self.q(x).view(b, t, self.n_heads, self.d_head).transpose(1, 2)
        k = self.k(x).view(b, t, self.n_heads, self.d_head).transpose(1, 2)
        v = self.v(x).view(b, t, self.n_heads, self.d_head).transpose(1, 2)

        scores = torch.matmul(q, k.transpose(-2, -1)) / (self.d_head ** 0.5)
        if self.topk > 0 and self.topk < t:
            topv, topi = torch.topk(scores, k=self.topk, dim=-1)
            sparse_scores = torch.full_like(scores, float("-inf"))
            sparse_scores.scatter_(-1, topi, topv)
            scores = sparse_scores

        attn = torch.softmax(scores, dim=-1)
        attn = self.drop(attn)
        out = torch.matmul(attn, v)
        out = out.transpose(1, 2).contiguous().view(b, t, self.d_model)
        return self.o(out)


class _AstBlock(nn.Module):
    def __init__(self, d_model=128, n_heads=8, ff_dim=256, topk=32, dropout=0.1):
        super().__init__()
        self.attn = _SparseAttention(d_model, n_heads, topk=topk, dropout=dropout)
        self.ff = nn.Sequential(
            nn.Linear(d_model, ff_dim),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(ff_dim, d_model),
        )
        self.n1 = nn.LayerNorm(d_model)
        self.n2 = nn.LayerNorm(d_model)
        self.drop = nn.Dropout(dropout)

    def forward(self, x):
        x = self.n1(x + self.drop(self.attn(x)))
        x = self.n2(x + self.drop(self.ff(x)))
        return x


class _AstGenerator(nn.Module):
    def __init__(self, context_len=96, pred_len=1, d_model=128, n_heads=8, n_layers=4, ff_dim=256, dropout=0.12, topk=32):
        super().__init__()
        self.inp = nn.Linear(1, d_model)
        self.local_conv = nn.Conv1d(d_model, d_model, kernel_size=3, padding=1, groups=1)
        self.pos = nn.Parameter(torch.zeros(1, context_len, d_model))
        self.blocks = nn.ModuleList([_AstBlock(d_model, n_heads, ff_dim, topk=topk, dropout=dropout) for _ in range(n_layers)])
        self.out_norm = nn.LayerNorm(d_model)
        self.q10 = nn.Linear(d_model, pred_len)
        self.q50 = nn.Linear(d_model, pred_len)
        self.q90 = nn.Linear(d_model, pred_len)

    def forward(self, x):
        h = self.inp(x) + self.pos[:, :x.size(1), :]
        h_conv = self.local_conv(h.transpose(1, 2)).transpose(1, 2)
        h = h + h_conv
        for block in self.blocks:
            h = block(h)
        z = self.out_norm(h[:, -1, :])
        q10 = self.q10(z)
        q50 = self.q50(z)
        q90 = self.q90(z)
        return torch.stack([q10, q50, q90], dim=-1)


class _AstDiscriminator(nn.Module):
    def __init__(self, total_len: int, hidden=256):
        super().__init__()
        self.net = nn.Sequential(
            nn.utils.spectral_norm(nn.Linear(total_len, hidden)),
            nn.LeakyReLU(0.2),
            nn.Dropout(0.15),
            nn.utils.spectral_norm(nn.Linear(hidden, hidden // 2)),
            nn.LeakyReLU(0.2),
            nn.Dropout(0.1),
            nn.Linear(hidden // 2, 1),
        )

    def forward(self, seq):
        return self.net(seq)


def _pinball(pred, target, q):
    err = target - pred
    return torch.maximum(q * err, (q - 1.0) * err).mean()


def _q_loss(pred_q, y):
    q10 = pred_q[:, :, 0]
    q50 = pred_q[:, :, 1]
    q90 = pred_q[:, :, 2]
    l10 = _pinball(q10, y, 0.10)
    l50 = _pinball(q50, y, 0.50)
    l90 = _pinball(q90, y, 0.90)
    monotonic_penalty = torch.relu(q10 - q50).mean() + torch.relu(q50 - q90).mean()
    return 0.2 * l10 + 0.6 * l50 + 0.2 * l90 + 0.5 * monotonic_penalty


def run_ast_astmain_style(
    full_series: pd.Series,
    test_ratio=0.2,
    context_len=168,
    pred_len=1,
    epochs=35,
    batch_size=64,
    lr=6e-4,
    min_lr=1e-5,
    lambda_adv=0.008,
    d_steps=2,
    d_model=128,
    n_heads=8,
    n_layers=4,
    ff_dim=256,
    dropout=0.12,
    topk=48,
    seed=42,
    use_cuda=True,
    use_amp=True,
    use_gan="auto",
):
    torch.manual_seed(seed)
    np.random.seed(seed)

    values = pd.to_numeric(full_series, errors="coerce").astype(float).values
    values = values[np.isfinite(values)]
    values = np.clip(values, 1e-8, None)

    mean = float(values.mean())
    std = float(values.std() + 1e-8)
    scaled = (values - mean) / std

    n = len(scaled)
    max_start = n - context_len - pred_len
    starts = np.arange(0, max_start + 1, dtype=np.int64)

    test_size = max(10, int(n * test_ratio))
    split_point = n - test_size
    end_idx = starts + context_len + pred_len
    train_starts = starts[end_idx <= split_point]
    test_starts = starts[end_idx > split_point]

    if len(train_starts) < 24 or len(test_starts) < 8:
        raise RuntimeError("Слишком мало окон для AST")

    small_data = len(train_starts) < 400
    if small_data:
        d_model = min(d_model, 96)
        n_heads = 4 if d_model % 4 == 0 else n_heads
        n_layers = min(n_layers, 2)
        ff_dim = min(ff_dim, 192)
        topk = min(topk, max(8, context_len // 3))

    if use_gan == "auto":
        gan_enabled = len(train_starts) >= 800
    else:
        gan_enabled = bool(use_gan)

    train_ds = _AstSeriesDataset(scaled, train_starts, context_len, pred_len)
    test_ds = _AstSeriesDataset(scaled, test_starts, context_len, pred_len)

    cuda_exist = torch.cuda.is_available() and bool(use_cuda)
    if cuda_exist:
        torch.backends.cudnn.benchmark = True

    amp_enabled = cuda_exist and bool(use_amp)
    device = torch.device("cuda" if cuda_exist else "cpu")
    amp_device_type = "cuda" if cuda_exist else "cpu"

    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True, drop_last=True, pin_memory=cuda_exist)
    test_loader = DataLoader(test_ds, batch_size=batch_size, shuffle=False, pin_memory=cuda_exist)

    gen = _AstGenerator(
        context_len=context_len,
        pred_len=pred_len,
        d_model=d_model,
        n_heads=n_heads,
        n_layers=n_layers,
        ff_dim=ff_dim,
        dropout=dropout,
        topk=max(8, min(topk, context_len)),
    ).to(device)
    disc = _AstDiscriminator(total_len=context_len + pred_len, hidden=max(128, d_model * 2)).to(device)

    g_opt = torch.optim.AdamW(gen.parameters(), lr=lr, weight_decay=1e-4)
    d_opt = torch.optim.AdamW(disc.parameters(), lr=lr, weight_decay=1e-4)
    g_sch = torch.optim.lr_scheduler.CosineAnnealingLR(g_opt, T_max=epochs, eta_min=min_lr)
    d_sch = torch.optim.lr_scheduler.CosineAnnealingLR(d_opt, T_max=epochs, eta_min=min_lr)

    bce = nn.BCEWithLogitsLoss()
    point = nn.SmoothL1Loss()

    scaler_g = torch.amp.GradScaler(device="cuda", enabled=amp_enabled)
    scaler_d = torch.amp.GradScaler(device="cuda", enabled=amp_enabled)

    warmup_epochs = max(2, epochs // 4)
    for epoch in range(1, epochs + 1):
        gen.train()
        disc.train()
        adv_w = 0.0 if (epoch <= warmup_epochs or not gan_enabled) else lambda_adv

        for x, y, _scale in train_loader:
            x = x.to(device, non_blocking=cuda_exist)
            y = y.to(device, non_blocking=cuda_exist)
            bs = x.size(0)

            if gan_enabled:
                for _ in range(max(1, d_steps)):
                    with torch.no_grad():
                        with torch.amp.autocast(device_type=amp_device_type, enabled=amp_enabled):
                            fake_q = torch.clamp(gen(x), -8.0, 8.0)
                            fake_med = fake_q[:, :, 1]

                    real_seq = torch.cat([x.squeeze(-1), y], dim=1)
                    fake_seq = torch.cat([x.squeeze(-1), fake_med], dim=1)
                    real_t = torch.full((bs, 1), 0.90, device=device)
                    fake_t = torch.full((bs, 1), 0.10, device=device)

                    d_opt.zero_grad(set_to_none=True)
                    with torch.amp.autocast(device_type=amp_device_type, enabled=amp_enabled):
                        d_loss = 0.5 * (bce(disc(real_seq), real_t) + bce(disc(fake_seq), fake_t))

                    if amp_enabled:
                        scaler_d.scale(d_loss).backward()
                        scaler_d.unscale_(d_opt)
                        torch.nn.utils.clip_grad_norm_(disc.parameters(), 1.0)
                        scaler_d.step(d_opt)
                        scaler_d.update()
                    else:
                        d_loss.backward()
                        torch.nn.utils.clip_grad_norm_(disc.parameters(), 1.0)
                        d_opt.step()

            g_opt.zero_grad(set_to_none=True)
            with torch.amp.autocast(device_type=amp_device_type, enabled=amp_enabled):
                pred_q = torch.clamp(gen(x), -8.0, 8.0)
                pred_med = pred_q[:, :, 1]
                g_adv = bce(disc(torch.cat([x.squeeze(-1), pred_med], dim=1)), torch.full((bs, 1), 0.90, device=device)) if gan_enabled else torch.tensor(0.0, device=device)
                g_loss = _q_loss(pred_q, y) + 0.6 * point(pred_med, y) + adv_w * g_adv

            if torch.isfinite(g_loss.detach()):
                if amp_enabled:
                    scaler_g.scale(g_loss).backward()
                    scaler_g.unscale_(g_opt)
                    torch.nn.utils.clip_grad_norm_(gen.parameters(), 1.0)
                    scaler_g.step(g_opt)
                    scaler_g.update()
                else:
                    g_loss.backward()
                    torch.nn.utils.clip_grad_norm_(gen.parameters(), 1.0)
                    g_opt.step()

        g_sch.step()
        if gan_enabled:
            d_sch.step()

    gen.eval()
    y_true_chunks, y_pred_chunks = [], []
    with torch.no_grad():
        for x, y, scale in test_loader:
            x = x.to(device, non_blocking=cuda_exist)
            with torch.amp.autocast(device_type=amp_device_type, enabled=amp_enabled):
                q = torch.clamp(gen(x), -8.0, 8.0)
            pred_first = q[:, 0, 1].cpu().numpy()
            true_first = y[:, 0].cpu().numpy()
            scale_np = scale.cpu().numpy().astype(float)
            y_pred_chunks.append(pred_first * scale_np)
            y_true_chunks.append(true_first * scale_np)

    y_pred_scaled = np.concatenate(y_pred_chunks).astype(float)
    y_true_scaled = np.concatenate(y_true_chunks).astype(float)

    y_pred = y_pred_scaled * std + mean
    y_true = y_true_scaled * std + mean
    y_pred = np.nan_to_num(y_pred, nan=mean, posinf=mean, neginf=mean)
    y_true = np.nan_to_num(y_true, nan=mean, posinf=mean, neginf=mean)

    eps = 1e-8
    mae = float(np.mean(np.abs(y_true - y_pred)))
    rmse = float(np.sqrt(np.mean((y_true - y_pred) ** 2)))
    mape = float(np.mean(np.abs((y_true - y_pred) / np.maximum(np.abs(y_true), eps))) * 100.0)

    return {"MAE": mae, "RMSE": rmse, "MAPE": mape}, pd.DataFrame({"y_true": y_true, "y_pred": y_pred})


class _ASTGridSearchEstimator(BaseEstimator):
    def __init__(
        self,
        context_len: int = 168,
        pred_len: int = 1,
        epochs: int = 20,
        batch_size: int = 64,
        lr: float = 6e-4,
        min_lr: float = 1e-5,
        lambda_adv: float = 0.008,
        d_steps: int = 2,
        d_model: int = 128,
        n_heads: int = 8,
        n_layers: int = 4,
        ff_dim: int = 256,
        dropout: float = 0.12,
        topk: int = 48,
        use_cuda: bool = True,
        use_amp: bool = True,
        use_gan: str = "auto",
        score_metric: str = "MAE",
    ):
        self.context_len = context_len
        self.pred_len = pred_len
        self.epochs = epochs
        self.batch_size = batch_size
        self.lr = lr
        self.min_lr = min_lr
        self.lambda_adv = lambda_adv
        self.d_steps = d_steps
        self.d_model = d_model
        self.n_heads = n_heads
        self.n_layers = n_layers
        self.ff_dim = ff_dim
        self.dropout = dropout
        self.topk = topk
        self.use_cuda = use_cuda
        self.use_amp = use_amp
        self.use_gan = use_gan
        self.score_metric = score_metric

    def fit(self, X, y):
        self._train_series_ = pd.Series(np.asarray(y, dtype=float))
        return self

    def score(self, X, y):
        if not hasattr(self, "_train_series_"):
            raise RuntimeError("Estimator is not fitted")

        valid_series = pd.Series(np.asarray(y, dtype=float))
        full_series = pd.concat([self._train_series_, valid_series], ignore_index=True)
        test_ratio = len(valid_series) / max(1, len(full_series))
        try:
            m, _ = run_ast_astmain_style(
                full_series=full_series,
                test_ratio=float(test_ratio),
                context_len=int(self.context_len),
                pred_len=int(self.pred_len),
                epochs=int(self.epochs),
                batch_size=int(self.batch_size),
                lr=float(self.lr),
                min_lr=float(self.min_lr),
                lambda_adv=float(self.lambda_adv),
                d_steps=int(self.d_steps),
                d_model=int(self.d_model),
                n_heads=int(self.n_heads),
                n_layers=int(self.n_layers),
                ff_dim=int(self.ff_dim),
                dropout=float(self.dropout),
                topk=int(self.topk),
                use_cuda=bool(self.use_cuda),
                use_amp=bool(self.use_amp),
                use_gan=self.use_gan,
            )
            return -float(m[self.score_metric])
        except Exception:
            # Mark infeasible folds/params as NaN to keep GridSearchCV running safely.
            return float("nan")


def run_ast_gridsearchcv_native_pipeline(
    full_series: pd.Series,
    param_grid: dict,
    test_ratio: float = 0.2,
    n_splits: int = 3,
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
    min_context = int(min(param_grid.get("context_len", [168])))
    min_pred_len = int(min(param_grid.get("pred_len", [1])))

    # Minimal safe series size for AST windowing + holdout split.
    min_total_points = max(96, min_context + min_pred_len + 40)
    if len(values) < min_total_points:
        raise RuntimeError(
            f"Слишком мало данных для AST GridSearchCV pipeline: "
            f"есть={len(values)}, нужно>={min_total_points}"
        )

    split_idx = int(len(values) * (1.0 - float(test_ratio)))
    min_train_points = max(64, min_context + min_pred_len + 24)
    min_test_points = max(10, min_pred_len + 8)
    split_idx = max(min_train_points, min(split_idx, len(values) - min_test_points))
    if split_idx <= 0 or split_idx >= len(values):
        raise RuntimeError("Некорректное разбиение train/test для AST GridSearchCV pipeline")

    train = pd.Series(values[:split_idx]).reset_index(drop=True)
    test = pd.Series(values[split_idx:]).reset_index(drop=True)

    jobs = max(1, int(n_jobs))
    if bool(use_cuda) and jobs > 1:
        print("AST GridSearchCV: use_cuda=True, n_jobs принудительно установлен в 1 (безопасный режим для GPU).")
        jobs = 1

    x_train = np.arange(len(train), dtype=np.float32)[:, None]
    y_train = train.values.astype(float)

    requested_splits = max(2, int(n_splits))
    min_fold_train_points = max(48, min_context + min_pred_len + 24)
    # Approximate first train-fold size in TimeSeriesSplit: n_train // (n_splits + 1)
    max_splits_by_context = (len(train) // max(1, min_fold_train_points)) - 1
    if max_splits_by_context < 2:
        raise RuntimeError(
            "Слишком мало train-данных для AST TimeSeriesSplit с текущим context_len/pred_len. "
            "Уменьши context_len в AST_PARAM_GRID или увеличь объем данных."
        )
    ts_splits = max(2, min(requested_splits, max_splits_by_context))
    cv = TimeSeriesSplit(n_splits=ts_splits)
    estimator = _ASTGridSearchEstimator(use_cuda=bool(use_cuda), score_metric=scoring)

    grid = GridSearchCV(
        estimator=estimator,
        param_grid=param_grid,
        scoring=None,
        cv=cv,
        n_jobs=jobs,
        refit=True,
        verbose=1,
        return_train_score=False,
    )

    print(f"AST GridSearchCV: train_points={len(train)} | test_points={len(test)} | n_splits={ts_splits}")
    grid.fit(x_train, y_train)

    cv_results_df = pd.DataFrame(grid.cv_results_)
    cv_results_df["score"] = -cv_results_df["mean_test_score"]
    cv_results_df["score_std"] = cv_results_df["std_test_score"]
    cv_results_df = cv_results_df.sort_values(["score", "score_std"], na_position="last").reset_index(drop=True)

    best_params = dict(grid.best_params_)
    print(f"AST GridSearchCV: best {scoring}={float(-grid.best_score_):.6f} | {best_params}")

    # Финальная оценка на holdout test: передаем full и test_ratio, совпадающий с внешним split.
    model_metrics, pred_df = run_ast_astmain_style(
        full_series=pd.Series(values),
        test_ratio=float(test_ratio),
        context_len=int(best_params["context_len"]),
        pred_len=int(best_params.get("pred_len", 1)),
        epochs=int(best_params["epochs"]),
        batch_size=int(best_params["batch_size"]),
        lr=float(best_params["lr"]),
        min_lr=float(best_params.get("min_lr", 1e-5)),
        lambda_adv=float(best_params.get("lambda_adv", 0.008)),
        d_steps=int(best_params.get("d_steps", 2)),
        d_model=int(best_params["d_model"]),
        n_heads=int(best_params["n_heads"]),
        n_layers=int(best_params["n_layers"]),
        ff_dim=int(best_params["ff_dim"]),
        dropout=float(best_params["dropout"]),
        topk=int(best_params["topk"]),
        use_cuda=bool(use_cuda),
        use_amp=bool(best_params.get("use_amp", True)),
        use_gan=best_params.get("use_gan", "auto"),
    )

    split_info = {
        "full_points": int(len(values)),
        "train_points": int(len(train)),
        "test_points": int(len(test)),
        "test_ratio": float(test_ratio),
        "cv_n_splits": int(ts_splits),
    }

    return best_params, cv_results_df, model_metrics, pred_df, split_info


def _fetch_week_by_random_end(base_url: str, symbol: str, interval: str, week_hours: int, max_years_back: int = 4) -> pd.DataFrame:
    endpoint = f"{base_url}/v5/market/kline"
    now_ms = int(time.time() * 1000)
    min_end_ms = now_ms - int(max_years_back * 365 * 24 * 3600 * 1000)

    rand_end = random.randint(min_end_ms, now_ms - 24 * 3600 * 1000)
    params = {
        "category": "linear",
        "symbol": symbol,
        "interval": interval,
        "limit": week_hours,
        "end": rand_end,
    }

    resp = requests.get(endpoint, params=params, timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("retCode") != 0:
        raise RuntimeError(f"Bybit error: {payload}")

    batch = payload.get("result", {}).get("list", [])
    if not batch:
        return pd.DataFrame()

    cols = ["start_ms", "open", "high", "low", "close", "volume", "turnover"]
    df = pd.DataFrame(batch, columns=cols).drop_duplicates(subset=["start_ms"])
    df["start_ms"] = pd.to_numeric(df["start_ms"], errors="coerce")
    for c in ["open", "high", "low", "close", "volume", "turnover"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna().copy()
    df["timestamp"] = pd.to_datetime(df["start_ms"], unit="ms", utc=True)
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df[["timestamp", "open", "high", "low", "close", "volume", "turnover"]]


def run_weekly_random_validation(
    *,
    processor,
    config,
    output_dir: Path,
    run_symbol: str,
    run_naive: Callable,
    run_arima: Callable,
    run_sarima: Callable,
    run_nbeats: Callable | None,
    run_lstm: Callable | None,
    arima_order,
    sarima_order,
    sarima_seasonal_order,
    run_ast_fn: Callable = run_ast_astmain_style,
    week_hours: int = 7 * 24,
    n_weeks: int = 10,
    max_random_years_back: int = 4,
    max_fetch_attempts_per_week: int = 20,
    parallel_workers: int = 1,
    sarima_refit_every: int = 48,
    sarima_fit_window: int = 800,
    sarima_maxiter: int = 40,
    sarima_max_concurrency: int | None = None,
    enabled_models: list[str] | None = None,
    fixed_train_series: pd.Series | None = None,
):
    default_models = ["naive", "arima", "sarima", "ast"]
    if run_nbeats is not None:
        default_models.append("nbeats")
    if run_lstm is not None:
        default_models.append("lstm")

    if enabled_models is None:
        models_to_check = default_models
    else:
        allowed = {"naive", "arima", "sarima", "ast", "nbeats", "lstm"}
        normalized = [str(m).strip().lower() for m in enabled_models]
        models_to_check = [m for m in default_models if m in normalized and m in allowed]

    if len(models_to_check) == 0:
        raise RuntimeError("Не выбрано ни одной модели для weekly validation.")
    print(f"Проверяем модели: {models_to_check}")
    print(f"Скачиваем {n_weeks} случайных недельных датасетов для {run_symbol} ...")

    rows = []
    selected_weeks_info = []

    cpu_count = int(os.cpu_count() or 8)
    sarima_slots = int(sarima_max_concurrency) if sarima_max_concurrency is not None else max(1, min(4, cpu_count // 2))
    sarima_slots = max(1, sarima_slots)
    sarima_sem = threading.BoundedSemaphore(value=sarima_slots)

    if fixed_train_series is not None:
        fixed_train = pd.to_numeric(fixed_train_series, errors="coerce").dropna().astype(float).reset_index(drop=True)
        if len(fixed_train) < 80:
            raise RuntimeError("fixed_train_series слишком короткий для weekly test-only режима")
    else:
        fixed_train = None

    def _evaluate_week(week_idx: int):
        week_raw = pd.DataFrame()
        picked_start = None
        picked_end = None
        week_rows = []

        for _attempt in range(1, max_fetch_attempts_per_week + 1):
            try:
                candidate = _fetch_week_by_random_end(
                    base_url=config.base_url,
                    symbol=run_symbol,
                    interval=config.interval,
                    week_hours=week_hours,
                    max_years_back=max_random_years_back,
                )
                if len(candidate) < week_hours:
                    continue

                ts_min = candidate["timestamp"].min()
                ts_max = candidate["timestamp"].max()
                if pd.isna(ts_min) or pd.isna(ts_max):
                    continue

                week_raw = candidate.iloc[-week_hours:].copy().reset_index(drop=True)
                picked_start = week_raw["timestamp"].min()
                picked_end = week_raw["timestamp"].max()
                break
            except Exception:
                continue

        if len(week_raw) < week_hours:
            return {
                "week": week_idx + 1,
                "rows": [],
                "info": None,
                "message": f"Week {week_idx + 1}: не удалось скачать валидный недельный датасет (попыток: {max_fetch_attempts_per_week})",
            }

        chunk_clean, _ = processor.process(week_raw)
        series = chunk_clean[config.target_col].astype(float).reset_index(drop=True)

        if len(series) < 80:
            return {
                "week": week_idx + 1,
                "rows": [],
                "info": None,
                "message": f"Week {week_idx + 1}: пропуск (после очистки слишком мало точек: {len(series)})",
            }

        if fixed_train is not None:
            # Test-only режим: weekly ряд используется целиком как тест,
            # train остается фиксированным (из основного пайплайна).
            train_w = fixed_train
            test_w = series.reset_index(drop=True)
        else:
            split_idx = int(len(series) * 0.8)
            split_idx = max(20, min(split_idx, len(series) - 10))
            train_w = series.iloc[:split_idx].reset_index(drop=True)
            test_w = series.iloc[split_idx:].reset_index(drop=True)

        info = {
            "week": week_idx + 1,
            "start_ts": picked_start,
            "end_ts": picked_end,
            "raw_points": int(len(week_raw)),
            "clean_points": int(len(series)),
        }

        message = f"Week {week_idx + 1}/{n_weeks}: {picked_start} .. {picked_end} | train={len(train_w)} test={len(test_w)}"

        for model_name in models_to_check:
            try:
                t0 = time.perf_counter()
                if model_name == "naive":
                    m, _pred = run_naive(train_w, test_w)
                elif model_name == "arima":
                    m, _pred = run_arima(train_w, test_w, order=arima_order, show_progress=False)
                elif model_name == "sarima":
                    with sarima_sem:
                        m, _pred = run_sarima(
                            train_w,
                            test_w,
                            order=sarima_order,
                            seasonal_order=sarima_seasonal_order,
                            refit_every=max(1, int(sarima_refit_every)),
                            fit_window=max(200, int(sarima_fit_window)),
                            maxiter=max(10, int(sarima_maxiter)),
                            show_progress=False,
                            use_cuda=False,
                        )
                elif model_name == "ast":
                    m, _pred = run_ast_fn(
                        full_series=series,
                        test_ratio=0.2,
                        context_len=72,
                        pred_len=1,
                        epochs=6,
                        batch_size=32,
                        lr=5e-4,
                        min_lr=1e-5,
                        lambda_adv=0.003,
                        d_steps=1,
                        d_model=96,
                        n_heads=4,
                        n_layers=2,
                        ff_dim=192,
                        dropout=0.10,
                        topk=24,
                        seed=42 + week_idx,
                        use_gan="auto",
                    )
                elif model_name == "nbeats":
                    m, _pred = run_nbeats(
                        train_w,
                        test_w,
                        context_len=96,
                        n_blocks=3,
                        layers=3,
                        layer_size=192,
                        epochs=12,
                        batch_size=64,
                        lr=1e-3,
                        use_cuda=True,
                        show_progress=False,
                    )
                elif model_name == "lstm":
                    m, _pred = run_lstm(
                        train_w,
                        test_w,
                        context_len=72,
                        hidden_size=96,
                        num_layers=2,
                        dropout=0.05,
                        epochs=20,
                        batch_size=32,
                        lr=6e-4,
                        use_cuda=True,
                        show_progress=False,
                    )
                else:
                    continue
                elapsed_sec = float(time.perf_counter() - t0)

                week_rows.append(
                    {
                        "symbol": run_symbol,
                        "week": week_idx + 1,
                        "start_ts": picked_start,
                        "end_ts": picked_end,
                        "model": model_name,
                        "n_points": int(len(series)),
                        "MAE": float(m["MAE"]),
                        "RMSE": float(m["RMSE"]),
                        "MAPE": float(m["MAPE"]),
                        "duration_sec": elapsed_sec,
                    }
                )
            except Exception as ex:
                week_rows.append(
                    {
                        "symbol": run_symbol,
                        "week": week_idx + 1,
                        "start_ts": picked_start,
                        "end_ts": picked_end,
                        "model": model_name,
                        "n_points": int(len(series)),
                        "MAE": np.nan,
                        "RMSE": np.nan,
                        "MAPE": np.nan,
                        "duration_sec": np.nan,
                        "error": str(ex),
                    }
                )

        return {
            "week": week_idx + 1,
            "rows": week_rows,
            "info": info,
            "message": message,
        }

    workers = int(max(1, parallel_workers))
    if workers > 1:
        print(f"Параллельный режим: {workers} воркеров | SARIMA одновременно: {sarima_slots}")
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_evaluate_week, i): i for i in range(n_weeks)}
            results = []
            for future in as_completed(futures):
                results.append(future.result())
    else:
        results = [_evaluate_week(i) for i in range(n_weeks)]

    for result in sorted(results, key=lambda x: x["week"]):
        if result.get("message"):
            print(result["message"])
        if result.get("info") is not None:
            selected_weeks_info.append(result["info"])
        rows.extend(result.get("rows", []))

    weekly_metrics_df = pd.DataFrame(rows)
    if len(weekly_metrics_df) == 0:
        raise RuntimeError("Не удалось получить weekly-метрики ни для одного случайного недельного датасета.")

    summary = (
        weekly_metrics_df.groupby("model", as_index=False)[["MAE", "RMSE", "MAPE"]]
        .mean()
        .sort_values("MAE")
        .reset_index(drop=True)
    )

    weeks_info_df = pd.DataFrame(selected_weeks_info)
    weekly_ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    weekly_path = output_dir / f"{run_symbol}_weekly{n_weeks}_random_metrics_{weekly_ts}.csv"
    summary_path = output_dir / f"{run_symbol}_weekly{n_weeks}_random_summary_{weekly_ts}.csv"
    weeks_info_path = output_dir / f"{run_symbol}_weekly{n_weeks}_random_weeks_{weekly_ts}.csv"

    weekly_metrics_df.to_csv(weekly_path, index=False)
    summary.to_csv(summary_path, index=False)
    weeks_info_df.to_csv(weeks_info_path, index=False)

    return weekly_metrics_df, summary, weeks_info_df, weekly_path, summary_path, weeks_info_path
