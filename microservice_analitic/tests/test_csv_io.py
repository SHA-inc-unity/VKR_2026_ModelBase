"""Тесты для backend.csv_io: save/load/stream.

Покрывают:
- roundtrip save → load,
- атомарность save (нет полуписаных файлов при падении),
- missing_ok поведение load_csv,
- валидация required_columns,
- обработка битых файлов (CsvLoadError),
- stream_csv_bytes воспроизводит полный CSV + прогресс-колбэк.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from backend.csv_io import (
    CsvLoadError,
    load_csv,
    save_csv,
    stream_csv_bytes,
)


@pytest.fixture
def sample_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts":     [1, 2, 3, 4, 5],
            "price":  [100.0, 101.5, 99.75, 102.25, 98.0],
            "volume": [10, 20, 30, 40, 50],
        }
    )


# ── save_csv ─────────────────────────────────────────────────────────────────

def test_save_csv_roundtrip(tmp_path: Path, sample_df: pd.DataFrame) -> None:
    path = tmp_path / "a" / "b" / "data.csv"  # проверяем make_parents
    saved = save_csv(sample_df, path)
    assert saved == path
    assert path.exists()

    loaded = load_csv(path)
    assert loaded is not None
    pd.testing.assert_frame_equal(loaded, sample_df)


def test_save_csv_atomic_no_partial_file_on_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """При падении to_csv временный файл не должен оставаться, target не создаётся."""
    path = tmp_path / "data.csv"

    def boom(self, *args, **kwargs):  # noqa: ANN001
        raise RuntimeError("disk full")

    monkeypatch.setattr(pd.DataFrame, "to_csv", boom)
    with pytest.raises(RuntimeError, match="disk full"):
        save_csv(pd.DataFrame({"a": [1]}), path)

    assert not path.exists()
    # Никаких .tmp-файлов рядом
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []


def test_save_csv_non_atomic(tmp_path: Path, sample_df: pd.DataFrame) -> None:
    path = tmp_path / "plain.csv"
    save_csv(sample_df, path, atomic=False)
    assert path.exists()
    pd.testing.assert_frame_equal(load_csv(path), sample_df)


# ── load_csv ─────────────────────────────────────────────────────────────────

def test_load_csv_missing_returns_none_by_default(tmp_path: Path) -> None:
    assert load_csv(tmp_path / "nope.csv") is None


def test_load_csv_missing_raises_when_not_ok(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_csv(tmp_path / "nope.csv", missing_ok=False)


def test_load_csv_required_columns_missing(
    tmp_path: Path, sample_df: pd.DataFrame
) -> None:
    path = tmp_path / "data.csv"
    save_csv(sample_df, path)
    with pytest.raises(CsvLoadError, match="missing required columns"):
        load_csv(path, required_columns=["ts", "non_existent"])


def test_load_csv_required_columns_present(
    tmp_path: Path, sample_df: pd.DataFrame
) -> None:
    path = tmp_path / "data.csv"
    save_csv(sample_df, path)
    df = load_csv(path, required_columns=["ts", "price"])
    assert df is not None and len(df) == 5


def test_load_csv_empty_file_raises(tmp_path: Path) -> None:
    path = tmp_path / "empty.csv"
    path.write_text("", encoding="utf-8")
    with pytest.raises(CsvLoadError):
        load_csv(path)


# ── stream_csv_bytes ─────────────────────────────────────────────────────────

def test_stream_csv_bytes_matches_full_to_csv(sample_df: pd.DataFrame) -> None:
    expected = sample_df.to_csv(index=False).encode("utf-8")
    got = stream_csv_bytes(sample_df, chunk_size=2)
    assert got == expected


def test_stream_csv_bytes_progress_callback(sample_df: pd.DataFrame) -> None:
    progress: list[tuple[int, int]] = []
    stream_csv_bytes(
        sample_df,
        chunk_size=2,
        on_progress=lambda d, t: progress.append((d, t)),
    )
    # 5 строк, чанк=2 → [2,4,5] done, total=5
    assert progress[-1] == (5, 5)
    assert all(t == 5 for _, t in progress)
    # Прогресс монотонно не убывает
    dones = [d for d, _ in progress]
    assert dones == sorted(dones)


def test_stream_csv_bytes_empty_df() -> None:
    df = pd.DataFrame({"a": pd.Series(dtype="int64"), "b": pd.Series(dtype="float64")})
    got = stream_csv_bytes(df)
    # Только заголовок
    assert got.decode("utf-8").strip() == "a,b"
