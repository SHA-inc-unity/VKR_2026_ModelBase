"""Distribution / tail diagnostics for the loaded dataset session.

Computes summary statistics that help the user judge the *shape* of
log-returns of the close price (``close_price``):

* skewness
* excess kurtosis (Fisher form — N(0,1) has 0)
* Jarque-Bera test statistic + p-value
* histogram of returns (bins + counts) for the front-end
* a normal-distribution overlay sampled at the bin centres so the chart can
  draw both as a single dataset

The diagnostic is purely advisory — it does not feed back into the cleaning
pipeline. It tells the user "your data is far from Gaussian, plan
accordingly".
"""
from __future__ import annotations

import gc
import logging
from typing import Any

_LOG = logging.getLogger(__name__)

DEFAULT_BINS   = 50
DEFAULT_COL    = "close_price"
# JB is asymptotic (chi² with 2 df), so it's only reliable above ~2k samples.
JB_MIN_SAMPLES = 2_000


def _coerce_int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        v = int(value) if value is not None else default
    except (TypeError, ValueError):
        v = default
    if v < lo: v = lo
    if v > hi: v = hi
    return v


def _verdict(kurtosis: float, jb_p: float, n: int) -> str:
    """Plain-language interpretation of the diagnostic numbers."""
    if n < JB_MIN_SAMPLES:
        return f"Sample too small ({n} < {JB_MIN_SAMPLES}); JB unreliable."
    if jb_p < 1e-3:
        if kurtosis > 3.0:
            return f"Heavy tails detected (excess kurtosis = {kurtosis:.2f}); not normal (JB p<0.001)."
        if kurtosis < -1.0:
            return f"Light tails / platykurtic (excess kurtosis = {kurtosis:.2f}); not normal (JB p<0.001)."
        return f"Distribution rejected as normal by JB (p<0.001); skewness/kurtosis dominate."
    return "Distribution appears compatible with normal."


async def handle_distribution(envelope) -> dict:
    """Compute distribution diagnostics for log-returns of ``column``.

    Payload fields (all optional):

    * ``column`` — column name; defaults to ``close_price``.
    * ``bins``   — histogram bin count, ``[10, 200]``.

    Reply:

    .. code-block:: json

        {
          "column": "close_price",
          "n":         12345,
          "mean":      0.0,
          "std":       0.012,
          "skewness":  -0.41,
          "kurtosis":   8.3,
          "jb_stat":  410.2,
          "jb_p":     1.5e-89,
          "verdict":  "Heavy tails ...",
          "bins":     [{"x": -0.05, "count": 12, "normal": 8.4}, ...]
        }
    """
    import numpy as np
    import pandas as pd
    from scipy import stats
    from backend.anomaly.session import get_session, read_parquet_contiguous

    payload = envelope.payload or {}
    column  = (payload.get("column") or DEFAULT_COL).strip()
    bins    = _coerce_int(payload.get("bins"), DEFAULT_BINS, 10, 200)

    parquet = get_session().get_parquet_path()
    meta    = get_session().get_metadata()
    if parquet is None or meta is None:
        return {"error": "no_session_loaded"}

    df = None
    try:
        # Use a generous row cap: statistical diagnostics are reliable above
        # ~2k samples and barely change for N > 200k, so reading the full
        # dataset on a 5M-row session is wasteful. 500k rows gives accurate
        # skew/kurtosis/JB while keeping disk I/O bounded.
        #
        # CRITICAL: we MUST read a contiguous tail-slice, not a strided
        # sample, because log-returns are computed as np.diff(log(prices))
        # and require neighbouring rows to actually be neighbours in time.
        # A row-group-strided sample would make np.diff compute "returns"
        # between non-adjacent timestamps and silently bias skew/kurtosis/JB.
        MAX_DIST_ROWS = 500_000
        total_rows = (meta or {}).get("row_count") or 0
        df = read_parquet_contiguous(parquet, [column], MAX_DIST_ROWS, total_rows)
        if column not in df.columns:
            return {"error": f"column not present: {column}"}
        # Log-returns: ln(c_t / c_{t-1}). Drop non-positive prices to avoid
        # log(0) / log(<0); they're either bad data or padding.
        prices = df[column].astype("float64")
        prices = prices[prices > 0]
        if len(prices) < 2:
            return {"error": "not_enough_positive_values"}
        returns = np.diff(np.log(prices.to_numpy()))
        # Strip non-finite (e.g. inf from extremely small priors).
        returns = returns[np.isfinite(returns)]
        n = int(returns.size)
        if n < 10:
            return {"error": "not_enough_returns"}

        mean = float(np.mean(returns))
        std  = float(np.std(returns, ddof=1)) if n > 1 else 0.0
        # Fisher-form skew/kurtosis: a Normal sample has skew=0 / excess kurt=0.
        skew = float(stats.skew(returns, bias=False))
        kurt = float(stats.kurtosis(returns, fisher=True, bias=False))
        # Jarque-Bera: scipy returns a Result object in modern versions; older
        # versions return a tuple. Handle both transparently.
        jb_res = stats.jarque_bera(returns)
        jb_stat = float(getattr(jb_res, "statistic", jb_res[0]))
        jb_p    = float(getattr(jb_res, "pvalue",    jb_res[1]))

        # Histogram: clip to ±5σ to avoid one extreme value collapsing the rest.
        if std > 0:
            lo, hi = mean - 5 * std, mean + 5 * std
        else:
            lo, hi = float(returns.min()), float(returns.max())
            if lo == hi:
                hi = lo + 1e-9
        clipped = np.clip(returns, lo, hi)
        counts, edges = np.histogram(clipped, bins=bins, range=(lo, hi))
        centres = (edges[:-1] + edges[1:]) / 2.0
        bin_w   = float(edges[1] - edges[0])
        # Normal-curve overlay sampled at bin centres, scaled to expected
        # *count* per bin so it overlays the histogram directly.
        if std > 0 and bin_w > 0:
            pdf = stats.norm.pdf(centres, loc=mean, scale=std)
            normal_counts = pdf * n * bin_w
        else:
            normal_counts = np.zeros_like(centres)

        bins_out = [
            {"x": float(c), "count": int(cnt), "normal": float(nc)}
            for c, cnt, nc in zip(centres, counts, normal_counts)
        ]

        return {
            "column":   column,
            "n":        n,
            "mean":     mean,
            "std":      std,
            "skewness": skew,
            "kurtosis": kurt,
            "jb_stat":  jb_stat,
            "jb_p":     jb_p,
            "verdict":  _verdict(kurt, jb_p, n),
            "bins":     bins_out,
        }
    except Exception as exc:  # noqa: BLE001
        _LOG.exception("distribution | failed")
        return {"error": str(exc)}
    finally:
        del df
        gc.collect()
