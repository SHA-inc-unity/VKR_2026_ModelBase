from __future__ import annotations

BYBIT_BASE_URL = "https://api.bybit.com"
DB_HOST = "localhost"
DB_PORT = 5432
DB_NAME = "crypt_date"
REQUEST_TIMEOUT_SECONDS = 20
MAX_RETRIES = 4
PAGE_LIMIT_KLINE = 1000          # Bybit API hard maximum per request
PAGE_LIMIT_FUNDING = 200
PAGE_LIMIT_OPEN_INTEREST = 200
UPSERT_BATCH_SIZE = 50000        # rows per staging-table batch (raised for large datasets)
MAX_PARALLEL_API_WORKERS = 20   # concurrent Bybit API page requests (well within 120 req/s IP limit)

TIMEFRAMES = {
    "1m": ("1", 60_000),
    "3m": ("3", 180_000),
    "5m": ("5", 300_000),
    "15m": ("15", 900_000),
    "30m": ("30", 1_800_000),
    "60m": ("60", 3_600_000),
    "120m": ("120", 7_200_000),
    "240m": ("240", 14_400_000),
    "360m": ("360", 21_600_000),
    "720m": ("720", 43_200_000),
    "1d": ("D", 86_400_000),
}

# Maps Bybit interval string → step size in milliseconds (for parallel page-window computation)
INTERVAL_TO_STEP_MS: dict[str, int] = {
    bybit_interval: step_ms
    for _, (bybit_interval, step_ms) in TIMEFRAMES.items()
}

TIMEFRAME_ALIASES = {
    "1": "1m",
    "3": "3m",
    "5": "5m",
    "15": "15m",
    "30": "30m",
    "60": "60m",
    "1h": "60m",
    "120": "120m",
    "2h": "120m",
    "240": "240m",
    "4h": "240m",
    "360": "360m",
    "6h": "360m",
    "720": "720m",
    "12h": "720m",
    "d": "1d",
}

OPEN_INTEREST_INTERVALS = [
    ("5min", 300_000),
    ("15min", 900_000),
    ("30min", 1_800_000),
    ("1h", 3_600_000),
    ("4h", 14_400_000),
    ("1d", 86_400_000),
]

RAW_TABLE_SCHEMA = [
    ("timestamp_utc", "timestamp with time zone"),
    ("symbol", "character varying"),
    ("exchange", "character varying"),
    ("timeframe", "character varying"),
    ("index_price", "numeric"),
    ("open_price",  "numeric"),
    ("high_price",  "numeric"),
    ("low_price",   "numeric"),
    ("volume",      "numeric"),
    ("turnover",    "numeric"),
    ("funding_rate", "numeric"),
    ("open_interest", "numeric"),
    ("rsi", "numeric"),
]

ROLLING_WINDOWS: tuple[int, ...] = (6, 24)
RETURN_HORIZONS: tuple[int, ...] = (1, 6, 24)
RSI_LAG_STEPS: tuple[int, ...] = (1, 2)
DEFAULT_WARMUP_CANDLES = 24

# Горизонт прогноза цели: предсказываем изменение цены через 3 часа
# вне зависимости от таймфрейма.
TARGET_HORIZON_MS: int = 3 * 3_600_000  # 3 часа в миллисекундах

# Одобренный набор 27 feature-колонок (double precision, nullable).
# Вычисляются SQL window-функциями на стороне PostgreSQL после ingest
# (см. DatasetRepository.ComputeAndUpdateFeaturesAsync). target_return_1
# в схему БД НЕ входит — это целевая переменная, вычисляется только в
# пайплайне обучения.
FEATURE_TABLE_SCHEMA = [
    *((f"return_{h}", "double precision") for h in RETURN_HORIZONS),
    *((f"log_return_{h}", "double precision") for h in RETURN_HORIZONS),
    *(
        (f"price_roll{w}_{stat}", "double precision")
        for w in ROLLING_WINDOWS
        for stat in ("mean", "std", "min", "max")
    ),
    *((f"price_to_roll{w}_mean", "double precision") for w in ROLLING_WINDOWS),
    *((f"price_vol_{w}", "double precision") for w in ROLLING_WINDOWS),
    *((f"oi_roll{w}_mean", "double precision") for w in ROLLING_WINDOWS),
    ("oi_return_1", "double precision"),
    *((f"rsi_lag_{lag}", "double precision") for lag in RSI_LAG_STEPS),
    ("hour_sin", "double precision"),
    ("hour_cos", "double precision"),
    ("dow_sin", "double precision"),
    ("dow_cos", "double precision"),
    # OHLCV-derived features
    *((f"atr_{w}", "double precision") for w in ROLLING_WINDOWS),
    ("candle_body",         "double precision"),
    ("upper_wick",          "double precision"),
    ("lower_wick",          "double precision"),
    *((f"volume_roll{w}_mean",    "double precision") for w in ROLLING_WINDOWS),
    *((f"volume_to_roll{w}_mean", "double precision") for w in ROLLING_WINDOWS),
    ("volume_return_1",     "double precision"),
    ("rsi_slope",           "double precision"),
]

EXPECTED_TABLE_SCHEMA = RAW_TABLE_SCHEMA + FEATURE_TABLE_SCHEMA
DATASET_COLUMN_NAMES = [column_name for column_name, _ in EXPECTED_TABLE_SCHEMA]
TEXT_DATASET_COLUMNS = {"symbol", "exchange", "timeframe"}
REQUIRED_NOT_NULL_COLUMNS = {"timestamp_utc", "symbol", "exchange", "timeframe", "index_price"}
FORBIDDEN_TABLE_COLUMNS = {"bid1_price", "ask1_price", "bid1_size", "ask1_size"}
RAW_FEATURE_COLUMNS: frozenset[str] = frozenset(
    {
        "timestamp_utc",
        "symbol",
        "exchange",
        "timeframe",
        "index_price",
        "open_price",
        "high_price",
        "low_price",
        "volume",
        "turnover",
        "funding_rate",
        "open_interest",
        "rsi",
        "target_return_1",
    }
)
