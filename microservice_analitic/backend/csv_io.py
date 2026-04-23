"""Единая точка работы с CSV-файлами для всего проекта.

Консолидирует разрозненные паттерны ``df.to_csv(path) + log + mkdir`` и
``pd.read_csv(path) + try/except`` из ``backend/model/report.py`` и
``frontend/pages/download_page.py`` (chunked byte-stream export).

Ответственность:
- Надёжная запись DataFrame в CSV (гарантированное создание родительских
  директорий, атомарная запись через временный файл, логирование).
- Безопасная загрузка CSV с ожидаемыми ошибками (отсутствие файла, битый
  формат) → возврат ``None`` вместо исключения.
- Потоковая сериализация DataFrame в bytes по чанкам — для экспорта в UI
  без скачка RAM до gigabytes.
- Единые значения по умолчанию: ``encoding='utf-8'``, ``sep=','``.

Для специфичного формата COPY FROM STDIN (TSV) в
``backend/dataset/database.py`` используется собственный писатель —
``csv_io`` не пытается покрыть этот горячий путь, чтобы не усложнять его.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Callable

import pandas as pd

_LOG = logging.getLogger(__name__)


# ── Запись ───────────────────────────────────────────────────────────────────

def save_csv(
    df: pd.DataFrame,
    path: Path | str,
    *,
    index: bool = False,
    encoding: str = "utf-8",
    sep: str = ",",
    make_parents: bool = True,
    atomic: bool = True,
) -> Path:
    """Сохраняет DataFrame в CSV.

    Параметры:
        df: Данные.
        path: Путь к файлу.
        index: Включать ли индекс pandas в CSV (обычно False).
        encoding: Кодировка (по умолчанию utf-8).
        sep: Разделитель (по умолчанию ',').
        make_parents: Создать родительские директории, если их нет.
        atomic: Если True — пишем во временный файл рядом и только потом
            переименовываем (os.replace). Защищает от полуписанного файла
            при падении процесса.

    Возвращает итоговый Path.
    """
    target = Path(path)
    if make_parents:
        target.parent.mkdir(parents=True, exist_ok=True)

    if atomic:
        # tempfile в той же директории — важно для os.replace на Windows/Linux
        fd, tmp_name = tempfile.mkstemp(
            prefix=target.name + ".", suffix=".tmp", dir=str(target.parent)
        )
        os.close(fd)
        tmp_path = Path(tmp_name)
        try:
            df.to_csv(tmp_path, index=index, encoding=encoding, sep=sep)
            os.replace(tmp_path, target)
        except Exception:
            # Чистим временный файл при ошибке
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
            raise
    else:
        df.to_csv(target, index=index, encoding=encoding, sep=sep)

    _LOG.info("csv saved: %s (rows=%d, cols=%d)", target, len(df), len(df.columns))
    return target


# ── Чтение ───────────────────────────────────────────────────────────────────

class CsvLoadError(Exception):
    """Ошибка чтения CSV, не связанная с «файла нет»."""


def load_csv(
    path: Path | str,
    *,
    encoding: str = "utf-8",
    sep: str = ",",
    required_columns: list[str] | None = None,
    missing_ok: bool = True,
) -> pd.DataFrame | None:
    """Безопасно загружает CSV в DataFrame.

    Возвращает ``None``, если файла нет и ``missing_ok=True``.
    Возбуждает ``CsvLoadError`` при битом формате или отсутствии обязательных
    колонок.

    Параметры:
        required_columns: Если задан — проверяет, что все эти имена есть в
            файле. Иначе — ``CsvLoadError``.
        missing_ok: Если True — отсутствие файла возвращает None (поведение
            legacy save/load_* в report.py). Если False — FileNotFoundError.
    """
    target = Path(path)
    if not target.exists():
        if missing_ok:
            return None
        raise FileNotFoundError(f"CSV not found: {target}")

    try:
        df = pd.read_csv(str(target), encoding=encoding, sep=sep)
    except (pd.errors.EmptyDataError, pd.errors.ParserError, UnicodeDecodeError) as exc:
        raise CsvLoadError(f"failed to parse CSV {target}: {exc}") from exc

    if required_columns:
        missing = [c for c in required_columns if c not in df.columns]
        if missing:
            raise CsvLoadError(
                f"CSV {target} missing required columns: {missing} "
                f"(available: {list(df.columns)})"
            )

    _LOG.debug("csv loaded: %s (rows=%d, cols=%d)", target, len(df), len(df.columns))
    return df


def load_csv_chunked(
    path: Path | str,
    *,
    chunksize: int = 100_000,
    encoding: str = "utf-8",
    sep: str = ",",
):
    """Итеративно читает CSV по чанкам для больших файлов.

    Использовать, когда full-load в память неприемлем. Возвращает pandas
    TextFileReader — итератор DataFrame-ов. Вызывающая сторона ответственна
    за обработку ошибок.
    """
    return pd.read_csv(str(path), encoding=encoding, sep=sep, chunksize=chunksize)


# ── Стриминг DataFrame → bytes (для Streamlit download_button) ───────────────

def stream_csv_bytes(
    df: pd.DataFrame,
    *,
    chunk_size: int = 50_000,
    encoding: str = "utf-8",
    sep: str = ",",
    on_progress: Callable[[int, int], None] | None = None,
) -> bytes:
    """Сериализует DataFrame в CSV-байты по чанкам.

    Пиковое потребление RAM ≈ один чанк (chunk_size × n_cols),
    а не весь DataFrame сразу. Для 3M строк разница — 50–100 MB vs 1.5–2 GB.

    Параметр ``on_progress(done, total)`` вызывается после каждого чанка;
    используется для обновления ``st.progress`` в UI.
    """
    n = len(df)
    if n == 0:
        return df.to_csv(index=False, encoding=encoding, sep=sep).encode(encoding)

    parts: list[bytes] = []
    # Заголовок: нулевой срез даёт только имена колонок
    parts.append(df.iloc[:0].to_csv(index=False, sep=sep).encode(encoding))
    for offset in range(0, n, chunk_size):
        parts.append(
            df.iloc[offset : offset + chunk_size]
            .to_csv(index=False, header=False, sep=sep)
            .encode(encoding)
        )
        if on_progress is not None:
            on_progress(min(offset + chunk_size, n), n)
    return b"".join(parts)


__all__ = [
    "CsvLoadError",
    "save_csv",
    "load_csv",
    "load_csv_chunked",
    "stream_csv_bytes",
]
