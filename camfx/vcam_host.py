"""Gerencia o processo do host da camera virtual Media Foundation.

O `camfx_vcam.exe` chama MFCreateVirtualCamera e mantem a camera "CamFX" viva
enquanto roda. O app inicia esse processo quando vai transmitir e o encerra
quando para, integrando com o modo sob demanda.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def host_exe_path() -> Path | None:
    """Localiza o camfx_vcam.exe (instalado em Program Files ou no projeto)."""
    candidates = [
        Path(r"C:\Program Files\CamFX\camfx_vcam.exe"),
    ]
    if hasattr(sys, "_MEIPASS"):
        candidates.append(Path(sys._MEIPASS) / "camfx_vcam.exe")  # type: ignore[attr-defined]
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).parent / "camfx_vcam.exe")
    here = Path(__file__).resolve().parent.parent
    candidates.append(here / "mfref" / "VCamSample" / "camfx_vcam.exe")
    for c in candidates:
        if c.exists():
            return c
    return None


class VCamHost:
    """Liga/desliga o host da camera virtual MF."""

    def __init__(self):
        self._proc: subprocess.Popen | None = None

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> bool:
        if self.running:
            return True
        exe = host_exe_path()
        if exe is None:
            return False
        # CREATE_NO_WINDOW: sem console; o processo so mantem a camera viva.
        self._proc = subprocess.Popen(
            [str(exe)],
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
        return True

    def stop(self) -> None:
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=3)
            except Exception:
                pass
            self._proc = None
        # Garante que nenhum host fique orfao (libera o icone da camera).
        try:
            subprocess.run(
                ["taskkill", "/F", "/IM", "camfx_vcam.exe"],
                creationflags=0x08000000,
                capture_output=True,
            )
        except Exception:
            pass
