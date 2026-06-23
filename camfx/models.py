"""Download e cache dos modelos .tflite do MediaPipe.

Os modelos nao sao versionados no repositorio. Na primeira execucao eles sao
baixados para a pasta de dados do usuario e reaproveitados depois.
"""

from __future__ import annotations

import os
import sys
import urllib.request
from pathlib import Path

# URLs oficiais dos modelos MediaPipe Tasks.
SELFIE_SEGMENTER_URL = (
    "https://storage.googleapis.com/mediapipe-models/image_segmenter/"
    "selfie_segmenter/float16/latest/selfie_segmenter.tflite"
)
FACE_DETECTOR_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_detector/"
    "blaze_face_short_range/float16/latest/blaze_face_short_range.tflite"
)
# Selfie segmentation em ONNX (roda via ONNX Runtime, GPU DirectML ou CPU).
SELFIE_ONNX_URL = (
    "https://huggingface.co/onnx-community/mediapipe_selfie_segmentation/"
    "resolve/main/onnx/model.onnx"
)

_MODELS = {
    "selfie_segmentation.onnx": SELFIE_ONNX_URL,
    "blaze_face_short_range.tflite": FACE_DETECTOR_URL,
}


def models_dir() -> Path:
    """Pasta local onde os modelos ficam em cache."""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    path = Path(base) / "CamFX" / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_models(progress=None) -> dict[str, Path]:
    """Garante que todos os modelos existam localmente, baixando o que faltar.

    `progress` e um callback opcional (mensagem: str) para feedback na UI.
    Retorna um dict nome -> caminho absoluto.
    """
    resolved: dict[str, Path] = {}
    for name, url in _MODELS.items():
        dest = models_dir() / name
        if not dest.exists() or dest.stat().st_size == 0:
            if progress:
                progress(f"Baixando {name}...")
            _download(url, dest)
        resolved[name] = dest
    return resolved


def _download(url: str, dest: Path) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "CamFX/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, "wb") as out:
        while True:
            chunk = resp.read(64 * 1024)
            if not chunk:
                break
            out.write(chunk)
    tmp.replace(dest)


def bundled_or_cached(name: str) -> Path:
    """Resolve o caminho de um modelo, preferindo a versao embutida no .exe.

    PyInstaller extrai os dados em sys._MEIPASS; se o modelo estiver la, usamos
    direto sem rede. Caso contrario caimos no cache do usuario.
    """
    if hasattr(sys, "_MEIPASS"):
        bundled = Path(sys._MEIPASS) / "models" / name  # type: ignore[attr-defined]
        if bundled.exists():
            return bundled
    return models_dir() / name
