"""Melhoria/restauracao do rosto trocado via GFPGAN em ONNX (sem PyTorch).

Opcional e custoso: roda so quando o usuario liga "Melhorar nitidez do rosto".
Mantido atras de uma interface simples para poder trocar por CodeFormer depois.
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from ..log import log
from ..models import ensure_enhancer_model


class FaceEnhancer:
    """Aplica GFPGAN (ONNX) num crop de rosto 512x512 e devolve melhorado."""

    INPUT = 512

    def __init__(self, providers: list[str], model_path: str | None = None):
        self._sess = None
        self._in_name = None
        self._out_name = None
        try:
            import onnxruntime as ort
            # Usa o enhancer selecionado (catalogo/proprio) se houver caminho;
            # senao cai no GFPGAN padrao baixado sob demanda.
            import os
            if model_path and os.path.exists(model_path):
                path = model_path
            else:
                path = str(ensure_enhancer_model(progress=lambda m: log(f"enhancer: {m}")))
            self._sess = ort.InferenceSession(str(path), providers=providers)
            self._in_name = self._sess.get_inputs()[0].name
            self._out_name = self._sess.get_outputs()[0].name
            log(f"enhancer: pronto ({os.path.basename(str(path))})")
        except Exception as exc:
            log(f"enhancer: indisponivel ({exc!r})")
            self._sess = None

    @property
    def ready(self) -> bool:
        return self._sess is not None

    def enhance_face(self, face_bgr: np.ndarray) -> Optional[np.ndarray]:
        """Recebe um crop de rosto BGR, retorna o mesmo tamanho melhorado."""
        if self._sess is None:
            return None
        try:
            import cv2
            h, w = face_bgr.shape[:2]
            inp = cv2.resize(face_bgr, (self.INPUT, self.INPUT))
            inp = cv2.cvtColor(inp, cv2.COLOR_BGR2RGB).astype(np.float32)
            inp = (inp / 255.0 - 0.5) / 0.5            # normaliza [-1,1]
            inp = np.transpose(inp, (2, 0, 1))[None]   # NCHW
            out = self._sess.run([self._out_name], {self._in_name: inp})[0]
            out = out[0].clip(-1, 1)
            out = ((out + 1) / 2 * 255).astype(np.uint8)
            out = np.transpose(out, (1, 2, 0))         # HWC
            out = cv2.cvtColor(out, cv2.COLOR_RGB2BGR)
            return cv2.resize(out, (w, h))
        except Exception as exc:
            log(f"enhancer: erro ({exc!r})")
            return None

    def close(self):
        self._sess = None
