"""Gerencia o processo do host da camera virtual Media Foundation.

O `camfx_vcam.exe` chama MFCreateVirtualCamera e mantem a camera "CamFX" viva
enquanto roda. O app inicia esse processo quando vai transmitir e o encerra
quando para, integrando com o modo sob demanda.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path


def host_exe_candidates() -> list[Path]:
    """Caminhos possiveis do camfx_vcam.exe, do mais preferido ao fallback.

    Preferimos o helper ao lado do app/dist (mesma versao do build) antes do
    instalador em Program Files: em dev o copy local funciona mesmo quando a
    politica do Windows bloqueia o binario instalado globalmente.
    """
    here = Path(__file__).resolve().parent.parent
    candidates: list[Path] = []

    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / "camfx_vcam.exe")
    else:
        candidates.append(here / "dist" / "CamFX" / "camfx_vcam.exe")
        candidates.append(here / "mfref" / "VCamSample" / "camfx_vcam.exe")

    candidates.append(Path(r"C:\Program Files\CamFX\camfx_vcam.exe"))

    if hasattr(sys, "_MEIPASS"):
        candidates.append(Path(sys._MEIPASS) / "camfx_vcam.exe")  # type: ignore[attr-defined]

    # Dedup preservando ordem.
    seen: set[str] = set()
    out: list[Path] = []
    for c in candidates:
        key = str(c.resolve()) if c.exists() else str(c)
        if key in seen:
            continue
        seen.add(key)
        if c.exists():
            out.append(c)
    return out


def host_exe_path() -> Path | None:
    """Primeiro candidato existente (compatibilidade)."""
    cands = host_exe_candidates()
    return cands[0] if cands else None


class VCamHost:
    """Liga/desliga o host da camera virtual MF."""

    def __init__(self):
        self._proc: subprocess.Popen | None = None
        self._exe: Path | None = None
        self.last_error: str | None = None

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> bool:
        if self.running:
            return True
        self.last_error = None
        candidates = host_exe_candidates()
        if not candidates:
            self.last_error = "camfx_vcam.exe nao encontrado"
            return False

        errors: list[str] = []
        for exe in candidates:
            try:
                proc = subprocess.Popen(
                    [str(exe)],
                    creationflags=0x08000000,  # CREATE_NO_WINDOW
                )
            except OSError as exc:
                errors.append(f"{exe}: {exc}")
                continue
            time.sleep(0.4)
            if proc.poll() is not None:
                errors.append(f"{exe}: encerrou cedo (codigo {proc.returncode})")
                continue
            self._proc = proc
            self._exe = exe
            return True

        self.last_error = "; ".join(errors) or "falha ao iniciar camfx_vcam.exe"
        return False

    def stop(self) -> None:
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=3)
            except Exception:
                pass
            self._proc = None
            self._exe = None
        # Garante que nenhum host fique orfao (libera o icone da camera).
        try:
            subprocess.run(
                ["taskkill", "/F", "/IM", "camfx_vcam.exe"],
                creationflags=0x08000000,
                capture_output=True,
            )
        except Exception:
            pass
