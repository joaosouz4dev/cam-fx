"""Pacote de troca de rosto (face swap) do CamFX.

A barreira de licenca/modelo fica concentrada nos backends concretos
(ex.: insightface_backend). Todo o resto do app fala apenas com a interface
abstrata FaceSwapperBackend, entao trocar o modelo depois (ex.: por um de
licenca comercial) e escrever um novo backend, sem tocar no pipeline/UI.
"""

from __future__ import annotations

from .base import FaceSwapperBackend, SwapResult


def load_swapper(backend: str, device: str = "auto", enhance: bool = False,
                 swap_model_path: str | None = None,
                 enhance_model_path: str | None = None,
                 refine: bool = False):
    """Factory: instancia o backend de face swap escolhido.

    `backend`: "insightface" (padrao). Novos backends adicionam-se aqui.
    `device`: "auto" | "gpu" | "cpu".
    `enhance`: liga a melhoria de rosto, se o backend suportar.
    `swap_model_path`/`enhance_model_path`: .onnx selecionados (catalogo/proprio).
    """
    name = (backend or "dlc").lower()
    if name == "dlc":
        # Motor do Deep-Live-Cam (vendorizado) - melhor qualidade e FPS.
        from .dlc_backend import DLCSwapper
        return DLCSwapper(device=device)
    if name == "insightface":
        from .insightface_backend import InsightFaceSwapper
        return InsightFaceSwapper(
            device=device, enhance=enhance,
            swap_model_path=swap_model_path,
            enhance_model_path=enhance_model_path,
            refine=refine,
        )
    raise ValueError(f"backend de face swap desconhecido: {backend!r}")


def available_backends() -> list[str]:
    return ["insightface"]


__all__ = [
    "FaceSwapperBackend",
    "SwapResult",
    "load_swapper",
    "available_backends",
]
