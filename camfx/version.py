"""Versao do CamFX.

A versao real e injetada pelo CI no momento do build (gera o arquivo
`camfx/_version.py` com __version__). Em desenvolvimento, sem esse arquivo,
cai para "0.0.0" (que o updater trata como "sempre desatualizado" para teste
local, mas nunca dispara update porque toda release publicada e maior).

O repositorio das releases tambem fica aqui, para o updater consultar a API
do GitHub.
"""

from __future__ import annotations

GITHUB_OWNER = "joaosouz4dev"
GITHUB_REPO = "cam-fx"


def get_version() -> str:
    try:
        from ._version import __version__  # type: ignore
        return str(__version__)
    except Exception:
        return "0.0.0"


__version__ = get_version()
