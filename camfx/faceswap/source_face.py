"""Carrega e cacheia o rosto-fonte (a foto escolhida pelo usuario).

O embedding/handle do rosto-fonte e caro de extrair, mas so muda quando a foto
muda. Esta classe memoriza por caminho de arquivo.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import numpy as np

from ..log import log


class SourceFace:
    def __init__(self):
        self._path: str = ""
        self._handle: Any = None

    @property
    def ready(self) -> bool:
        return self._handle is not None

    def load(self, path: str, backend) -> bool:
        """Carrega o rosto-fonte do `path` usando o backend. Reusa o cache se o
        caminho nao mudou. Retorna True se ha um rosto pronto."""
        if not path:
            self._handle = None
            self._path = ""
            return False
        if path == self._path and self._handle is not None:
            return True
        img = _imread_unicode(path)
        if img is None:
            log(f"source_face: nao foi possivel ler {path!r}")
            self._handle = None
            self._path = ""
            return False
        handle = backend.prepare_source(img)
        if handle is None:
            log(f"source_face: nenhum rosto encontrado em {path!r}")
            self._handle = None
            self._path = ""
            return False
        self._handle = handle
        self._path = path
        return True

    @property
    def handle(self) -> Optional[Any]:
        return self._handle


def _imread_unicode(path: str):
    """cv2.imread falha com acentos no caminho no Windows; usa imdecode."""
    try:
        import cv2
        if not Path(path).exists():
            return None
        data = np.fromfile(path, dtype=np.uint8)
        return cv2.imdecode(data, cv2.IMREAD_COLOR)
    except Exception as exc:
        log(f"source_face: erro lendo imagem: {exc!r}")
        return None
