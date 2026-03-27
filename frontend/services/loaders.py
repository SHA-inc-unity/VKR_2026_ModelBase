from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]
# Prefer repository-level `outputs/` but fall back to `catboost_floader/outputs` if present.
REPO_OUTPUTS = PROJECT_ROOT / "outputs"
PKG_OUTPUTS = PROJECT_ROOT / "catboost_floader" / "outputs"
if REPO_OUTPUTS.exists():
    OUTPUTS_DIR = REPO_OUTPUTS
elif PKG_OUTPUTS.exists():
    OUTPUTS_DIR = PKG_OUTPUTS
else:
    # default to repo outputs path (UI will handle empty dirs)
    OUTPUTS_DIR = REPO_OUTPUTS

ARTIFACTS_DIR = OUTPUTS_DIR / "artifacts"
LOG_DIR = OUTPUTS_DIR / "logs"
BACKTEST_DIR = OUTPUTS_DIR / "backtest_results"
REPORT_DIR = OUTPUTS_DIR / "reports"


def _safe_read_csv(path: Path, parse_ts: bool = True) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    df = pd.read_csv(path)
    if parse_ts:
        for col in ["timestamp", "start_ts", "end_ts"]:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], utc=True, errors="coerce")
    return df


def _safe_read_json(path: Path) -> Optional[dict]:
    if not path.exists() or path.stat().st_size == 0:
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@st.cache_data(show_spinner=False)
def list_market_files() -> list[str]:
    if not ARTIFACTS_DIR.exists():
        return []
    return sorted(p.name for p in ARTIFACTS_DIR.glob("*_market_dataset.csv")) or sorted(p.name for p in ARTIFACTS_DIR.glob("*_klines.csv"))


@st.cache_data(show_spinner=False)
def load_market_data(file_name: Optional[str] = None) -> pd.DataFrame:
    files = list_market_files()
    if not files:
        return pd.DataFrame()
    chosen = file_name or files[-1]
    path = ARTIFACTS_DIR / chosen
    df = _safe_read_csv(path)
    for col in df.columns:
        if col != "timestamp":
            try:
                df[col] = pd.to_numeric(df[col])
            except Exception:
                pass
    return df.sort_values("timestamp").drop_duplicates(subset=["timestamp"]).reset_index(drop=True) if "timestamp" in df.columns else df


@st.cache_data(show_spinner=False)
def load_anomalies() -> pd.DataFrame:
    return _safe_read_csv(LOG_DIR / "anomaly_flags.csv")


@st.cache_data(show_spinner=False)
def load_anomaly_windows() -> pd.DataFrame:
    return _safe_read_csv(LOG_DIR / "anomaly_windows.csv")


@st.cache_data(show_spinner=False)
def load_backtest_results() -> pd.DataFrame:
    return _safe_read_csv(BACKTEST_DIR / "backtest_results.csv")


@st.cache_data(show_spinner=False)
def load_backtest_summary() -> Optional[dict]:
    return _safe_read_json(BACKTEST_DIR / "backtest_summary.json")


@st.cache_data(show_spinner=False)
def load_live_snapshot() -> Optional[dict]:
    return _safe_read_json(LOG_DIR / "latest_live_prediction.json")


@st.cache_data(show_spinner=False)
def load_pipeline_summary() -> Optional[dict]:
    return _safe_read_json(REPORT_DIR / "pipeline_summary.json")


@st.cache_data(show_spinner=False)
def load_feature_importance() -> Optional[dict]:
    return _safe_read_json(REPORT_DIR / "feature_importance.json")


def get_latest_prediction(backtest_df: pd.DataFrame) -> Optional[dict]:
    if backtest_df.empty:
        return None
    return backtest_df.dropna(how="all").iloc[-1].to_dict()


def compute_market_summary(df: pd.DataFrame) -> dict:
    if df.empty:
        return {}
    out = {
        "last_close": float(df["close"].iloc[-1]),
        "prev_close": float(df["close"].iloc[-2]) if len(df) > 1 else float(df["close"].iloc[-1]),
        "last_volume": float(df["volume"].iloc[-1]) if "volume" in df.columns else None,
        "rows": int(len(df)),
        "mark_div": float(((df["close"] - df["mark_close"]) / (df["close"] + 1e-8)).iloc[-1]) if "mark_close" in df.columns else None,
    }
    out["change_1h"] = (float(df["close"].iloc[-1]) / float(df["close"].iloc[-13]) - 1.0) if len(df) >= 13 else None
    out["change_24h"] = (float(df["close"].iloc[-1]) / float(df["close"].iloc[-289]) - 1.0) if len(df) >= 289 else None
    returns = df["close"].pct_change().dropna()
    out["volatility_1h"] = float(returns.tail(12).std()) if len(returns) >= 12 else None
    return out
