"""Inicio automatico no Windows (registro HKCU\\...\\Run).

Quando ligado, o CamFX abre junto com o Windows ja minimizado na bandeja, o
oposto do comportamento do NVIDIA Broadcast que abria a janela maximizada.
"""

from __future__ import annotations

import sys

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = "CamFX"


def _launch_command() -> str:
    if getattr(sys, "frozen", False):
        # Executavel gerado pelo PyInstaller.
        return f'"{sys.executable}" --minimized'
    # Rodando do fonte: reusa o mesmo interpretador e o main.py.
    return f'"{sys.executable}" "{sys.argv[0]}" --minimized'


def is_enabled() -> bool:
    try:
        import winreg
    except ImportError:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.QueryValueEx(key, _VALUE_NAME)
            return True
    except OSError:
        return False


def set_enabled(enabled: bool) -> None:
    try:
        import winreg
    except ImportError:
        return
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE
    ) as key:
        if enabled:
            winreg.SetValueEx(key, _VALUE_NAME, 0, winreg.REG_SZ, _launch_command())
        else:
            try:
                winreg.DeleteValue(key, _VALUE_NAME)
            except OSError:
                pass
