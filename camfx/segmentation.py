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
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

        result = self._segmenter.segment_for_video(mp_image, timestamp_ms)
        # confidence_masks[0] = confianca de ser foreground (pessoa).
        confidence = result.confidence_masks[0].numpy_view()

        mask = self._refine_mask(
            confidence, frame_bgr.shape[:2], mask_threshold, edge_softness
        )
        self._last_mask = mask

        kernel = self._odd(blur_strength)
        blurred = cv2.GaussianBlur(frame_bgr, (kernel, kernel), 0)

        # Composicao alpha: pessoa nitida sobre fundo borrado.
        alpha = mask[:, :, np.newaxis]
        out = frame_bgr * alpha + blurred * (1.0 - alpha)
        return out.astype(np.uint8)

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
