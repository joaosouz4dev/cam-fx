"""Interface abstrata do face swap.

Esta e a fronteira que isola a escolha de modelo/licenca. O pipeline e a UI
dependem so destas assinaturas; o backend concreto (insightface, ou outro no
futuro) implementa os detalhes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np


@dataclass
class SwapResult:
    """Resultado de uma troca. `frame` e a imagem BGR com o rosto trocado;
    `swapped` indica se algum rosto foi de fato substituido."""
    frame: np.ndarray
    swapped: bool


class FaceSwapperBackend(ABC):
    """Backend de troca de rosto. Implementacoes carregam os modelos no __init__
    (ja com os arquivos baixados) e expoem prepare_source/swap_frame."""

    @staticmethod
    @abstractmethod
    def available_devices() -> list[str]:
        """Dispositivos suportados, ex.: ['gpu', 'cpu'] ou ['cpu']."""

    @abstractmethod
    def prepare_source(self, image_bgr: np.ndarray) -> Optional[Any]:
        """Extrai e cacheia o rosto-fonte de uma foto (BGR). Retorna um handle
        opaco (usado em swap_frame) ou None se nenhum rosto for encontrado."""

    @abstractmethod
    def swap_frame(
        self,
        frame_bgr: np.ndarray,
        source: Any,
        *,
        detect: bool = True,
    ) -> SwapResult:
        """Troca o(s) rosto(s) do frame pelo rosto-fonte.

        `detect=False` permite ao chamador pedir que o backend reuse a ultima
        deteccao (otimizacao de FPS); backends que nao suportam podem ignorar.
        """

    def close(self) -> None:
        """Libera modelos/sessoes. Idempotente."""
