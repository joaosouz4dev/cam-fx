"""Localiza os assets de marca (logo/icone), no fonte e no .exe empacotado."""

from __future__ import annotations

import sys
from pathlib import Path


def _asset(name: str) -> Path | None:
    candidates = []
    if hasattr(sys, "_MEIPASS"):
        candidates.append(Path(sys._MEIPASS) / "assets" / name)  # type: ignore[attr-defined]
    here = Path(__file__).resolve().parent.parent
    candidates.append(here / "assets" / name)
    for c in candidates:
        if c.exists():
            return c
    return None


def icon_path() -> Path | None:
    """Caminho do icon.ico (janela do app / instalador)."""
    return _asset("icon.ico")


def logo_path() -> Path | None:
    """Caminho do logo.png (bandeja, etc.)."""
    return _asset("logo.png")
