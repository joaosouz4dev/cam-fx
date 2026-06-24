"""Teste minimo: carrega o backend de face swap (baixa modelos se faltarem).

Nao usa imagens pessoais; so valida que os modelos baixam e as sessoes ONNX
(DirectML) sobem. Uso: python tools/test_faceswap_load.py
"""

import os
import sys
import time

os.environ.setdefault("OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS", "0")

# roda a partir da raiz do projeto
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camfx.faceswap import load_swapper  # noqa: E402


def main():
    print("Dispositivos disponiveis:",
          __import__("camfx.faceswap.insightface_backend",
                     fromlist=["InsightFaceSwapper"])
          .InsightFaceSwapper.available_devices())
    t0 = time.perf_counter()
    print("Carregando backend (pode baixar ~250MB do inswapper + buffalo_l)...")
    swapper = load_swapper("insightface", "auto", enhance=False)
    print(f"Backend pronto em {time.perf_counter() - t0:.1f}s")

    # Gera uma imagem sintetica simples so para exercitar a deteccao (nao deve
    # achar rosto; valida que get() roda sem crashar).
    import numpy as np
    dummy = np.full((480, 640, 3), 80, dtype=np.uint8)
    src = swapper.prepare_source(dummy)
    print("prepare_source em imagem sem rosto retornou:", src)
    res = swapper.swap_frame(dummy, src)
    print("swap_frame sem fonte/rosto -> swapped =", res.swapped)
    swapper.close()
    print("OK: backend carrega, baixa modelos e roda sem erros.")


if __name__ == "__main__":
    main()
