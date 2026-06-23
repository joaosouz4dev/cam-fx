"""Ponto de entrada do CamFX.

Uso:
    python main.py              abre a janela normalmente
    python main.py --minimized  inicia direto na bandeja (usado no autostart)
"""

# CRITICO: desabilitar os hardware transforms do MSMF ANTES de qualquer import
# do OpenCV. Sem isso, abrir a webcam por Media Foundation leva 11-28s nesta
# maquina; com isso, abre em ~1s. Precisa estar no ambiente antes do cv2 carregar.
import os

os.environ.setdefault("OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS", "0")

from camfx.app import main

if __name__ == "__main__":
    main()
