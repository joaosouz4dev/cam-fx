"""Smoke test: baixa modelos e roda blur + framing sobre frames sinteticos.

Nao precisa de camera nem de camera virtual. Valida que os modelos carregam e
que o pipeline de imagem nao quebra.
"""

import numpy as np

from camfx.models import ensure_models
from camfx.segmentation import BackgroundBlur
from camfx.framing import AutoFraming


def make_frame(w=1280, h=720):
    # Fundo em gradiente + um "rosto" claro no centro.
    frame = np.zeros((h, w, 3), dtype=np.uint8)
    frame[:, :, 0] = np.tile(np.linspace(0, 255, w, dtype=np.uint8), (h, 1))
    cy, cx = h // 2, w // 2
    y, x = np.ogrid[:h, :w]
    face = (x - cx) ** 2 + (y - cy) ** 2 <= (h // 6) ** 2
    frame[face] = (200, 180, 170)
    return frame


def main():
    print("Baixando/validando modelos...")
    ensure_models(progress=print)

    blur = BackgroundBlur()
    framing = AutoFraming()
    frame = make_frame()

    for i in range(3):
        ts = i * 33
        out = framing.process(frame, ts, zoom=1.4, smoothing=0.9)
        assert out.shape == frame.shape, out.shape
        out = blur.process(out, ts + 1, blur_strength=25, mask_threshold=0.5)
        assert out.shape == frame.shape, out.shape
        assert out.dtype == np.uint8
        print(f"frame {i}: ok shape={out.shape} dtype={out.dtype}")

    blur.close()
    framing.close()
    print("SMOKE TEST OK")


if __name__ == "__main__":
    main()
