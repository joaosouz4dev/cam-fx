"""CamFX - blur de fundo e auto-framing aplicados apenas na webcam.

Substituto leve e focado do NVIDIA Broadcast: os efeitos rodam so na camera,
nunca no microfone nem nos alto-falantes, e o app inicia minimizado na bandeja.
"""

# Desabilita os hardware transforms do MSMF antes que qualquer submodulo importe
# o OpenCV. Sem isso, abrir a webcam por Media Foundation leva 11-28s; com isso,
# ~1s. Definido aqui (no import do pacote) para valer em todos os caminhos.
import os as _os

_os.environ.setdefault("OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS", "0")

__version__ = "1.0.0"
