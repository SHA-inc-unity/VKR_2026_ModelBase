#!/usr/bin/env python3
"""CLI для обучения CatBoost-модели на датасете из PostgreSQL.

Примеры использования:
    python train_catboost.py --symbol BTCUSDT --timeframe 60m \\
        --postgres-user postgres --postgres-password postgres

    # Без GPU (CPU fallback):
    python train_catboost.py --symbol BTCUSDT --timeframe 1d --no-gpu \\
        --postgres-user postgres --postgres-password postgres

    # Через переменные окружения:
    export PGUSER=postgres PGPASSWORD=postgres
    python train_catboost.py --symbol ETHUSDT --timeframe 240m
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Корень воркспейса в sys.path для импортов backend.*
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import psycopg2

from backend.dataset.constants import DB_HOST, DB_NAME, DB_PORT
from backend.dataset.core import log, make_table_name, normalize_timeframe
from backend.model import (
    grid_search_cv,
    load_training_data,
    plot_actual_vs_predicted,
    plot_feature_importance,
    save_grid_results,
    save_model,
    train_final_model,
    walk_forward_split,
)
from backend.model.report import print_summary

# Секунд в году (для аннуализации Sharpe)
_SECONDS_PER_YEAR: float = 365.25 * 24 * 3600


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Обучение CatBoost-модели прогнозирования target_return_1.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--symbol",    default="BTCUSDT", help="Торговый символ (e.g. BTCUSDT)")
    parser.add_argument("--timeframe", default="60m",     help="Таймфрейм (e.g. 60m, 1d)")
    parser.add_argument("--postgres-user",     default=os.getenv("PGUSER"),     metavar="USER")
    parser.add_argument("--postgres-password", default=os.getenv("PGPASSWORD"), metavar="PASS")
    parser.add_argument("--postgres-host",     default=os.getenv("PGHOST", DB_HOST))
    parser.add_argument("--postgres-port",     default=int(os.getenv("PGPORT", str(DB_PORT))), type=int)
    parser.add_argument("--postgres-db",       default=os.getenv("PGDATABASE", DB_NAME))
    parser.add_argument(
        "--no-gpu",
        action="store_true",
        help="Отключить GPU (task_type=CPU). Использовать если CUDA недоступна.",
    )
    parser.add_argument(
        "--skip-grid",
        action="store_true",
        help="Пропустить grid search, обучить сразу с первым набором параметров.",
    )
    return parser


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if not args.postgres_user:
        parser.error("--postgres-user обязателен если PGUSER не задан")
    if args.postgres_password is None:
        parser.error("--postgres-password обязателен если PGPASSWORD не задан")

    use_gpu = not args.no_gpu
    symbol = args.symbol.upper().strip()
    timeframe, _, step_ms = normalize_timeframe(args.timeframe)
    table_name = make_table_name(symbol, timeframe)

    # Фактор аннуализации для Sharpe: кол-во баров в году
    annualize_factor = _SECONDS_PER_YEAR * 1000.0 / step_ms

    log(f"[main] Символ={symbol}  Таймфрейм={timeframe}  Таблица={table_name}  "
        f"GPU={'да' if use_gpu else 'нет'}")
    log(f"[main] Annualize factor (баров/год): {annualize_factor:.0f}")

    # --- Подключение к БД ---
    try:
        connection = psycopg2.connect(
            host=args.postgres_host,
            port=args.postgres_port,
            dbname=args.postgres_db,
            user=args.postgres_user,
            password=args.postgres_password,
        )
    except Exception as exc:
        log(f"[main] Ошибка подключения к PostgreSQL: {exc}")
        return 1

    try:
        # --- Загрузка данных ---
        X, y, feature_cols, timestamps = load_training_data(connection, table_name)
    finally:
        connection.close()

    # --- Walk-forward split (70% train / 30% test по времени) ---
    train_size, test_size = walk_forward_split(len(X))
    X_train, X_test = X.iloc[:train_size], X.iloc[train_size:]
    y_train, y_test = y.iloc[:train_size], y.iloc[train_size:]
    ts_test = timestamps.iloc[train_size:]

    log(f"[main] Train: {train_size} строк | Test: {test_size} строк")

    # --- Grid Search (или первый набор параметров) ---
    prefix = f"catboost_{symbol.lower()}_{timeframe.lower()}"

    if args.skip_grid:
        from backend.model.config import PARAM_GRID
        best_params = PARAM_GRID[0].copy()
        grid_df = None
        log("[main] Grid search пропущен (--skip-grid), используются первые параметры")
    else:
        best_params, grid_df = grid_search_cv(X_train, y_train, use_gpu=use_gpu)
        save_grid_results(grid_df, prefix=prefix)

    # --- Финальное обучение ---
    model, metrics, y_pred = train_final_model(
        X_train, y_train,
        X_test, y_test,
        best_params,
        annualize_factor=annualize_factor,
        use_gpu=use_gpu,
    )

    # --- Сохранение модели ---
    model_path = save_model(model, symbol, timeframe)

    # --- Отчёты ---
    plot_feature_importance(model, feature_cols, prefix=prefix)
    plot_actual_vs_predicted(y_test, y_pred, ts_test, prefix=prefix)

    # --- Итоговая сводка ---
    print_summary(metrics, best_params, model_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
