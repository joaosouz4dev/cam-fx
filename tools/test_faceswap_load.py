"""Teste minimo: carrega o motor de face swap (Deep-Live-Cam vendorizado).

Valida que ensure_engine() registra o motor e que a cadeia de deteccao/swap
importa e sobe, sem imagens pessoais. E o mesmo caminho que o BridgeRunner usa.

Uso: python tools/test_faceswap_load.py
"""

import os
import sys
import time

os.environ.setdefault("OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS", "0")

# roda a partir da raiz do projeto
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    t0 = time.perf_counter()
    print("Carregando motor DLC (pode baixar ~250MB do inswapper + buffalo_l)...")

    from camfx.models import enable_cuda_dlls
    enable_cuda_dlls()

    from camfx.vendor.dlc import ensure_engine
    swapper = ensure_engine()
    print(f"ensure_engine() OK em {time.perf_counter() - t0:.1f}s")

    import modules.globals as G
    G.execution_providers = ["CPUExecutionProvider"]
    G.frame_processors = ["face_swapper"]
    G.fp_ui = {"face_enhancer": False}

    from modules.face_analyser import get_one_face

    # imagem sintetica sem rosto: valida que get_one_face roda sem crashar
    import numpy as np
    dummy = np.full((480, 640, 3), 80, dtype=np.uint8)
    face = get_one_face(dummy)
    print("get_one_face em imagem sem rosto retornou:", face)

    print("OK: motor DLC carrega, baixa modelos e a deteccao roda sem erros.")


if __name__ == "__main__":
    main()
