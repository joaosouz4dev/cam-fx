"""Backend de face swap usando o motor VENDORIZADO do Deep-Live-Cam.

Usa camfx/vendor/dlc (AGPL) por tras da interface FaceSwapperBackend, entao o
resto do app (pipeline, UI) nao muda. Este backend entrega a qualidade e o FPS
do Deep-Live-Cam (mouth mask, color correction, deteccao/pos-processamento
deles).

ATENCAO LICENCA: motor AGPL-3.0 + modelo inswapper research-only. Uso nao
comercial (ver camfx/terms.py).
"""

from __future__ import annotations

from typing import Any, Optional

import cv2
import numpy as np

from ..log import log
from ..models import enable_cuda_dlls
from .base import FaceSwapperBackend, SwapResult


def _providers(device: str) -> list[str]:
    cuda = "CUDAExecutionProvider"
    dml = "DmlExecutionProvider"
    cpu = "CPUExecutionProvider"
    try:
        import onnxruntime as ort
        avail = ort.get_available_providers()
    except Exception:
        avail = [cpu]
    if device == "cpu":
        return [cpu]
    if cuda in avail:
        return [cuda, cpu]
    if dml in avail:
        return [dml, cpu]
    return [cpu]


class DLCSwapper(FaceSwapperBackend):
    """Envolve o motor do Deep-Live-Cam. mouth_mask/color_correction ligados."""

    def __init__(self, device: str = "auto", mouth_mask: bool = True,
                 color_correction: bool = True):
        self._device = device
        self._swapper = None      # modulo face_swapper do DLC
        self._get_one_face = None
        self._source_face = None  # rosto-fonte (Face do insightface)
        self._last_target = None  # ultima deteccao do alvo
        self._closed = False
        # deteccao assincrona (thread), como a ponte
        self._det_thread = None
        self._det_lock = None
        self._det_latest_frame = None
        self._det_stop = None
        self._load(mouth_mask, color_correction)

    @staticmethod
    def available_devices() -> list[str]:
        try:
            import onnxruntime as ort
            av = ort.get_available_providers()
            if "CUDAExecutionProvider" in av or "DmlExecutionProvider" in av:
                return ["gpu", "cpu"]
        except Exception:
            pass
        return ["cpu"]

    def _load(self, mouth_mask, color_correction):
        # DLLs do CUDA no PATH antes de tocar no onnxruntime.
        if self._device != "cpu":
            enable_cuda_dlls()

        from ..vendor.dlc import ensure_engine
        swapper = ensure_engine()

        # O motor deles procura o modelo numa pasta `models` relativa ao proprio
        # arquivo (que no exe fica vazia). Apontamos o models_dir deles para o
        # nosso cache e garantimos o inswapper baixado la. Ele usa
        # inswapper_128_fp16.onnx quando CUDA, senao inswapper_128.onnx.
        from ..models import models_dir as camfx_models_dir, ensure_faceswap_models
        mdir = str(camfx_models_dir())
        swapper.models_dir = mdir
        provs = _providers(self._device)
        want_fp16 = "CUDAExecutionProvider" in provs
        try:
            ensure_faceswap_models(
                progress=lambda m: log(f"dlc: {m}"), fp16=want_fp16)
        except TypeError:
            ensure_faceswap_models(progress=lambda m: log(f"dlc: {m}"))

        # Configura os globals do motor.
        import modules.globals as G
        G.execution_providers = provs
        G.many_faces = False
        G.map_faces = False
        G.mouth_mask = bool(mouth_mask)
        G.color_correction = bool(color_correction)
        G.nsfw_filter = False
        G.source_path = None
        # o face_swapper le fp_ui em alguns caminhos
        if not hasattr(G, "fp_ui") or G.fp_ui is None:
            G.fp_ui = {}
        G.fp_ui.setdefault("face_enhancer", False)

        from modules.face_analyser import get_one_face
        self._get_one_face = get_one_face
        self._swapper = swapper

        # Forca o carregamento do modelo agora. Se falhar, o swap nao vai
        # funcionar - propaga para o pipeline desativar e logar claramente.
        model = swapper.get_face_swapper()
        if model is None:
            raise RuntimeError(
                f"motor DLC nao carregou o inswapper (models_dir={mdir}). "
                "Verifique se o modelo foi baixado.")
        log(f"dlc: motor pronto, provider={model.session.get_providers()[0]}")

    def prepare_source(self, image_bgr: np.ndarray) -> Optional[Any]:
        if self._get_one_face is None:
            return None
        face = self._get_one_face(image_bgr)
        return face  # pode ser None (sem rosto)

    def _ensure_detection_thread(self):
        """Deteccao assincrona (como a ponte que ficou linda): uma thread roda
        get_one_face continuamente no frame mais recente; o swap usa o ultimo
        resultado sem bloquear. E o que deu ~18 FPS na ponte."""
        if self._det_thread is not None:
            return
        import threading
        self._det_lock = threading.Lock()
        self._det_latest_frame = None
        self._det_stop = threading.Event()

        def _loop():
            import time as _t
            # onnxruntime/insightface em thread: garante COM em MTA.
            try:
                import ctypes
                ctypes.windll.ole32.CoInitializeEx(None, 0x0)
            except Exception:
                pass
            while not self._det_stop.is_set():
                with self._det_lock:
                    f = self._det_latest_frame
                if f is None:
                    _t.sleep(0.005)
                    continue
                try:
                    face = self._get_one_face(f)
                except Exception:
                    face = None
                with self._det_lock:
                    self._last_target = face
        self._det_thread = threading.Thread(target=_loop, daemon=True)
        self._det_thread.start()

    def swap_frame(self, frame_bgr, source, *, detect: bool = True) -> SwapResult:
        if self._swapper is None or source is None:
            return SwapResult(frame=frame_bgr, swapped=False)

        # Publica o frame para a thread de deteccao e usa a ultima deteccao
        # disponivel (nao bloqueia o loop principal esperando a deteccao).
        self._ensure_detection_thread()
        with self._det_lock:
            self._det_latest_frame = frame_bgr
            target = self._last_target

        if target is None:
            return SwapResult(frame=frame_bgr, swapped=False)

        try:
            out = self._swapper.swap_face(source, target, frame_bgr)
            bboxes = []
            if getattr(target, "bbox", None) is not None:
                bboxes.append(target.bbox.astype(int))
            out = self._swapper.apply_post_processing(out, bboxes)
            return SwapResult(frame=out, swapped=True)
        except Exception as exc:
            log(f"dlc: erro no swap: {exc!r}")
            return SwapResult(frame=frame_bgr, swapped=False)

    def close(self) -> None:
        self._closed = True
        if self._det_stop is not None:
            self._det_stop.set()
        if self._det_thread is not None:
            self._det_thread.join(timeout=2)
            self._det_thread = None
        self._swapper = None
        self._last_target = None
