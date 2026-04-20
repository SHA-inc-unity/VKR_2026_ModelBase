"""Точка входа: ``python -m backend.api.run``.

Эквивалент:
    uvicorn backend.api.app:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import logging
import os

_LOG = logging.getLogger(__name__)


def main() -> None:
    try:
        import uvicorn
    except ImportError:
        _LOG.error("uvicorn не установлен. Запустите: pip install uvicorn[standard]")
        raise SystemExit(1)

    host = os.getenv("API_HOST", "0.0.0.0")
    port = int(os.getenv("API_PORT", "8000"))
    reload = os.getenv("API_RELOAD", "false").lower() == "true"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    )

    _LOG.info("Запуск ModelLine API на %s:%d  reload=%s", host, port, reload)
    uvicorn.run(
        "backend.api.app:app",
        host=host,
        port=port,
        reload=reload,
    )


if __name__ == "__main__":
    main()
