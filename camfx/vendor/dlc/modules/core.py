"""Stub de modules.core do Deep-Live-Cam (vendorizado no CamFX).

O core.py original puxa a UI Tkinter (customtkinter) e tensorflow, que nao
usamos. O face_swapper so precisa de update_status (log). Este stub evita a
cadeia pesada de imports.
"""

from __future__ import annotations


def update_status(message: str, scope: str = "DLC") -> None:
    try:
        from ....log import log  # camfx.log
        log(f"[{scope}] {message}")
    except Exception:
        pass
