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

# CRITICO: poe as DLLs do CUDA (cuDNN/cuBLAS) no PATH ANTES de qualquer import
# de onnxruntime/insightface. No .exe elas ficam em _internal/nvidia/*/bin e o
# onnxruntime nao as acha sozinho -> CUDA falha com "cudnn64_9.dll missing" e o
# face swap trava. Chamar aqui garante o PATH desde o inicio.
try:
    from camfx.models import enable_cuda_dlls
    enable_cuda_dlls()
except Exception:
    pass


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


def _selfcheck() -> int:
    """Verifica, sem abrir a UI nem a camera, que toda a cadeia do face swap
    importa dentro do bundle. Usado para validar o empacotamento (o CI/build
    local roda `CamFX.exe --selfcheck` e confere o resultado). Sai 0 se OK, 1 se
    algum modulo faltar - foi o que quebrou o instalador (urllib3, joblib...).

    Como o exe e --windowed (sem console), o resultado tambem vai para
    LOCALAPPDATA/CamFX/selfcheck.txt, alem do stdout/exit code."""
    def _report(msg: str) -> None:
        print(msg)
        try:
            base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
            d = os.path.join(base, "CamFX")
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "selfcheck.txt"), "w", encoding="utf-8") as f:
                f.write(msg + "\n")
        except Exception:
            pass

    try:
        from camfx.vendor.dlc import ensure_engine
        ensure_engine()
        import modules.globals  # noqa: F401
        from modules.face_analyser import get_one_face  # noqa: F401
        from modules.processors.frame import face_swapper  # noqa: F401
        # e as libs de terceiros que quebravam em cascata:
        import insightface  # noqa: F401
        from insightface.utils import download  # noqa: F401  (-> requests -> urllib3)
        import sklearn.base  # noqa: F401  (-> joblib, scipy)
        import skimage.transform  # noqa: F401
        import albumentations  # noqa: F401
        _report("SELFCHECK: OK - cadeia de face swap importa sem modulo faltando")
        return 0
    except ModuleNotFoundError as exc:
        _report(f"SELFCHECK: FALHOU - modulo faltando: {exc}\n{traceback.format_exc()}")
        return 1
    except Exception as exc:
        # erro que nao e "modulo faltando" (ex.: DLL CUDA ausente sem GPU) nao
        # invalida o empacotamento; reporta como aviso.
        _report(f"SELFCHECK: aviso (nao-fatal p/ empacotamento): "
                f"{type(exc).__name__}: {exc}")
        return 0


if __name__ == "__main__":
    if "--selfcheck" in sys.argv:
        raise SystemExit(_selfcheck())
    try:
        main()
    except Exception:
        _write_startup("CRASH:\n" + traceback.format_exc())
        raise
