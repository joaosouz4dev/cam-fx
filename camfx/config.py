"""Configuracao persistente do CamFX (JSON na pasta do usuario)."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


def config_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    path = Path(base) / "CamFX"
    path.mkdir(parents=True, exist_ok=True)
    return path


CONFIG_PATH = config_dir() / "config.json"


@dataclass
class Config:
    """Estado salvo entre execucoes."""

    camera_index: int = 0
    # 640x480 abre rapido na maioria das webcams; resolucoes altas podem fazer
    # o backend MSMF do Windows demorar dezenas de segundos para negociar.
    width: int = 1280
    height: int = 720
    fps: int = 30

    blur_enabled: bool = True
    blur_strength: int = 25            # tamanho do kernel gaussiano (impar e >= 3)
    mask_threshold: float = 0.5        # corte da mascara de segmentacao [0..1]
    edge_softness: int = 7             # suavizacao da borda da mascara

    # Processamento: "auto" usa GPU (DirectML) se disponivel, senao CPU.
    # "gpu" forca DirectML; "cpu" forca CPU.
    compute_device: str = "auto"       # auto | gpu | cpu

    # Auto-framing desligado por padrao: ele recorta e amplia (zoom), o que
    # reduz a nitidez vs a webcam crua. O usuario liga se quiser o efeito.
    framing_enabled: bool = False
    framing_zoom: float = 1.4          # zoom maximo do auto-framing
    framing_smoothing: float = 0.9     # 0..1 (quanto maior, mais suave/lento)

    start_minimized: bool = True       # inicia direto na bandeja
    autostart_capture: bool = True     # ja liga a camera virtual ao abrir

    # --- Face swap (troca de rosto em tempo real) ---
    # Recurso pesado e opcional. Desligado por padrao. Os modelos sao baixados
    # sob demanda (centenas de MB) na primeira ativacao.
    faceswap_enabled: bool = False
    source_face_path: str = ""         # caminho da foto do rosto-fonte
    faceswap_enhance: bool = False     # melhora o rosto trocado (custa FPS)
    faceswap_refine: bool = False      # color transfer + mascaras (custa FPS)
    faceswap_backend: str = "dlc"  # "dlc" (motor Deep-Live-Cam) | "insightface"
    faceswap_detect_every: int = 3     # detecta o rosto a cada N frames

    # Modelos selecionaveis (catalogo ou arquivo proprio). Ver faceswap/registry.
    swap_model_id: str = "inswapper_128"   # id do catalogo ou "custom"
    swap_model_path: str = ""              # .onnx proprio quando id == custom
    enhance_model_id: str = ""             # "" / "none" desliga; id ou "custom"
    enhance_model_path: str = ""           # .onnx proprio quando id == custom

    # --- Termos de uso (salvaguarda do face swap) ---
    terms_accepted: bool = False
    terms_version: int = 0             # versao dos termos que o usuario aceitou

    def save(self) -> None:
        CONFIG_PATH.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    @classmethod
    def load(cls) -> "Config":
        if CONFIG_PATH.exists():
            try:
                data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                known = {k: v for k, v in data.items() if k in cls.__annotations__}
                return cls(**known)
            except (json.JSONDecodeError, TypeError):
                pass
        return cls()
