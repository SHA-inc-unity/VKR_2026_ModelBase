import os
import sys
import json
import numpy as np
import pandas as pd

# Ensure project root is on sys.path so `catboost_floader` can be imported when
# this script is run directly (e.g. `python scripts/review_backtest_outputs.py`).
# When a script is executed by filename, Python sets sys.path[0] to the script
# directory (scripts/), so the package in the parent folder is not found.
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from catboost_floader.core import config as cfg

out = {}
csv_path = os.path.join(cfg.BACKTEST_DIR, "direct_backtest_results.csv")
if not os.path.exists(csv_path):
    print(json.dumps({"error": f"{csv_path} not found"}))
    sys.exit(2)

try:
    df = pd.read_csv(csv_path)
except Exception as e:
    print(json.dumps({"error": f"failed to read csv: {e}"}))
    sys.exit(3)

required = ["direct_pred_return", "target_return"]
for c in required:
    if c not in df.columns:
        print(json.dumps({"error": "required columns missing", "cols": list(df.columns)}))
        sys.exit(4)

# drop NaNs
df = df.dropna(subset=required)
if df.empty:
    print(json.dumps({"error": "no valid rows after dropping NA"}))
    sys.exit(5)

out["rows"] = int(len(df))
out["mean_abs_pred_return"] = float(np.mean(np.abs(df["direct_pred_return"])))
out["mean_abs_target_return"] = float(np.mean(np.abs(df["target_return"])))
out["median_abs_pred_return"] = float(np.median(np.abs(df["direct_pred_return"])))
out["median_abs_target_return"] = float(np.median(np.abs(df["target_return"])))

# Use deadband from config for labels
dead = float(getattr(cfg, "DIRECTION_DEADBAND", 0.0005))

def label_from_return(arr, deadband):
    a = np.asarray(arr, dtype=float)
    labs = np.zeros_like(a, dtype=int)
    labs[a > deadband] = 1
    labs[a < -deadband] = -1
    return labs

pred_lbl = label_from_return(df["direct_pred_return"].to_numpy(), dead)
true_lbl = label_from_return(df["target_return"].to_numpy(), dead)

out["deadband"] = dead
out["pred_label_counts"] = {
    "-1": int(np.sum(pred_lbl == -1)),
    "0": int(np.sum(pred_lbl == 0)),
    "1": int(np.sum(pred_lbl == 1)),
}
out["true_label_counts"] = {
    "-1": int(np.sum(true_lbl == -1)),
    "0": int(np.sum(true_lbl == 0)),
    "1": int(np.sum(true_lbl == 1)),
}

# confusion matrix
conf = {}
for t in (-1, 0, 1):
    for p in (-1, 0, 1):
        conf[f"true_{t}_pred_{p}"] = int(np.sum((true_lbl == t) & (pred_lbl == p)))
out["confusion_matrix"] = conf

# sign accuracy using labels
out["sign_accuracy_label"] = float(np.mean(pred_lbl == true_lbl))

# simple bias: mean(pred) vs mean(target)
out["mean_pred_return"] = float(np.mean(df["direct_pred_return"]))
out["mean_target_return"] = float(np.mean(df["target_return"]))

# Save brief report
print(json.dumps(out, ensure_ascii=False, indent=2))
