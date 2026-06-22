"""Registro do driver de camera virtual CamFX (CamFXSource.dll).

O driver e um filtro DirectShow que precisa ser registrado no Windows uma vez
(regsvr32, com privilegio de administrador). Localiza o DLL embutido no .exe
(PyInstaller) ou ao lado do codigo, e registra/desregistra com elevacao.
"""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path


def driver_dll_path() -> Path | None:
    """Caminho do CamFXSource.dll, embutido ou no diretorio do projeto."""
    candidates = []
    if hasattr(sys, "_MEIPASS"):
        candidates.append(Path(sys._MEIPASS) / "CamFXSource.dll")  # type: ignore[attr-defined]
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).parent / "CamFXSource.dll")
    here = Path(__file__).resolve().parent.parent
    candidates.append(here / "driver" / "CamFXSource.dll")
    candidates.append(here / "CamFXSource.dll")
    for c in candidates:
        if c.exists():
            return c
    return None


def is_registered() -> bool:
    try:
        from pygrabber.dshow_graph import FilterGraph

        return "CamFX" in FilterGraph().get_input_devices()
    except Exception:
        return False


def _run_regsvr32(dll: Path, unregister: bool = False) -> int:
    """Executa regsvr32 com elevacao (UAC). Retorna o codigo de saida."""
    params = f'/s {"/u " if unregister else ""}"{dll}"'
    # ShellExecuteEx com 'runas' para elevar.
    rc = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", "regsvr32.exe", params, None, 0
    )
    # ShellExecuteW retorna >32 em sucesso ao LANCAR (nao o exit do processo).
    return 0 if rc > 32 else int(rc)


def register(unregister: bool = False) -> tuple[bool, str]:
    """Registra (ou remove) o driver. Retorna (ok, mensagem)."""
    dll = driver_dll_path()
    if dll is None:
        return False, "CamFXSource.dll nao encontrado no pacote."
    rc = _run_regsvr32(dll, unregister=unregister)
    if rc != 0:
        return False, "O usuario cancelou a elevacao ou o registro falhou."
    # Confirma de fato.
    import time

    for _ in range(10):
        time.sleep(0.3)
        ok = is_registered()
        if ok != unregister:  # registrou (ok=True) ou removeu (ok=False)
            return True, "Driver registrado." if not unregister else "Driver removido."
    return False, "Registro executado, mas a CamFX nao apareceu na lista."
