"""Extra tests for backend.model.pdf_report — uncovered branches."""
from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("matplotlib")

from backend.model.pdf_report import (
    _fmt_num,
    generate_session_pdf,
    generate_session_pdf_bytes,
)


# ---------------------------------------------------------------------------
# _fmt_num — exception branch (non-numeric values)
# ---------------------------------------------------------------------------

def test_fmt_num_converts_float():
    assert _fmt_num(3.14159, digits=2) == "3.14"


def test_fmt_num_none_falls_through_to_str():
    # float(None) raises TypeError → falls to return str(v)
    result = _fmt_num(None)
    assert result == "None"


def test_fmt_num_non_numeric_string():
    # float("abc") raises ValueError → falls to return str(v)
    result = _fmt_num("abc")
    assert result == "abc"


# ---------------------------------------------------------------------------
# generate_session_pdf / generate_session_pdf_bytes — no-matplotlib branches
# ---------------------------------------------------------------------------

def _fake_kwargs():
    n = 50
    rng = np.random.default_rng(7)
    y = rng.normal(0, 0.01, n)
    ts = pd.Series(pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC"))

    class _M:
        def get_feature_importance(self):
            return np.ones(4)

    return dict(
        prefix="test",
        model=_M(),
        metrics={"sharpe": 1.0, "RMSE": 0.01},
        best_params={"depth": 6},
        feature_cols=["a", "b", "c", "d"],
        y_test=y,
        y_pred=y * 0.8,
        ts_test=ts,
        target_col="target_return_1",
        overfit_diagnostics=None,
    )


def test_generate_session_pdf_raises_import_error_when_no_matplotlib(tmp_path):
    with patch("backend.model.pdf_report._HAS_MATPLOTLIB", False):
        with pytest.raises(ImportError, match="matplotlib"):
            generate_session_pdf(**_fake_kwargs(), output_dir=tmp_path)


def test_generate_session_pdf_bytes_raises_import_error_when_no_matplotlib():
    with patch("backend.model.pdf_report._HAS_MATPLOTLIB", False):
        with pytest.raises(ImportError, match="matplotlib"):
            generate_session_pdf_bytes(**_fake_kwargs())


# ---------------------------------------------------------------------------
# _page_feature_importance — exception branch (model raises on get_feature_importance)
# ---------------------------------------------------------------------------

def test_generate_session_pdf_model_raises_on_feature_importance(tmp_path):
    """Covers the except Exception block in _page_feature_importance."""
    class _FailingModel:
        def get_feature_importance(self):
            raise RuntimeError("no fi available")

    kwargs = _fake_kwargs()
    kwargs["model"] = _FailingModel()
    out = generate_session_pdf(**kwargs, output_dir=tmp_path)
    assert out.exists()
    assert out.read_bytes()[:4] == b"%PDF"
