"""Smoke-tests for backend.model.pdf_report.generate_session_pdf*."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("matplotlib")

from backend.model.pdf_report import (
    generate_session_pdf,
    generate_session_pdf_bytes,
)


class _DummyModel:
    """Заглушка CatBoost с get_feature_importance()."""

    def __init__(self, n_features: int = 6) -> None:
        self._n = n_features

    def get_feature_importance(self) -> np.ndarray:
        return np.linspace(1.0, 30.0, self._n)


def _fake_session() -> dict:
    n = 200
    ts = pd.Series(pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC"))
    rng = np.random.default_rng(42)
    y_test = rng.normal(0, 0.01, size=n)
    y_pred = y_test * 0.2 + rng.normal(0, 0.005, size=n)
    return {
        "prefix": "catboost_testcoin_1h",
        "model":  _DummyModel(n_features=6),
        "metrics": {
            "sharpe": 1.23, "RMSE": 0.009, "MAE": 0.005, "R2": 0.04,
            "dir_acc_pct": 52.3, "mae_pct": 98.1, "profit_factor": 1.15,
        },
        "best_params": {
            "iterations": 500, "depth": 6, "learning_rate": 0.03,
        },
        "feature_cols": [f"feat_{i}" for i in range(6)],
        "y_test": y_test,
        "y_pred": y_pred,
        "ts_test": ts,
        "target_col": "target_return_1",
        "overfit_diagnostics": {
            "learning_curve": {
                "iterations": list(range(100)),
                "val_rmse": list(np.linspace(0.01, 0.008, 100)),
                "best_iteration": 55,
                "train_rmse_at_best": 0.0075,
            },
        },
    }


def test_generate_session_pdf_to_file(tmp_path):
    sess = _fake_session()
    out = generate_session_pdf(
        **sess,
        output_dir=tmp_path,
    )
    assert out.exists()
    assert out.suffix == ".pdf"
    # PDF-файл должен начинаться с %PDF
    assert out.read_bytes()[:4] == b"%PDF"


def test_generate_session_pdf_bytes_roundtrip():
    sess = _fake_session()
    data = generate_session_pdf_bytes(**sess)
    assert isinstance(data, bytes)
    assert data.startswith(b"%PDF")
    assert len(data) > 5_000  # 5 страниц A4 не бывают короче пары КБ


def test_generate_session_pdf_handles_missing_overfit(tmp_path):
    sess = _fake_session()
    sess["overfit_diagnostics"] = None
    out = generate_session_pdf(**sess, output_dir=tmp_path)
    assert out.exists()
    assert out.read_bytes()[:4] == b"%PDF"
