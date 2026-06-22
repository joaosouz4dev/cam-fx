"""Log simples em arquivo, util para diagnosticar o .exe (que roda sem console)."""

from __future__ import annotations

import time
from pathlib import Path

from .config import config_dir

_LOG_PATH = config_dir() / "camfx.log"


def log(msg: str) -> None:
    try:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{ts} {msg}\n")
    except Exception:
        pass


def log_path() -> Path:
    return _LOG_PATH
