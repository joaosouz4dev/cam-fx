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

        # Configura os globals do motor.
        import modules.globals as G
        G.execution_providers = _providers(self._device)
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

        # Forca o carregamento do modelo agora (loga o provider).
        try:
            sess = swapper.get_face_swapper().session
            log(f"dlc: swapper provider={sess.get_providers()[0]}")
        except Exception as exc:
            log(f"dlc: aviso ao carregar swapper: {exc!r}")

    def prepare_source(self, image_bgr: np.ndarray) -> Optional[Any]:
        if self._get_one_face is None:
            return None
        face = self._get_one_face(image_bgr)
        return face  # pode ser None (sem rosto)

    def swap_frame(self, frame_bgr, source, *, detect: bool = True) -> SwapResult:
        if self._swapper is None or source is None:
            return SwapResult(frame=frame_bgr, swapped=False)

        # Deteccao do rosto-alvo (amortizada: reusa a ultima quando detect=False).
        if detect or self._last_target is None:
            self._last_target = self._get_one_face(frame_bgr)
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
        self._swapper = None
        self._last_target = None
