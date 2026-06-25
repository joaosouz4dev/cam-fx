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

import os
from typing import Any, Optional

import numpy as np

from ..log import log
from ..models import ensure_faceswap_models, insightface_home, enable_cuda_dlls
from .base import FaceSwapperBackend, SwapResult


def _avail_providers() -> list[str]:
    try:
        import onnxruntime as ort
        return list(ort.get_available_providers())
    except Exception:
        return ["CPUExecutionProvider"]


def _has_cuda() -> bool:
    return "CUDAExecutionProvider" in _avail_providers()


def _providers_for(device: str) -> list[str]:
    """Providers do ONNX Runtime para o face swap.

    Prioridade: CUDA (NVIDIA) > DirectML (qualquer GPU) > CPU. O CUDA, ao
    contrario do DirectML, e estavel cross-thread e bem mais rapido, entao e a
    escolha ideal para o inswapper quando ha placa NVIDIA.
    """
    cuda = "CUDAExecutionProvider"
    dml = "DmlExecutionProvider"
    cpu = "CPUExecutionProvider"
    avail = _avail_providers()
    if device == "cpu":
        return [cpu]
    if device == "gpu":
        if cuda in avail:
            return [cuda, cpu]
        if dml in avail:
            return [dml, cpu]
        return [cpu]
    # auto: CUDA > DML > CPU
    if cuda in avail:
        return [cuda, cpu]
    if dml in avail:
        return [dml, cpu]
    return [cpu]


class InsightFaceSwapper(FaceSwapperBackend):
    def __init__(self, device: str = "auto", enhance: bool = False,
                 swap_model_path: str | None = None,
                 enhance_model_path: str | None = None):
        self._device = device
        self._want_enhance = enhance
        self._swap_model_path = swap_model_path  # .onnx do swapper selecionado
        self._enhance_model_path = enhance_model_path  # .onnx do enhancer
        self._app = None          # FaceAnalysis (detector + recognition)
        self._swapper = None      # INSwapper
        self._enhancer = None     # FaceEnhancer | None
        self._last_faces = None   # ultima deteccao (para reuso entre frames)
        self._closed = False
        self._load()

    @staticmethod
    def available_devices() -> list[str]:
        avail = _avail_providers()
        if "CUDAExecutionProvider" in avail or "DmlExecutionProvider" in avail:
            return ["gpu", "cpu"]
        return ["cpu"]

    def _load(self):
        # Coloca as DLLs do CUDA/cuDNN no PATH para o onnxruntime achar a GPU
        # (sem isso, o CUDAExecutionProvider cai para CPU silenciosamente).
        if enable_cuda_dlls():
            log("faceswap: DLLs CUDA adicionadas ao PATH")
        # Garante os modelos (inswapper baixado; buffalo_l o insightface baixa).
        insightface_home()
        paths = ensure_faceswap_models(progress=lambda m: log(f"faceswap: {m}"))
        providers = _providers_for(self._device)

        from insightface.app import FaceAnalysis
        from insightface.model_zoo import get_model

        cuda = _has_cuda()
        # Com CUDA (NVIDIA) tudo roda em GPU: o CUDAExecutionProvider e estavel
        # cross-thread e rapido, entao detector e inswapper usam CUDA.
        # Sem CUDA, so DirectML disponivel: o det_10g quebra em DML e o inswapper
        # crasha cross-thread, entao caimos para CPU (estavel; worker assincrono
        # mantem o FPS).
        if cuda:
            det_providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
            swap_providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
            ctx_id = 0
        else:
            det_providers = ["CPUExecutionProvider"]
            swap_providers = ["CPUExecutionProvider"]
            ctx_id = 0
        log(f"faceswap: cuda={cuda} det={det_providers} swap={swap_providers}")

        self._app = FaceAnalysis(name="buffalo_l", providers=det_providers)
        self._app.prepare(ctx_id=ctx_id, det_size=(320, 320))

        # Swapper: usa o modelo selecionado (catalogo/proprio); cai no inswapper
        # padrao baixado se nenhum caminho valido foi passado.
        swap_path = self._swap_model_path
        if not swap_path or not os.path.exists(swap_path):
            swap_path = str(paths["inswapper_128.onnx"])
        log(f"faceswap: swapper={os.path.basename(swap_path)}")
        self._swapper = get_model(swap_path, providers=swap_providers)

        if self._want_enhance:
            from .enhancer import FaceEnhancer
            enh = FaceEnhancer(swap_providers, model_path=self._enhance_model_path)
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

        from . import blending

        original = frame_bgr
        out = frame_bgr
        for face in faces:
            # 1) swap cru (inswapper cola sua face 128x128 de volta).
            swapped = self._swapper.get(out, face, source, paste_back=True)

            lm = getattr(face, "landmark_2d_106", None)
            if lm is None:
                out = swapped
            else:
                # 2) color matching: o rosto trocado herda a iluminacao/tom do
                #    frame original (evita diferenca de cor).
                fmask = blending.face_mask(original.shape, lm)
                if fmask is not None:
                    swapped = blending.color_transfer_lab(swapped, original)
                    # 3) blend com mascara facial suavizada (sem borda visivel).
                    out = blending.blend(original, swapped, fmask)
                    # 4) mouth mask: restaura a boca original (fala natural).
                    mmask = blending.mouth_mask(original.shape, lm)
                    if mmask is not None:
                        out = blending.blend(out, original, mmask)
                else:
                    out = swapped

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
