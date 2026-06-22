"""Auto-framing via MediaPipe Face Detector.

Detecta o rosto, calcula um retangulo de corte centrado nele e suaviza o
movimento com media exponencial para a imagem nao "tremer". Em seguida o corte
e redimensionado de volta ao tamanho de saida (efeito de camera que te segue).
"""

from __future__ import annotations

import cv2
import numpy as np
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

from .models import bundled_or_cached


class AutoFraming:
    def __init__(self) -> None:
        model_path = str(bundled_or_cached("blaze_face_short_range.tflite"))
        options = vision.FaceDetectorOptions(
            base_options=mp_python.BaseOptions(model_asset_path=model_path),
            running_mode=vision.RunningMode.VIDEO,
            min_detection_confidence=0.5,
        )
        self._detector = vision.FaceDetector.create_from_options(options)
        # Estado do retangulo de corte suavizado (cx, cy, w, h) em pixels.
        self._smoothed: tuple[float, float, float, float] | None = None

    def process(
        self,
        frame_bgr: np.ndarray,
        timestamp_ms: int,
        *,
        zoom: float = 1.4,
        smoothing: float = 0.9,
    ) -> np.ndarray:
        h, w = frame_bgr.shape[:2]
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        result = self._detector.detect_for_video(mp_image, timestamp_ms)

        target = self._target_crop(result, w, h, zoom)
        if target is None:
            # Sem rosto: relaxa devagar de volta para o frame cheio.
            target = (w / 2.0, h / 2.0, float(w), float(h))

        self._smoothed = self._smooth(self._smoothed, target, smoothing)
        cx, cy, cw, ch = self._clamp(self._smoothed, w, h)

        x1 = int(round(cx - cw / 2))
        y1 = int(round(cy - ch / 2))
        crop = frame_bgr[y1 : y1 + int(round(ch)), x1 : x1 + int(round(cw))]
        if crop.size == 0:
            return frame_bgr
        return cv2.resize(crop, (w, h), interpolation=cv2.INTER_LINEAR)

    def _target_crop(self, result, w: int, h: int, zoom: float):
        if not result.detections:
            return None
        # Usa a maior deteccao (rosto mais proximo / principal).
        det = max(
            result.detections,
            key=lambda d: d.bounding_box.width * d.bounding_box.height,
        )
        box = det.bounding_box
        face_cx = box.origin_x + box.width / 2.0
        face_cy = box.origin_y + box.height / 2.0

        zoom = max(1.0, float(zoom))
        crop_w = w / zoom
        crop_h = h / zoom
        return (face_cx, face_cy, crop_w, crop_h)

    @staticmethod
    def _smooth(prev, target, smoothing: float):
        if prev is None:
            return target
        a = float(np.clip(smoothing, 0.0, 0.99))
        return tuple(a * p + (1.0 - a) * t for p, t in zip(prev, target))

    @staticmethod
    def _clamp(rect, w: int, h: int):
        cx, cy, cw, ch = rect
        cw = min(cw, w)
        ch = min(ch, h)
        cx = min(max(cx, cw / 2), w - cw / 2)
        cy = min(max(cy, ch / 2), h - ch / 2)
        return cx, cy, cw, ch

    def close(self) -> None:
        self._detector.close()
