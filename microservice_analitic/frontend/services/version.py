"""Утилита для получения версии из Git.

Возвращает порядковый номер коммита, короткий хеш HEAD и дату последнего коммита.

Пример::

    from services.version import get_version
    ver = get_version()
    print(ver["display"])  # "v30 · 4e698f1 · 2026-04-22"

Стратегия получения версии (в порядке приоритета):
  1. subprocess git  — точный результат; при успехе сохраняется в кеш-файл.
  2. Кеш-файл        — используется в Docker-среде без git CLI.
  3. Прямое чтение .git-файлов — минимальная информация (SHA + дата) без подсчёта коммитов.
"""
from __future__ import annotations

import json
import subprocess
from functools import lru_cache
from pathlib import Path

# Корень репозитория: …/ModelLine (три уровня выше этого файла)
_REPO_ROOT = Path(__file__).resolve().parents[3]
# Кеш-файл рядом с version.py — пишется при успешном вызове git, читается как запасной вариант
_CACHE_PATH = Path(__file__).resolve().parent / "_git_version_cache.json"


def _write_cache(data: dict) -> None:
    try:
        _CACHE_PATH.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _read_cache() -> dict | None:
    try:
        return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_git_files() -> dict | None:
    """Минимальный fallback: читает .git-файлы напрямую без subprocess.

    Возвращает SHA + дату из последней строки .git/logs/HEAD.
    Счётчик коммитов недоступен без git CLI — возвращает None.
    """
    try:
        git_dir = _REPO_ROOT / ".git"
        if not git_dir.is_dir():
            return None

        # Определяем текущий SHA через HEAD → ref → packed-refs / loose ref
        head_text = (git_dir / "HEAD").read_text(encoding="utf-8", errors="replace").strip()
        if head_text.startswith("ref: "):
            ref = head_text[5:]  # e.g. refs/heads/main
            ref_file = git_dir / ref
            if ref_file.exists():
                sha = ref_file.read_text(encoding="utf-8").strip()[:7]
            else:
                # Попробуем packed-refs
                packed_file = git_dir / "packed-refs"
                sha = None
                if packed_file.exists():
                    for line in packed_file.read_text(encoding="utf-8").splitlines():
                        if line.startswith("#"):
                            continue
                        parts = line.split()
                        if len(parts) >= 2 and parts[1] == ref:
                            sha = parts[0][:7]
                            break
                if sha is None:
                    return None
        else:
            sha = head_text[:7]

        # Дата из последней строки .git/logs/HEAD
        # Формат строки: <old> <new> Name <email> <unix_ts> <tz>\t<msg>
        date_str = "unknown"
        log_file = git_dir / "logs" / "HEAD"
        if log_file.exists():
            lines = log_file.read_text(encoding="utf-8", errors="replace").strip().splitlines()
            if lines:
                parts = lines[-1].split()
                # Ищем поле после <email> (содержит угловые скобки)
                for i, p in enumerate(parts):
                    if p.startswith("<") and p.endswith(">") and i + 1 < len(parts):
                        try:
                            from datetime import datetime, timezone
                            ts = int(parts[i + 1])
                            date_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
                        except (ValueError, IndexError):
                            pass
                        break

        return {"number": None, "sha": sha, "date": date_str}
    except Exception:
        return None


@lru_cache(maxsize=1)
def get_version() -> dict[str, object]:
    """Возвращает информацию о версии.

    Ключи:
        number  (int | None) — счётчик коммитов от начала истории.
        sha     (str)        — короткий хеш HEAD.
        date    (str)        — дата последнего коммита ``YYYY-MM-DD``.
        display (str)        — готовая строка для UI, напр. ``v30 · 4e698f1 · 2026-04-22``.
    """
    # 1. Попытка через git subprocess ─────────────────────────────────────
    try:
        number = int(
            subprocess.check_output(
                ["git", "rev-list", "--count", "HEAD"],
                cwd=str(_REPO_ROOT),
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        )
        sha = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(_REPO_ROOT),
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        date_str = subprocess.check_output(
            ["git", "log", "-1", "--format=%cd", "--date=short"],
            cwd=str(_REPO_ROOT),
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        result = {"number": number, "sha": sha, "date": date_str}
        _write_cache(result)  # обновляем кеш для Docker-окружений без git
        result["display"] = f"v{number} · {sha} · {date_str}"
        return result
    except Exception:
        pass

    # 2. Кеш-файл (написан при предыдущем успешном запуске с git) ─────────
    cached = _read_cache()
    if cached and cached.get("sha") and cached.get("sha") != "unknown":
        n = cached.get("number")
        cached.setdefault("date", "unknown")
        cached["display"] = (
            f"v{n} · {cached['sha']} · {cached['date']}" if n else
            f"{cached['sha']} · {cached['date']}"
        )
        return cached

    # 3. Прямое чтение .git-файлов ─────────────────────────────────────────
    git_info = _read_git_files()
    if git_info:
        sha = git_info["sha"]
        date_str = git_info["date"]
        return {"number": None, "sha": sha, "date": date_str, "display": f"{sha} · {date_str}"}

    # 4. Полный fallback ────────────────────────────────────────────────────
    return {"number": 0, "sha": "unknown", "date": "unknown", "display": "v? · unknown"}
