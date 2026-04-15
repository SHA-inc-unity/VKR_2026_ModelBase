import argparse
import json
import os
import sys
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import precision_recall_fscore_support


script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from catboost_floader.core import config as cfg
from catboost_floader.core.utils import load_json, save_json


REQUIRED_COLUMNS = [
    "close",
    "target_future_close",
    "target_return",
    "direct_pred_return",
    "direct_pred_price",
    "baseline_persistence_price",
    "movement_pred_magnitude",
]
PROBA_COLUMNS = ["direction_proba_neg", "direction_proba_zero", "direction_proba_pos"]


def regression_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    mae = float(np.mean(np.abs(y_true - y_pred)))
    rmse = float(np.sqrt(np.mean((y_true - y_pred) ** 2)))
    mape = float(np.mean(np.abs((y_true - y_pred) / (np.abs(y_true) + 1e-8))) * 100)
    return {"MAE": mae, "RMSE": rmse, "MAPE": mape}


def label_from_return(arr: np.ndarray, deadband: float) -> np.ndarray:
    out = np.zeros(len(arr), dtype=int)
    out[arr > deadband] = 1
    out[arr < -deadband] = -1
    return out


def safe_corr(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 2:
        return float("nan")
    if np.allclose(np.std(a), 0.0) or np.allclose(np.std(b), 0.0):
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def compute_direction_metrics(true_lbl: np.ndarray, pred_lbl: np.ndarray, y_true: np.ndarray, dir_exp: np.ndarray | None) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "label_accuracy": float(np.mean(pred_lbl == true_lbl)),
        "confusion": {},
    }
    for t in (-1, 0, 1):
        for p in (-1, 0, 1):
            metrics["confusion"][f"true_{t}_pred_{p}"] = int(np.sum((true_lbl == t) & (pred_lbl == p)))
    p, r, f, s = precision_recall_fscore_support(true_lbl, pred_lbl, labels=[-1, 0, 1], zero_division=0)
    metrics["prf"] = {
        "per_class": {
            "-1": {"precision": float(p[0]), "recall": float(r[0]), "f1": float(f[0]), "support": int(s[0])},
            "0": {"precision": float(p[1]), "recall": float(r[1]), "f1": float(f[1]), "support": int(s[1])},
            "1": {"precision": float(p[2]), "recall": float(r[2]), "f1": float(f[2]), "support": int(s[2])},
        },
        "macro": {"precision": float(np.mean(p)), "recall": float(np.mean(r)), "f1": float(np.mean(f))},
    }
    if dir_exp is not None:
        metrics["expectation_sign_accuracy"] = float(np.mean(np.sign(dir_exp) == np.sign(y_true)))
    return metrics


def repair_direction_labels(df: pd.DataFrame, fixes: list[str], issues: list[str]) -> pd.DataFrame:
    missing_labels = "direction_pred_label" not in df.columns or df["direction_pred_label"].isna().any()
    if not missing_labels:
        return df

    if all(col in df.columns for col in PROBA_COLUMNS):
        proba_frame = df[PROBA_COLUMNS].apply(pd.to_numeric, errors="coerce")
        valid_mask = proba_frame.notna().all(axis=1)
        if valid_mask.any():
            classes = np.array([-1, 0, 1], dtype=int)
            rebuilt = np.full(len(df), np.nan)
            rebuilt[valid_mask.to_numpy()] = classes[np.argmax(proba_frame.loc[valid_mask].to_numpy(dtype=float), axis=1)]
            df["direction_pred_label"] = rebuilt
            fixes.append("Rebuilt `direction_pred_label` from saved direction probabilities and repaired the backtest CSV.")
            issues.append("`direction_pred_label` contained missing values because label decoding failed during backtest export.")
            return df

    if "direction_pred_expectation" in df.columns:
        exp = pd.to_numeric(df["direction_pred_expectation"], errors="coerce").to_numpy(dtype=float)
        valid_mask = np.isfinite(exp)
        if valid_mask.any():
            rebuilt = np.full(len(df), np.nan)
            rebuilt[valid_mask] = np.sign(exp[valid_mask]).astype(int)
            df["direction_pred_label"] = rebuilt
            fixes.append("Rebuilt `direction_pred_label` from direction sign expectation as a fallback.")
            issues.append("`direction_pred_label` contained missing values because label decoding failed during backtest export.")
            return df

    issues.append("Direction labels are missing and could not be reconstructed from saved outputs.")
    return df


def update_saved_summaries(df: pd.DataFrame, direction_metrics: dict[str, Any]) -> None:
    backtest_summary_path = os.path.join(cfg.BACKTEST_DIR, "backtest_summary.json")
    pipeline_summary_path = os.path.join(cfg.REPORT_DIR, "pipeline_summary.json")

    backtest_summary = load_json(backtest_summary_path) or {}
    if backtest_summary:
        per_model = backtest_summary.setdefault("per_model_sign_accuracy", {})
        per_model["direction"] = direction_metrics
        save_json(backtest_summary, backtest_summary_path)

    pipeline_summary = load_json(pipeline_summary_path) or {}
    if pipeline_summary:
        bt_summary = pipeline_summary.setdefault("backtest_summary", {})
        per_model = bt_summary.setdefault("per_model_sign_accuracy", {})
        per_model["direction"] = direction_metrics
        save_json(pipeline_summary, pipeline_summary_path)


def format_report(
    data_integrity: str,
    direction_quality: str,
    movement_quality: str,
    direct_quality: str,
    metrics: dict[str, float],
    issues: list[str],
    fixes: list[str],
    verdict: str,
    assessment: str,
) -> str:
    issue_lines = "\n".join(f"- {item}" for item in issues) if issues else "- None"
    fix_lines = "\n".join(f"- {item}" for item in fixes) if fixes else "- None"
    return (
        "RESULT VALIDATION REPORT\n\n"
        "1. Data Integrity:\n"
        f"{data_integrity}\n\n"
        "2. Prediction Quality:\n"
        f"- Direction: {direction_quality}\n"
        f"- Movement: {movement_quality}\n"
        f"- Direct: {direct_quality}\n\n"
        "3. Metrics:\n"
        f"MAE: {metrics['MAE']:.6f}\n"
        f"RMSE: {metrics['RMSE']:.6f}\n"
        f"MAPE: {metrics['MAPE']:.6f}\n\n"
        "4. Issues Found:\n"
        f"{issue_lines}\n\n"
        "5. Fixes Applied:\n"
        f"{fix_lines}\n\n"
        "6. Final Verdict:\n"
        f"{verdict}\n\n"
        "7. Model Assessment:\n"
        f'- "{assessment}"'
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="repair saved artifacts in place")
    args = parser.parse_args()

    issues: list[str] = []
    fixes: list[str] = []

    csv_path = os.path.join(cfg.BACKTEST_DIR, "backtest_results.csv")
    if not os.path.exists(csv_path):
        print(
            format_report(
                data_integrity="FAIL",
                direction_quality="BAD",
                movement_quality="BAD",
                direct_quality="BAD",
                metrics={"MAE": float("nan"), "RMSE": float("nan"), "MAPE": float("nan")},
                issues=["Backtest results CSV was not found."],
                fixes=["Unable to repair because the primary artifact is missing."],
                verdict="FAIL",
                assessment="useless",
            )
        )
        return

    df = pd.read_csv(csv_path)
    if df.empty:
        print(
            format_report(
                data_integrity="FAIL",
                direction_quality="BAD",
                movement_quality="BAD",
                direct_quality="BAD",
                metrics={"MAE": float("nan"), "RMSE": float("nan"), "MAPE": float("nan")},
                issues=["Backtest results CSV is empty."],
                fixes=["Unable to repair because there are no rows to validate."],
                verdict="FAIL",
                assessment="useless",
            )
        )
        return

    missing_required = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing_required:
        print(
            format_report(
                data_integrity="FAIL",
                direction_quality="BAD",
                movement_quality="BAD",
                direct_quality="BAD",
                metrics={"MAE": float("nan"), "RMSE": float("nan"), "MAPE": float("nan")},
                issues=[f"Missing required columns: {', '.join(missing_required)}."],
                fixes=["Unable to repair because required prediction/target fields are missing."],
                verdict="FAIL",
                assessment="useless",
            )
        )
        return

    df = df.replace([np.inf, -np.inf], np.nan)
    invalid_mask = df[REQUIRED_COLUMNS].isna().any(axis=1)
    if invalid_mask.any():
        bad_rows = int(invalid_mask.sum())
        issues.append(f"Found {bad_rows} rows with NaN/inf values in required columns.")
        df = df.loc[~invalid_mask].copy()
        fixes.append(f"Removed {bad_rows} invalid rows before recalculating validation metrics.")

    df = repair_direction_labels(df, fixes, issues)

    deadband = float(getattr(cfg, "DIRECTION_DEADBAND", 0.0005))
    y_true_price = pd.to_numeric(df["target_future_close"], errors="coerce").to_numpy(dtype=float)
    y_pred_price = pd.to_numeric(df["direct_pred_price"], errors="coerce").to_numpy(dtype=float)
    y_base_price = pd.to_numeric(df["baseline_persistence_price"], errors="coerce").to_numpy(dtype=float)
    y_true_ret = pd.to_numeric(df["target_return"], errors="coerce").to_numpy(dtype=float)
    y_pred_ret = pd.to_numeric(df["direct_pred_return"], errors="coerce").to_numpy(dtype=float)
    y_move = pd.to_numeric(df["movement_pred_magnitude"], errors="coerce").to_numpy(dtype=float)
    close = pd.to_numeric(df["close"], errors="coerce").to_numpy(dtype=float)

    metrics = regression_metrics(y_true_price, y_pred_price)
    baseline_metrics = regression_metrics(y_true_price, y_base_price)

    true_lbl = label_from_return(y_true_ret, deadband)
    direct_lbl = label_from_return(y_pred_ret, deadband)

    dir_pred_lbl = None
    if "direction_pred_label" in df.columns:
        dir_col = pd.to_numeric(df["direction_pred_label"], errors="coerce").to_numpy(dtype=float)
        if np.isfinite(dir_col).all():
            dir_pred_lbl = dir_col.astype(int)

    dir_exp = None
    if "direction_pred_expectation" in df.columns:
        dir_exp = pd.to_numeric(df["direction_pred_expectation"], errors="coerce").to_numpy(dtype=float)

    if dir_pred_lbl is None:
        issues.append("Direction labels remain incomplete after repair, so classifier quality cannot be trusted.")
        direction_quality = "BAD"
        direction_metrics = {
            "label_accuracy": float("nan"),
            "confusion": {},
            "prf": {},
        }
        direction_majority = 1.0
        direction_acc = 0.0
        direction_majority_baseline = 0.0
    else:
        direction_metrics = compute_direction_metrics(true_lbl, dir_pred_lbl, y_true_ret, dir_exp)
        unique_vals = set(np.unique(dir_pred_lbl).tolist())
        pred_counts = {label: int(np.sum(dir_pred_lbl == label)) for label in (-1, 0, 1)}
        direction_majority = max(pred_counts.values()) / max(len(dir_pred_lbl), 1)
        direction_acc = float(direction_metrics["label_accuracy"])
        true_counts = {label: int(np.sum(true_lbl == label)) for label in (-1, 0, 1)}
        direction_majority_baseline = max(true_counts.values()) / max(len(true_lbl), 1)
        if not unique_vals.issubset({-1, 0, 1}):
            issues.append("Direction labels contain values outside the expected sign set {-1, 0, 1}.")
            direction_quality = "BAD"
        elif direction_majority >= 0.9:
            issues.append(f"Direction predictions are overly imbalanced: one class accounts for {direction_majority:.1%} of outputs.")
            direction_quality = "BAD"
        elif direction_acc <= direction_majority_baseline:
            issues.append(
                f"Direction accuracy ({direction_acc:.2%}) does not beat the naive majority-class baseline ({direction_majority_baseline:.2%})."
            )
            direction_quality = "BAD"
        else:
            direction_quality = "OK"

    movement_variance = float(np.var(y_move))
    movement_mean = float(np.mean(y_move))
    movement_std = float(np.std(y_move))
    movement_outlier_threshold = movement_mean + 8.0 * movement_std
    movement_outlier_frac = float(np.mean(y_move > movement_outlier_threshold)) if movement_std > 0 else 0.0
    if movement_variance <= 0.0:
        issues.append("Movement predictions are constant or zero-variance.")
        movement_quality = "BAD"
    elif movement_outlier_frac > 0.02:
        issues.append(f"Movement predictions contain too many extreme outliers ({movement_outlier_frac:.2%} of rows).")
        movement_quality = "BAD"
    else:
        movement_quality = "OK"

    direct_corr = safe_corr(y_true_ret, y_pred_ret)
    direct_sign_accuracy = float(np.mean(np.sign(y_true_ret) == np.sign(y_pred_ret)))
    naive_copy_rate = float(np.mean(np.abs(y_pred_price - close) <= 1e-6))
    persistence_similarity = safe_corr(y_pred_price, y_base_price)
    if naive_copy_rate > 0.9:
        issues.append(f"Direct predictions copy the current close on {naive_copy_rate:.1%} of rows.")
        direct_quality = "BAD"
    elif not np.isfinite(direct_corr) or direct_corr <= 0.0:
        issues.append(f"Direct predictions have non-positive correlation with the target return ({direct_corr:.4f}).")
        direct_quality = "BAD"
    else:
        direct_quality = "OK"
    if metrics["MAE"] >= baseline_metrics["MAE"]:
        issues.append(
            f"Direct model MAE ({metrics['MAE']:.3f}) is worse than the naive persistence baseline ({baseline_metrics['MAE']:.3f})."
        )
        direct_quality = "BAD"
    if persistence_similarity >= 0.995 and metrics["MAE"] >= baseline_metrics["MAE"]:
        issues.append(
            f"Direct predictions are almost identical to the persistence baseline (corr={persistence_similarity:.4f}) without beating it."
        )

    rmse_mae_ratio = metrics["RMSE"] / max(metrics["MAE"], 1e-8)
    if rmse_mae_ratio > 1.8:
        issues.append(f"RMSE/MAE ratio is too large ({rmse_mae_ratio:.2f}), which suggests unstable large errors.")
    if metrics["MAPE"] >= 100.0:
        issues.append(f"MAPE is invalid at {metrics['MAPE']:.3f} and exceeds the 100% threshold.")

    if "target_future_close" in df.columns:
        horizon_exists = True
    else:
        horizon_exists = False
        issues.append("Prediction horizon is missing because `target_future_close` is unavailable.")

    if horizon_exists and direct_sign_accuracy <= 0.5:
        issues.append(f"Backtest directional accuracy is weak at {direct_sign_accuracy:.2%}.")

    train_diag = load_json(os.path.join(cfg.MODEL_DIR, "direction_training_diagnostics.json")) or {}
    if train_diag.get("confusion_matrix"):
        cm = np.asarray(train_diag["confusion_matrix"], dtype=float)
        train_acc = float(np.trace(cm) / np.maximum(cm.sum(), 1.0))
        if dir_pred_lbl is not None and train_acc - direction_acc > 0.3:
            issues.append(
                f"Direction model is overfit: train accuracy is {train_acc:.2%} versus {direction_acc:.2%} on backtest."
            )

    if args.write:
        df.to_csv(csv_path, index=False)
        if dir_pred_lbl is not None:
            update_saved_summaries(df, direction_metrics)
            fixes.append("Refreshed saved summary JSON files with repaired direction classifier metrics.")

    data_integrity = "PASS"
    if df.empty:
        data_integrity = "FAIL"
        issues.append("No rows remained after integrity cleaning.")
    elif float(np.var(y_pred_ret)) <= 0.0:
        data_integrity = "FAIL"
        issues.append("Direct predictions are constant after repair.")

    verdict = "PASS"
    if data_integrity == "FAIL" or direction_quality == "BAD" or direct_quality == "BAD":
        verdict = "FAIL"
    elif movement_quality == "BAD" or any("RMSE/MAE" in item or "MAPE" in item for item in issues):
        verdict = "WARNING"

    if verdict == "FAIL" and metrics["MAE"] >= baseline_metrics["MAE"] and direct_corr <= 0.0:
        assessment = "useless"
    elif verdict == "PASS":
        assessment = "promising"
    else:
        assessment = "acceptable"

    print(
        format_report(
            data_integrity=data_integrity,
            direction_quality=direction_quality,
            movement_quality=movement_quality,
            direct_quality=direct_quality,
            metrics=metrics,
            issues=issues,
            fixes=fixes,
            verdict=verdict,
            assessment=assessment,
        )
    )


if __name__ == "__main__":
    main()
