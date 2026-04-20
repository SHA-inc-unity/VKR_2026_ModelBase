"""Конфигурация обучения CatBoost: гиперпараметры, пути, константы."""
from __future__ import annotations

import itertools
import random
from pathlib import Path

# ---------------------------------------------------------------------------
# Пути
# ---------------------------------------------------------------------------
MODELS_DIR: Path = Path(__file__).resolve().parents[2] / "models"

# ---------------------------------------------------------------------------
# Параметры обучения
# ---------------------------------------------------------------------------
RANDOM_SEED: int = 42
EARLY_STOPPING_ROUNDS: int = 100         # для CV-фолдов (grid search): быстрая остановка
FINAL_EARLY_STOPPING_ROUNDS: int = 500   # для финального обучения: дать больше времени сойтись
TRAIN_FRACTION: float = 0.70   # первые 70% по времени → train; последние 30% → test
CV_SPLITS: int = 5             # TimeSeriesSplit folds для grid search

# ---------------------------------------------------------------------------
# GPU / Device
# ---------------------------------------------------------------------------
TASK_TYPE: str = "GPU"
DEVICES: str = "0"
GPU_RAM_PART: float = 0.85     # доля видеопамяти для CatBoost GPU
VERBOSE_TRAIN: int = 100       # verbose для финального обучения

# ---------------------------------------------------------------------------
# Target и исключения из матрицы X
# ---------------------------------------------------------------------------
TARGET_COLUMN: str = "target_return_1"
TARGET_COLUMN_PREFIX: str = "target_"   # префикс, по которому UI/loader ищут кандидатов
# Мета-колонки, которые никогда не попадают в X (без учёта target_*).
# Фактический target и все прочие колонки с префиксом TARGET_COLUMN_PREFIX
# исключаются в loader.py (anti-leakage), поэтому здесь их перечислять не нужно.
META_COLUMNS: frozenset[str] = frozenset(
    {"timestamp_utc", "symbol", "exchange", "timeframe"}
)
# Алиас для обратной совместимости (часть кода могла импортировать
# EXCLUDE_FROM_FEATURES напрямую).
EXCLUDE_FROM_FEATURES: frozenset[str] = META_COLUMNS | {TARGET_COLUMN}

# ---------------------------------------------------------------------------
# Полная решётка гиперпараметров (243 базовых × 2 border_count = 486 max)
# ---------------------------------------------------------------------------
# iterations × depth × lr × l2 × bag × border_count;
# при запуске с лимитом пользователь сам указывает сколько прогнать.
# Диапазон iterations расширен до 3000–10000 для поиска оптимального числа деревьев;
# early_stopping гарантирует остановку до фактического предела.
PARAM_GRID: list[dict] = [
    # iterations  depth  lr      l2  bagging_temp  border_count
    {"iterations": 2000,  "depth": 6, "learning_rate": 0.05,  "l2_leaf_reg": 3, "bagging_temperature": 0.5,  "border_count": 128},
    {"iterations": 2000,  "depth": 6, "learning_rate": 0.03,  "l2_leaf_reg": 5, "bagging_temperature": 1.0,  "border_count": 254},
    {"iterations": 2000,  "depth": 8, "learning_rate": 0.01,  "l2_leaf_reg": 3, "bagging_temperature": 1.0,  "border_count": 128},
    {"iterations": 2000,  "depth": 8, "learning_rate": 0.05,  "l2_leaf_reg": 7, "bagging_temperature": 1.5,  "border_count": 254},
    {"iterations": 3000,  "depth": 6, "learning_rate": 0.03,  "l2_leaf_reg": 3, "bagging_temperature": 1.0,  "border_count": 254},
    {"iterations": 3000,  "depth": 8, "learning_rate": 0.01,  "l2_leaf_reg": 5, "bagging_temperature": 0.5,  "border_count": 128},
    {"iterations": 5000,  "depth": 6, "learning_rate": 0.01,  "l2_leaf_reg": 3, "bagging_temperature": 1.0,  "border_count": 254},
    {"iterations": 5000,  "depth": 6, "learning_rate": 0.03,  "l2_leaf_reg": 5, "bagging_temperature": 0.5,  "border_count": 128},
    {"iterations": 5000,  "depth": 8, "learning_rate": 0.01,  "l2_leaf_reg": 7, "bagging_temperature": 1.5,  "border_count": 254},
    {"iterations": 5000,  "depth": 10,"learning_rate": 0.01,  "l2_leaf_reg": 3, "bagging_temperature": 1.0,  "border_count": 128},
    {"iterations": 10000, "depth": 6, "learning_rate": 0.01,  "l2_leaf_reg": 5, "bagging_temperature": 1.0,  "border_count": 254},
    {"iterations": 10000, "depth": 8, "learning_rate": 0.01,  "l2_leaf_reg": 7, "bagging_temperature": 0.5,  "border_count": 128},
]

# ---------------------------------------------------------------------------
# Компактный формат: отдельные значения на каждый параметр (для UI)
# ---------------------------------------------------------------------------

# Значения по умолчанию для UI в режиме «значения → авто-комбинации»
DEFAULT_PARAM_VALUES: dict[str, list] = {
    "iterations":          [2000, 3000, 5000, 10000],
    "depth":               [6, 8, 10],
    "learning_rate":       [0.01, 0.03, 0.05],
    "l2_leaf_reg":         [3, 5, 7],
    "bagging_temperature": [0.5, 1.0, 1.5],
    "border_count":        [128, 254],
}

# Типы для парсинга строковых значений из UI
_PARAM_TYPES: dict[str, type] = {
    "iterations":          int,
    "depth":               int,
    "learning_rate":       float,
    "l2_leaf_reg":         float,
    "bagging_temperature": float,
    "border_count":        int,
}


# ---------------------------------------------------------------------------
# Временно́й фрейм → миллисекунды → коэффициент аннуализации
# ---------------------------------------------------------------------------

_TIMEFRAME_MS: dict[str, int] = {
    "1m":  60_000,
    "3m":  180_000,
    "5m":  300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "60m": 3_600_000,
    "1h":  3_600_000,
    "4h":  14_400_000,
    "1d":  86_400_000,
}


def timeframe_to_ms(timeframe: str) -> int:
    """Возвращает длительность одного бара в миллисекундах."""
    return _TIMEFRAME_MS.get(timeframe.lower(), 3_600_000)


def annualize_factor(timeframe: str) -> float:
    """Возвращает число баров в году для аннуализации Sharpe ratio."""
    ms = timeframe_to_ms(timeframe)
    return 365 * 24 * 3_600_000 / ms


def expand_param_grid(
    param_values: dict[str, list],
    max_combos: int | None = None,
    seed: int = RANDOM_SEED,
) -> list[dict]:
    """Раскрывает словарь {param: [v1, v2, ...]} в список комбинаций (декартово произведение).

    Если max_combos задан и число всех комбинаций превышает его — делает случайную выборку
    без повторений (с фиксированным seed для воспроизводимости).
    """
    keys = list(param_values.keys())
    all_combos = [
        dict(zip(keys, combo))
        for combo in itertools.product(*[param_values[k] for k in keys])
    ]
    if max_combos is not None and len(all_combos) > max_combos:
        rng = random.Random(seed)
        all_combos = rng.sample(all_combos, max_combos)
    return all_combos
