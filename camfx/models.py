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

# --- Modelos de face swap (baixados SOB DEMANDA, fora do instalador) ---
# Sao grandes (centenas de MB) e tem licenca apenas-pesquisa (inswapper).
# So baixam quando o usuario ativa a troca de rosto.
INSWAPPER_URL = (
    "https://huggingface.co/ezioruan/inswapper_128.onnx/"
    "resolve/main/inswapper_128.onnx"
)
# Versao fp16 (meia precisao) - usada em CUDA pelo motor DLC, mais rapida.
INSWAPPER_FP16_URL = (
    "https://huggingface.co/hacksider/deep-live-cam/"
    "resolve/main/inswapper_128_fp16.onnx"
)
# GFPGAN em ONNX (melhora/restaura o rosto trocado), opcional.
GFPGAN_URL = (
    "https://huggingface.co/facefusion/models/"
    "resolve/main/gfpgan_1.4.onnx"
)

_FACESWAP_MODELS = {
    "inswapper_128.onnx": INSWAPPER_URL,
}
# O detector/reconhecedor (buffalo_l) e baixado pelo proprio insightface para
# INSIGHTFACE_HOME (apontado para models_dir() em insightface_home()).

_ENHANCER_MODELS = {
    "gfpgan_1.4.onnx": GFPGAN_URL,
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


def enable_cuda_dlls() -> bool:
    """Coloca as DLLs do CUDA/cuDNN (pacotes pip nvidia-*) no PATH.

    O onnxruntime-gpu so acha o CUDAExecutionProvider se as DLLs do cuDNN/cuBLAS
    estiverem no PATH (alem de add_dll_directory, por causa de dependencias
    transitivas como cudnn64_9.dll). Sem isso, cai silenciosamente para CPU.
    Idempotente; retorna True se encontrou as pastas. Deve rodar ANTES de
    importar/usar o onnxruntime para CUDA.
    """
    import glob
    found = []
    # 1) Dev: pacotes pip nvidia-* em site-packages/nvidia/*/bin.
    for sp in _site_packages_dirs():
        base = sp / "nvidia"
        if base.exists():
            found += glob.glob(str(base / "*" / "bin"))
    # 2) Exe empacotado (PyInstaller): as DLLs coletadas ficam no _MEIPASS e/ou
    # na pasta do exe. Adiciona qualquer pasta que contenha cudnn/cublas.
    for root in _bundle_dirs():
        for dll in ("cudnn64_*.dll", "cublas64_*.dll", "cublasLt64_*.dll"):
            for hit in glob.glob(str(root / "**" / dll), recursive=True):
                d = str(Path(hit).parent)
                if d not in found:
                    found.append(d)
    if not found:
        return False
    os.environ["PATH"] = os.pathsep.join(found) + os.pathsep + os.environ.get("PATH", "")
    for d in found:
        try:
            os.add_dll_directory(d)
        except Exception:
            pass
    return True


def _bundle_dirs() -> list[Path]:
    dirs = []
    if hasattr(sys, "_MEIPASS"):
        dirs.append(Path(sys._MEIPASS))  # type: ignore[attr-defined]
    if getattr(sys, "frozen", False):
        dirs.append(Path(sys.executable).resolve().parent)
    return dirs


def _site_packages_dirs() -> list[Path]:
    dirs = []
    try:
        import site
        for p in site.getsitepackages():
            dirs.append(Path(p))
        u = site.getusersitepackages()
        if u:
            dirs.append(Path(u))
    except Exception:
        pass
    # fallback: a pasta do proprio numpy/onnxruntime ja instalado
    try:
        import onnxruntime
        dirs.append(Path(onnxruntime.__file__).resolve().parent.parent)
    except Exception:
        pass
    return dirs


def insightface_home() -> Path:
    """Pasta onde o insightface guarda o buffalo_l (detector/recognition).

    Apontamos para dentro do nosso cache para controle total (importante no
    .exe). O insightface le a env INSIGHTFACE_HOME e espera os modelos em
    <home>/models/<nome>.
    """
    home = models_dir().parent / "insightface"
    (home / "models").mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("INSIGHTFACE_HOME", str(home))
    return home


def ensure_faceswap_models(progress=None, fp16: bool = False) -> dict[str, Path]:
    """Baixa os modelos de troca de rosto sob demanda. Retorna nome -> caminho.

    `progress` pode ser (msg: str) ou (recebidos: int, total: int).
    `fp16=True` tambem garante o inswapper_128_fp16.onnx (motor DLC em CUDA).
    """
    insightface_home()  # garante a env antes de o insightface carregar
    to_get = dict(_FACESWAP_MODELS)
    if fp16:
        to_get["inswapper_128_fp16.onnx"] = INSWAPPER_FP16_URL
    resolved: dict[str, Path] = {}
    for name, url in to_get.items():
        dest = models_dir() / name
        if not dest.exists() or dest.stat().st_size == 0:
            if progress:
                progress(f"Baixando {name}...")
            _download(url, dest, on_bytes=_bytes_cb(progress))
        resolved[name] = dest
    return resolved


def ensure_enhancer_model(progress=None) -> Path:
    """Baixa o modelo de melhoria de rosto (GFPGAN) sob demanda."""
    name = next(iter(_ENHANCER_MODELS))
    url = _ENHANCER_MODELS[name]
    dest = models_dir() / name
    if not dest.exists() or dest.stat().st_size == 0:
        if progress:
            progress(f"Baixando {name}...")
        _download(url, dest, on_bytes=_bytes_cb(progress))
    return dest


def _bytes_cb(progress):
    """Adapta um callback de progresso para receber (recebidos, total)."""
    if progress is None:
        return None

    def cb(got, total):
        try:
            if total > 0:
                progress(f"Baixando... {int(got * 100 / total)}%")
            else:
                progress(f"Baixando... {got // (1024 * 1024)} MB")
        except Exception:
            pass

    return cb


def _download(url: str, dest: Path, on_bytes=None) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "CamFX/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp, open(tmp, "wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        got = 0
        while True:
            chunk = resp.read(256 * 1024)
            if not chunk:
                break
            out.write(chunk)
            got += len(chunk)
            if on_bytes:
                on_bytes(got, total)
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
