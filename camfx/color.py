"""Correcao automatica de white balance (gray-world).

A webcam C505e tem um vies azul (white balance frio): o canal azul fica ~11%
mais forte que o vermelho. Esta correcao equaliza as medias dos canais para
neutralizar o dominante de cor, deixando a imagem mais natural.

Usa suavizacao temporal nos ganhos para nao "pulsar" a cor entre frames.
"""

from __future__ import annotations

import numpy as np


class WhiteBalance:
    def __init__(self, strength: float = 1.0, smoothing: float = 0.9):
        # strength: 0 = sem correcao, 1 = gray-world completo.
        self.strength = float(np.clip(strength, 0.0, 1.0))
        self.smoothing = float(np.clip(smoothing, 0.0, 0.98))
        self._gains = None  # (gb, gg, gr) suavizados

    def apply(self, frame_bgr: np.ndarray) -> np.ndarray:
        if self.strength <= 0:
            return frame_bgr
        b = float(frame_bgr[:, :, 0].mean())
        g = float(frame_bgr[:, :, 1].mean())
        r = float(frame_bgr[:, :, 2].mean())
        gray = (b + g + r) / 3.0
        if gray < 1:
            return frame_bgr

        # Ganho por canal para levar cada media a media cinza.
        target = np.array([gray / max(b, 1), gray / max(g, 1), gray / max(r, 1)])
        # Aplica so uma fracao (strength) do ganho, para nao exagerar.
        target = 1.0 + (target - 1.0) * self.strength

        if self._gains is None:
            self._gains = target
        else:
            a = self.smoothing
            self._gains = a * self._gains + (1.0 - a) * target

        out = frame_bgr.astype(np.float32)
        out[:, :, 0] *= self._gains[0]
        out[:, :, 1] *= self._gains[1]
        out[:, :, 2] *= self._gains[2]
        return np.clip(out, 0, 255).astype(np.uint8)
