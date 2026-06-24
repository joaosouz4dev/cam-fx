"""Backend de face swap usando insightface (FaceAnalysis buffalo_l + inswapper).

ATENCAO LICENCA: o modelo inswapper_128 da InsightFace e licenciado apenas para
pesquisa/uso nao comercial. Este arquivo concentra essa dependencia; para um uso
comercial troque por outro backend que implemente FaceSwapperBackend.

Estrategia de performance embutida:
- Deteccao de rosto-alvo amortizada: detecta a cada N frames e reusa a ultima
  bounding box/landmarks entre deteccoes (o chamador controla via detect=...).
- inswapper opera em 128x128 do crop alinhado, entao e insensivel a resolucao
  do frame; so o paste-back volta ao tamanho cheio.
- Providers via DirectML (GPU) com fallback CPU.
"""

from __future__ import annotations

from typing import Any, Optional

import numpy as np

from ..log import log
from ..models import ensure_faceswap_models, insightface_home
from .base import FaceSwapperBackend, SwapResult


def _providers_for(device: str) -> list[str]:
    """Mapeia a escolha do usuario para os execution providers do ONNX Runtime.
    Espelha camfx/segmentation.py para coerencia."""
    dml = "DmlExecutionProvider"
    cpu = "CPUExecutionProvider"
    try:
        import onnxruntime as ort
        avail = ort.get_available_providers()
    except Exception:
        avail = [cpu]
    if device == "cpu":
        return [cpu]
    if device == "gpu":
        return [dml, cpu] if dml in avail else [cpu]
    # auto
    return [dml, cpu] if dml in avail else [cpu]


class InsightFaceSwapper(FaceSwapperBackend):
    def __init__(self, device: str = "auto", enhance: bool = False):
        self._device = device
        self._want_enhance = enhance
        self._app = None          # FaceAnalysis (detector + recognition)
        self._swapper = None      # INSwapper
        self._enhancer = None     # FaceEnhancer | None
        self._last_faces = None   # ultima deteccao (para reuso entre frames)
        self._closed = False
        self._load()

    @staticmethod
    def available_devices() -> list[str]:
        try:
            import onnxruntime as ort
            if "DmlExecutionProvider" in ort.get_available_providers():
                return ["gpu", "cpu"]
        except Exception:
            pass
        return ["cpu"]

    def _load(self):
        # Garante os modelos (inswapper baixado; buffalo_l o insightface baixa).
        insightface_home()
        paths = ensure_faceswap_models(progress=lambda m: log(f"faceswap: {m}"))
        providers = _providers_for(self._device)

        from insightface.app import FaceAnalysis
        from insightface.model_zoo import get_model

        # IMPORTANTE: o detector/recognition (RetinaFace det_10g do buffalo_l)
        # quebra no DmlExecutionProvider (DirectML) com um UnicodeDecodeError
        # vindo do session.run nesta combinacao de versoes. A deteccao e leve,
        # entao roda SEMPRE em CPU. O peso de verdade e o inswapper, que tenta
        # GPU e cai para CPU se falhar.
        det_providers = ["CPUExecutionProvider"]
        log(f"faceswap: detector providers={det_providers} (CPU forcado)")
        self._app = FaceAnalysis(name="buffalo_l", providers=det_providers)
        # det_size menor = deteccao mais rapida; 320 e um bom equilibrio.
        self._app.prepare(ctx_id=0, det_size=(320, 320))

        # inswapper: por ESTABILIDADE roda em CPU. O DirectML do inswapper
        # funciona isolado (54ms), mas no app as sessoes DirectML sao criadas na
        # thread STA do pipeline e usadas na thread do worker, o que crasha o
        # processo (apartment COM incompativel). Em CPU (~1.1s/swap) e estavel; o
        # worker e assincrono, entao o FPS de saida nao cai (so o rosto trocado
        # atualiza mais devagar). Otimizar p/ GPU exige criar/usar a sessao na
        # MESMA thread (refator futuro). Forcamos CPU aqui.
        swap_providers = ["CPUExecutionProvider"]
        log(f"faceswap: inswapper providers={swap_providers} (CPU p/ estabilidade)")
        self._swapper = get_model(str(paths["inswapper_128.onnx"]),
                                  providers=swap_providers)

        if self._want_enhance:
            from .enhancer import FaceEnhancer
            enh = FaceEnhancer(swap_providers)  # CPU, mesma razao do inswapper
            self._enhancer = enh if enh.ready else None
        log("faceswap: insightface pronto")

    def prepare_source(self, image_bgr: np.ndarray) -> Optional[Any]:
        if self._app is None:
            return None
        faces = self._app.get(image_bgr)
        if not faces:
            return None
        # Maior rosto da foto-fonte.
        face = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
        return face

    def swap_frame(self, frame_bgr, source, *, detect: bool = True) -> SwapResult:
        if self._app is None or self._swapper is None or source is None:
            return SwapResult(frame=frame_bgr, swapped=False)

        if detect or self._last_faces is None:
            faces = self._app.get(frame_bgr)
            self._last_faces = faces
        else:
            faces = self._last_faces

        if not faces:
            return SwapResult(frame=frame_bgr, swapped=False)

        out = frame_bgr
        for face in faces:
            out = self._swapper.get(out, face, source, paste_back=True)
            if self._enhancer is not None:
                out = self._enhance_region(out, face)
        return SwapResult(frame=out, swapped=True)

    def _enhance_region(self, frame, face):
        """Melhora apenas a regiao do rosto (bbox), recompondo no frame."""
        try:
            import numpy as np
            h, w = frame.shape[:2]
            x1, y1, x2, y2 = [int(v) for v in face.bbox]
            # margem para pegar contorno do rosto
            mw, mh = int((x2 - x1) * 0.2), int((y2 - y1) * 0.2)
            x1, y1 = max(0, x1 - mw), max(0, y1 - mh)
            x2, y2 = min(w, x2 + mw), min(h, y2 + mh)
            if x2 <= x1 or y2 <= y1:
                return frame
            crop = frame[y1:y2, x1:x2]
            better = self._enhancer.enhance_face(crop)
            if better is not None:
                frame[y1:y2, x1:x2] = better
            return frame
        except Exception:
            return frame

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._app = None
        self._swapper = None
        self._last_faces = None
