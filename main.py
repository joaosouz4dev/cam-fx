"""Ponto de entrada do CamFX.

Uso:
    python main.py              abre a janela normalmente
    python main.py --minimized  inicia direto na bandeja (usado no autostart)
"""

# CRITICO: desabilitar os hardware transforms do MSMF ANTES de qualquer import
# do OpenCV. Sem isso, abrir a webcam por Media Foundation leva 11-28s nesta
# maquina; com isso, abre em ~1s. Precisa estar no ambiente antes do cv2 carregar.
import os

os.environ.setdefault("OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS", "0")

import sys
import traceback


def _crash_path():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "CamFX")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return os.path.join(d, "startup.log")


def _write_startup(msg):
    """Log de startup independente do camfx.log (para diagnosticar o .exe)."""
    try:
        import time
        with open(_crash_path(), "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:
        pass


def main():
    _write_startup(f"main() iniciando (frozen={getattr(sys, 'frozen', False)})")
    from camfx.single_instance import SingleInstance

    # Instancia unica: se ja ha um CamFX aberto, traz a janela existente.
    instance = SingleInstance()
    if not instance.acquire():
        _write_startup("outra instancia ja aberta; saindo")
        instance.signal_existing()
        return

    start_minimized = "--minimized" in sys.argv
    from camfx import webui
    _write_startup("chamando webui.run")
    # listen() sera ligado dentro do run apos a janela existir.
    webui.run(start_minimized=start_minimized, instance=instance)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        _write_startup("CRASH:\n" + traceback.format_exc())
        raise
