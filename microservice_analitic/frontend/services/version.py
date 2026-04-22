"""Утилита для получения версии из Git.

Возвращает порядковый номер коммита (счётчик с начала истории)
и короткий хеш HEAD — без тегов.

Пример::

    from services.version import get_version
    ver = get_version()
    print(ver["display"])  # "v29 · a4f09a8"
"""
from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path

# Корень репозитория: …/ModelLine (три уровня выше этого файла)
_REPO_ROOT = Path(__file__).resolve().parents[3]


@lru_cache(maxsize=1)
def get_version() -> dict[str, object]:
    """Возвращает информацию о версии.

    Ключи:
        number  (int)  — счётчик коммитов (``git rev-list --count HEAD``).
        sha     (str)  — короткий хеш (``git rev-parse --short HEAD``).
        display (str)  — готовая строка "vN · <hash>" для отображения в UI.

    При недоступности git возвращает ``number=0, sha="unknown"``.
    """
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
    except Exception:
        number = 0
        sha = "unknown"
    return {
        "number": number,
        "sha": sha,
        "display": f"v{number} · {sha}",
    }
