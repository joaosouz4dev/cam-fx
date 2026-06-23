"""Blur de fundo via MediaPipe Image Segmenter (selfie segmentation).

Recebe um frame BGR (OpenCV), produz uma mascara de "pessoa" e compoe a pessoa
nitida sobre uma versao desfocada do mesmo frame.
"""

from __future__ import annotations

import cv2
import numpy as np
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

from .models import bundled_or_cached


class BackgroundBlur:
    def __init__(self) -> None:
        model_path = str(bundled_or_cached("selfie_segmenter.tflite"))
        options = vision.ImageSegmenterOptions(
            base_options=mp_python.BaseOptions(model_asset_path=model_path),
            running_mode=vision.RunningMode.VIDEO,
            output_category_mask=False,
            output_confidence_masks=True,
        )
        self._segmenter = vision.ImageSegmenter.create_from_options(options)
        self._last_mask: np.ndarray | None = None

    def process(
        self,
        frame_bgr: np.ndarray,
        timestamp_ms: int,
        *,
        blur_strength: int = 25,
        mask_threshold: float = 0.5,
        edge_softness: int = 7,
    ) -> np.ndarray:
        """Retorna o frame com o fundo desfocado."""
        h, w = frame_bgr.shape[:2]

        # Segmenta numa imagem reduzida (~360p de largura): a mascara de pessoa
        # nao precisa da resolucao cheia, e a inferencia fica varias vezes mais
        # rapida. Em 720p isso e o que mais derruba o tempo de processamento.
        seg_w = 480
        seg_scale = seg_w / w if w > seg_w else 1.0
        if seg_scale < 1.0:
            seg_in = cv2.resize(frame_bgr, (seg_w, int(h * seg_scale)),
                                interpolation=cv2.INTER_LINEAR)
        else:
            seg_in = frame_bgr

        rgb = cv2.cvtColor(seg_in, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        result = self._segmenter.segment_for_video(mp_image, timestamp_ms)
        confidence = result.confidence_masks[0].numpy_view()

        # A composicao (mistura pessoa nitida + fundo borrado) em 720p full-res
        # custa ~17-30ms na CPU - o maior gargalo. Fazemos a composicao numa
        # escala reduzida (COMPOSE_SCALE) e ampliamos: ~3x mais rapido. A pessoa
        # perde um pouco de nitidez, mas o ganho de FPS compensa. (Quando houver
        # GPU/OpenCV-CUDA, este e o ponto a acelerar sem reduzir escala.)
        cs = 0.5
        cw, ch = max(1, int(w * cs)), max(1, int(h * cs))
        frame_s = cv2.resize(frame_bgr, (cw, ch), interpolation=cv2.INTER_AREA)

        # Mascara na escala da composicao.
        mask = self._refine_mask(confidence, (ch, cw), mask_threshold, edge_softness)
        self._last_mask = mask

        # Fundo borrado na escala da composicao.
        k = self._odd(max(3, int(blur_strength * cs)))
        blurred_s = cv2.GaussianBlur(frame_s, (k, k), 0)

        # Composicao alpha em escala reduzida (float32 in-place).
        alpha = mask[:, :, np.newaxis].astype(np.float32, copy=False)
        fg = frame_s.astype(np.float32)
        bg = blurred_s.astype(np.float32)
        fg -= bg
        fg *= alpha
        fg += bg
        out_s = fg.astype(np.uint8)

        # Amplia o resultado de volta a resolucao cheia.
        return cv2.resize(out_s, (w, h), interpolation=cv2.INTER_LINEAR)

    def _refine_mask(
        self,
        confidence: np.ndarray,
        shape: tuple[int, int],
        threshold: float,
        edge_softness: int,
    ) -> np.ndarray:
        h, w = shape
        if confidence.shape[:2] != (h, w):
            confidence = cv2.resize(confidence, (w, h), interpolation=cv2.INTER_LINEAR)
        # Binariza no threshold e suaviza a borda para a transicao nao serrilhar.
        mask = (confidence >= threshold).astype(np.float32)
        k = self._odd(edge_softness)
        mask = cv2.GaussianBlur(mask, (k, k), 0)
        return np.clip(mask, 0.0, 1.0)

    @staticmethod
    def _odd(value: int) -> int:
        value = max(3, int(value))
        return value if value % 2 == 1 else value + 1

    def close(self) -> None:
        self._segmenter.close()
