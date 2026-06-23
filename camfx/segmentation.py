"""Blur de fundo via ONNX Runtime (selfie segmentation).

A segmentacao roda na GPU (DirectML) quando disponivel, com fallback para CPU.
O modelo (256x256) devolve uma mascara de pessoa; compomos a pessoa nitida
sobre uma versao desfocada do mesmo frame. A composicao e feita em escala
reduzida (o gargalo em 720p na CPU) e ampliada de volta.
"""

from __future__ import annotations

import cv2
import numpy as np

from .models import bundled_or_cached

_INPUT = 256  # o modelo opera em 256x256


def available_devices() -> list[str]:
    """Lista os devices que conseguimos usar: ['gpu','cpu'] ou ['cpu']."""
    try:
        import onnxruntime as ort

        provs = ort.get_available_providers()
        out = []
        if "DmlExecutionProvider" in provs or "CUDAExecutionProvider" in provs:
            out.append("gpu")
        out.append("cpu")
        return out
    except Exception:
        return ["cpu"]


def _providers_for(device: str):
    """Mapeia a preferencia (auto|gpu|cpu) para a lista de providers do ORT."""
    import onnxruntime as ort

    avail = ort.get_available_providers()
    gpu = [p for p in ("DmlExecutionProvider", "CUDAExecutionProvider") if p in avail]
    if device == "cpu":
        return ["CPUExecutionProvider"]
    if device == "gpu":
        return gpu + ["CPUExecutionProvider"]
    # auto: GPU se houver, senao CPU
    return gpu + ["CPUExecutionProvider"]


class BackgroundBlur:
    def __init__(self, device: str = "auto") -> None:
        import onnxruntime as ort

        model_path = str(bundled_or_cached("selfie_segmentation.onnx"))
        opts = ort.SessionOptions()
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self._sess = ort.InferenceSession(
            model_path, sess_options=opts, providers=_providers_for(device))
        self._input_name = self._sess.get_inputs()[0].name
        self.active_provider = self._sess.get_providers()[0]
        self._last_mask: np.ndarray | None = None

    def process(
        self,
        frame_bgr: np.ndarray,
        timestamp_ms: int = 0,
        *,
        blur_strength: int = 25,
        mask_threshold: float = 0.5,
        edge_softness: int = 7,
    ) -> np.ndarray:
        """Retorna o frame com o fundo desfocado."""
        h, w = frame_bgr.shape[:2]

        # Inferencia: 256x256 RGB normalizado, NCHW.
        inp = cv2.resize(frame_bgr, (_INPUT, _INPUT), interpolation=cv2.INTER_LINEAR)
        inp = cv2.cvtColor(inp, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        inp = np.transpose(inp, (2, 0, 1))[np.newaxis]
        conf = self._sess.run(None, {self._input_name: inp})[0][0, 0]  # 256x256, 0..1

        # Composicao em escala reduzida (gargalo em 720p full-res na CPU).
        cs = 0.5
        cw, ch = max(1, int(w * cs)), max(1, int(h * cs))
        frame_s = cv2.resize(frame_bgr, (cw, ch), interpolation=cv2.INTER_AREA)

        mask = self._refine_mask(conf, (ch, cw), mask_threshold, edge_softness)
        self._last_mask = mask

        k = self._odd(max(3, int(blur_strength * cs)))
        blurred_s = cv2.GaussianBlur(frame_s, (k, k), 0)

        alpha = mask[:, :, np.newaxis].astype(np.float32, copy=False)
        fg = frame_s.astype(np.float32)
        bg = blurred_s.astype(np.float32)
        fg -= bg
        fg *= alpha
        fg += bg
        out_s = fg.astype(np.uint8)
        return cv2.resize(out_s, (w, h), interpolation=cv2.INTER_LINEAR)

    def _refine_mask(self, confidence, shape, threshold, edge_softness):
        ch, cw = shape
        m = cv2.resize(confidence, (cw, ch), interpolation=cv2.INTER_LINEAR)
        m = (m >= threshold).astype(np.float32)
        k = self._odd(edge_softness)
        m = cv2.GaussianBlur(m, (k, k), 0)
        return np.clip(m, 0.0, 1.0)

    @staticmethod
    def _odd(value: int) -> int:
        value = max(3, int(value))
        return value if value % 2 == 1 else value + 1

    def close(self) -> None:
        self._sess = None
