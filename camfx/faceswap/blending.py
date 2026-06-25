"""Tecnicas de qualidade do face swap (color matching, mascaras, feathering).

Reimplementacao propria das tecnicas que o Deep-Live-Cam usa para um swap mais
natural: transferencia de cor no espaco LAB (casa o tom do rosto trocado com a
iluminacao do frame), mascara facial suavizada (paste-back sem borda visivel) e
mascara da boca (preserva a boca original sobre o rosto trocado).

Trabalha com landmarks 2d_106 do insightface:
- contorno do rosto: indices 0..32
- labios (boca): indices 52..63
"""

from __future__ import annotations

import cv2
import numpy as np

# --- contornos por indice de landmark (insightface 2d_106) ---
_FACE_OUTLINE = list(range(0, 33))
_MOUTH = list(range(52, 64))

# parametros (mesmos valores do Deep-Live-Cam)
_FACE_MASK_BLUR = 31      # kernel gaussiano da mascara facial
_MOUTH_MASK_BLUR = 15     # kernel gaussiano da mascara da boca
_MOUTH_EXPAND = 0.10      # expansao radial da boca (1 + 0.10)
_LAB_EPS = 1e-6


def color_transfer_lab(source_bgr: np.ndarray, target_bgr: np.ndarray) -> np.ndarray:
    """Casa a cor de `source` com a de `target` no espaco LAB (mean/std).

    Usado para o rosto trocado herdar a iluminacao/tom do frame original,
    evitando diferenca visivel de cor. result = (src - src_mean)*(tgt_std/
    src_std) + tgt_mean, por canal L, A, B.
    """
    try:
        src = cv2.cvtColor(source_bgr.astype(np.float32) / 255.0, cv2.COLOR_BGR2LAB)
        tgt = cv2.cvtColor(target_bgr.astype(np.float32) / 255.0, cv2.COLOR_BGR2LAB)
        out = np.empty_like(src)
        for c in range(3):
            s_mean, s_std = src[:, :, c].mean(), src[:, :, c].std()
            t_mean, t_std = tgt[:, :, c].mean(), tgt[:, :, c].std()
            out[:, :, c] = (src[:, :, c] - s_mean) * (t_std / max(s_std, _LAB_EPS)) + t_mean
        out = cv2.cvtColor(out, cv2.COLOR_LAB2BGR)
        return np.clip(out * 255.0, 0, 255).astype(np.uint8)
    except Exception:
        return source_bgr


def _poly_mask(shape, points, blur_kernel) -> np.ndarray:
    """Mascara float [0..1] de um poligono (convex hull) com borda suavizada."""
    mask = np.zeros(shape[:2], dtype=np.uint8)
    pts = np.array(points, dtype=np.int32)
    if len(pts) >= 3:
        hull = cv2.convexHull(pts)
        cv2.fillConvexPoly(mask, hull, 255)
    k = blur_kernel | 1  # impar
    mask = cv2.GaussianBlur(mask, (k, k), 0)
    return (mask.astype(np.float32) / 255.0)[:, :, None]


def face_mask(frame_shape, landmarks) -> np.ndarray | None:
    """Mascara facial suavizada a partir do contorno (landmarks 0..32) +
    extensao para a testa. Retorna float [0..1] com shape (H,W,1)."""
    try:
        lm = np.asarray(landmarks, dtype=np.float32)
        if lm.shape[0] < 33:
            return None
        outline = lm[_FACE_OUTLINE]
        # estende para a testa: reflete o contorno do queixo para cima.
        center = outline.mean(axis=0)
        top = outline.copy()
        top[:, 1] = center[1] - (outline[:, 1] - center[1]) * 0.6
        pts = np.vstack([outline, top])
        return _poly_mask(frame_shape, pts, _FACE_MASK_BLUR)
    except Exception:
        return None


def blend(original: np.ndarray, swapped: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Compoe swapped sobre original usando a mascara float [0..1]."""
    return (original.astype(np.float32) * (1 - mask)
            + swapped.astype(np.float32) * mask).astype(np.uint8)


def mouth_mask(frame_shape, landmarks) -> np.ndarray | None:
    """Mascara da boca (labios 52..63) expandida e suavizada, float [0..1]."""
    try:
        lm = np.asarray(landmarks, dtype=np.float32)
        if lm.shape[0] < 64:
            return None
        mouth = lm[_MOUTH]
        center = mouth.mean(axis=0)
        mouth = center + (mouth - center) * (1 + _MOUTH_EXPAND)
        return _poly_mask(frame_shape, mouth, _MOUTH_MASK_BLUR)
    except Exception:
        return None
