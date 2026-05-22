from __future__ import annotations

from datetime import datetime, timezone

from .constants import OPEN_INTEREST_INTERVALS, TIMEFRAME_ALIASES, TIMEFRAMES


def log(message: str) -> None:
    """Печатает короткое сообщение в консоль."""
    print(message, flush=True)


def normalize_timeframe(value: str) -> tuple[str, str, int]:
    """Нормализует таймфрейм для Bybit и имени таблицы."""
    key = value.strip().lower()
    key = TIMEFRAME_ALIASES.get(key, key)
    if key not in TIMEFRAMES:
        supported = ", ".join(sorted(TIMEFRAMES))
        raise ValueError(f"Unsupported timeframe '{value}'. Supported values: {supported}")
    bybit_interval, step_ms = TIMEFRAMES[key]
    return key, bybit_interval, step_ms


def parse_timestamp_to_ms(value: str) -> int:
    """Преобразует время в миллисекунды UTC."""
    value = value.strip()
    if not value:
        raise ValueError("Timestamp value cannot be empty")
    if value.isdigit():
        number = int(value)
        return number if number >= 1_000_000_000_000 else number * 1000
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return int(parsed.timestamp() * 1000)


def ms_to_datetime(value_ms: int) -> datetime:
    """Переводит миллисекунды в datetime UTC."""
    return datetime.fromtimestamp(value_ms / 1000, tz=timezone.utc)


def floor_to_step(value_ms: int, step_ms: int) -> int:
    """Округляет время вниз до границы свечи."""
    return (value_ms // step_ms) * step_ms


def ceil_to_step(value_ms: int, step_ms: int) -> int:
    """Округляет время вверх до ближайшей свечи."""
    return ((value_ms + step_ms - 1) // step_ms) * step_ms


def normalize_window(start_ms: int, end_ms: int, step_ms: int) -> tuple[int, int]:
    """Оставляет только закрытые свечи в заданном диапазоне."""
    if start_ms >= end_ms:
        raise ValueError("Start timestamp must be earlier than end timestamp")
    now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    end_ms = min(end_ms, now_ms - step_ms)
    start_ms = ceil_to_step(start_ms, step_ms)
    end_ms = floor_to_step(end_ms, step_ms)
    if start_ms > end_ms:
        raise RuntimeError("No closed candles in the requested window")
    return start_ms, end_ms


def make_table_name(symbol: str, timeframe: str) -> str:
    """Строит имя таблицы вида <symbol>_<timeframe>."""
    return f"{symbol.lower()}_{timeframe.lower()}"


def choose_open_interest_interval(step_ms: int) -> tuple[str, int]:
    """Выбирает ближайший интервал open interest."""
    selected = OPEN_INTEREST_INTERVALS[0]
    for label, interval_ms in OPEN_INTEREST_INTERVALS:
        if interval_ms <= step_ms:
            selected = (label, interval_ms)
    return selected
