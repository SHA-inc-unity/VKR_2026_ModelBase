import os
import sys
import json
import numpy as np
import pandas as pd

# ensure project root is importable
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from catboost_floader.core import config as cfg


def label_from_return(arr, deadband):
    a = np.asarray(arr, dtype=float)
    labs = np.zeros_like(a, dtype=int)
    labs[a > deadband] = 1
    labs[a < -deadband] = -1
    return labs


def compute_confusion(pred_lbl, true_lbl):
    conf = {}
    for t in (-1, 0, 1):
        for p in (-1, 0, 1):
            conf[f"true_{t}_pred_{p}"] = int(np.sum((true_lbl == t) & (pred_lbl == p)))
    return conf


def main():
    out = {}
    backtest_dir = cfg.BACKTEST_DIR
    merged_path = os.path.join(backtest_dir, "backtest_results.csv")
    direct_path = os.path.join(backtest_dir, "direct_backtest_results.csv")
    range_path = os.path.join(backtest_dir, "range_backtest_results.csv")

    if os.path.exists(merged_path):
        df = pd.read_csv(merged_path)
    elif os.path.exists(direct_path):
        df = pd.read_csv(direct_path)
    else:
        print(json.dumps({"error": "no backtest CSV found"}))
        sys.exit(2)

    # load range results if available
    range_df = pd.read_csv(range_path) if os.path.exists(range_path) else None

    required = ["target_return", "close"]
    if not all(c in df.columns for c in required):
        print(json.dumps({"error": "required columns missing", "cols": list(df.columns)}))
        sys.exit(3)

    # drop NaNs in target_return/close
    df = df.dropna(subset=required)
    if df.empty:
        print(json.dumps({"error": "no valid rows after dropping NA"}))
        sys.exit(4)

    dead = float(getattr(cfg, "DIRECTION_DEADBAND", 0.0005))

    y_true = df["target_return"].to_numpy(dtype=float)
    true_lbl = label_from_return(y_true, dead)

    results = {}

    # Direct model sign accuracy (float sign method and label method)
    if "direct_pred_return" in df.columns:
        y_pred = df["direct_pred_return"].to_numpy(dtype=float)
        results["direct_sign_accuracy_sign"] = float(np.mean(np.sign(y_true) == np.sign(y_pred)))
        pred_lbl = label_from_return(y_pred, dead)
        results["direct_sign_accuracy_label"] = float(np.mean(pred_lbl == true_lbl))
        results["direct_label_counts"] = {"-1": int((pred_lbl == -1).sum()), "0": int((pred_lbl == 0).sum()), "1": int((pred_lbl == 1).sum())}

    # If direction submodel columns exist, compute per-class confusion and metrics
    if "direction_pred_label" in df.columns:
        dir_lbl = pd.to_numeric(df["direction_pred_label"], errors="coerce").fillna(0).to_numpy(dtype=int)
        results["direction_label_accuracy"] = float(np.mean(dir_lbl == true_lbl))
        results["direction_confusion"] = compute_confusion(dir_lbl, true_lbl)

    if "direction_pred_expectation" in df.columns:
        dir_exp = df["direction_pred_expectation"].to_numpy(dtype=float)
        # sign accuracy using expectation sign
        dir_signs = np.sign(dir_exp)
        results["direction_expectation_sign_accuracy"] = float(np.mean(np.sign(y_true) == dir_signs))

    # Movement model: magnitude vs absolute target stats
    if "movement_pred_magnitude" in df.columns:
        mov = df["movement_pred_magnitude"].to_numpy(dtype=float)
        results["movement_mean_abs_pred"] = float(np.mean(np.abs(mov)))
        results["movement_mean_abs_target"] = float(np.mean(np.abs(y_true)))

    # Range model: use center of predicted low/high to compute sign
    if "range_pred_low" in df.columns and "range_pred_high" in df.columns:
        close = df["close"].to_numpy(dtype=float)
        center_price = (df["range_pred_low"].to_numpy(dtype=float) + df["range_pred_high"].to_numpy(dtype=float)) / 2.0
        center_return = (center_price - close) / (np.abs(close) + 1e-12)
        results["range_center_sign_accuracy_sign"] = float(np.mean(np.sign(y_true) == np.sign(center_return)))
        center_lbl = label_from_return(center_return, dead)
        results["range_center_sign_accuracy_label"] = float(np.mean(center_lbl == true_lbl))

    # Baselines
    if "baseline_persistence_price" in df.columns:
        base_p = df["baseline_persistence_price"].to_numpy(dtype=float)
        base_p_ret = (base_p - df["close"].to_numpy(dtype=float)) / (np.abs(df["close"].to_numpy(dtype=float)) + 1e-12)
        results["baseline_persistence_sign_accuracy_sign"] = float(np.mean(np.sign(y_true) == np.sign(base_p_ret)))
        results["baseline_persistence_sign_accuracy_label"] = float(np.mean(label_from_return(base_p_ret, dead) == true_lbl))

    if "baseline_rolling_price" in df.columns:
        base_r = df["baseline_rolling_price"].to_numpy(dtype=float)
        base_r_ret = (base_r - df["close"].to_numpy(dtype=float)) / (np.abs(df["close"].to_numpy(dtype=float)) + 1e-12)
        results["baseline_rolling_sign_accuracy_sign"] = float(np.mean(np.sign(y_true) == np.sign(base_r_ret)))
        results["baseline_rolling_sign_accuracy_label"] = float(np.mean(label_from_return(base_r_ret, dead) == true_lbl))

    out["rows"] = int(len(df))
    out["deadband"] = dead
    out["results"] = results

    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
