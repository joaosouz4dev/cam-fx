"""Operacoes de frame puras (sem estado): correcao de cor e ajuste de aspecto.

Extraidas do pipeline.py para separar a manipulacao de imagem da orquestracao
da thread. Sao funcoes puras (frame de entrada -> frame de saida), faceis de
testar isoladamente.
"""

from __future__ import annotations

import cv2


def fix_blue_cast(frame):
    """Corrige o tom azulado do DirectShow via gray-world white balance.

    O DirectShow entrega a imagem "crua" (B/R ~1.10, azulada). Equalizamos as
    medias dos canais para a media global (gray-world), o que neutraliza o
    excesso de azul aproximando da cor que o MSMF/Meet mostram. Barato (~1ms).
    """
    try:
        import numpy as np
        b, g, r = cv2.split(frame)
        mb, mg, mr = float(b.mean()), float(g.mean()), float(r.mean())
        mgray = (mb + mg + mr) / 3.0
        if mb > 1 and mr > 1 and mg > 1:
            b = cv2.multiply(b, mgray / mb)
            g = cv2.multiply(g, mgray / mg)
            r = cv2.multiply(r, mgray / mr)
            frame = cv2.merge([
                np.clip(b, 0, 255).astype("uint8"),
                np.clip(g, 0, 255).astype("uint8"),
                np.clip(r, 0, 255).astype("uint8"),
            ])
    except Exception:
        pass
    return frame


def fit_aspect(frame, out_w: int, out_h: int):
    """Redimensiona para out_w x out_h SEM esticar: corta o excesso (crop
    central) para casar o aspecto e so entao redimensiona.

    A camera pode entregar 4:3 (ex.: C505e em 960p = 1280x960) enquanto a saida
    e 16:9 (1280x720). Um cv2.resize direto esticava a imagem (rosto alongado).
    Aqui recortamos a faixa central no aspecto de saida (como os apps de video
    fazem) e redimensionamos, preservando as proporcoes."""
    h, w = frame.shape[:2]
    target = out_w / out_h
    src = w / h
    if abs(src - target) > 0.01:
        if src > target:
            # fonte mais larga: corta as laterais
            new_w = int(round(h * target))
            x0 = (w - new_w) // 2
            frame = frame[:, x0:x0 + new_w]
        else:
            # fonte mais alta (4:3 p/ 16:9): corta topo/base
            new_h = int(round(w / target))
            y0 = (h - new_h) // 2
            frame = frame[y0:y0 + new_h, :]
    if frame.shape[1] != out_w or frame.shape[0] != out_h:
        frame = cv2.resize(frame, (out_w, out_h))
    return frame
