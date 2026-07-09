"""Pacote de troca de rosto (face swap) do CamFX.

O face swap roda pelo SwapStage (camfx/faceswap/swap_stage.py), plugado no
pipeline unico. Usa o motor Deep-Live-Cam vendorizado (camfx/vendor/dlc,
modelos fica em registry.py e o cache do rosto-fonte em source_face.py.

ATENCAO LICENCA: o modelo inswapper_128 e research-only (nao comercial) e o
motor DLC e AGPL-3.0. Ver camfx/terms.py e o README.
"""

from __future__ import annotations
