"""Motor de face swap do Deep-Live-Cam, vendorizado (AGPL-3.0).

Os modulos originais importam uns aos outros como `modules.xxx` (absoluto).
Para nao reescrever o codigo deles, registramos o subpacote vendorizado
`camfx.vendor.dlc.modules` sob o nome `modules` em sys.modules na primeira
importacao. Assim `import modules.globals` deles resolve para o nosso vendor.

ATENCAO LICENCA: o Deep-Live-Cam e AGPL-3.0 e o modelo inswapper e research-only.
Uso nao comercial (ver camfx/terms.py e README).
"""

from __future__ import annotations

import importlib
import sys

_loaded = False


def ensure_engine():
    """Registra o pacote `modules` (vendor) e devolve o modulo face_swapper.

    Idempotente. Deve ser chamado antes de usar o motor.
    """
    global _loaded
    if not _loaded:
        pkg = importlib.import_module("camfx.vendor.dlc.modules")
        sys.modules.setdefault("modules", pkg)
        # registra tambem os submodulos ja importados sob o prefixo `modules.`
        _loaded = True
    return importlib.import_module(
        "camfx.vendor.dlc.modules.processors.frame.face_swapper")
