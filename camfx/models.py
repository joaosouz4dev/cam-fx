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
    """Pasta local onde os modelos ficam em cache (LOCALAPPDATA/CamFX/models)."""
    from .config import config_dir
    path = config_dir() / "models"
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


def providers_for(device: str = "auto", kind: str = "blur") -> list[str]:
    """Politica UNICA de escolha GPU/CPU (onnxruntime), com fallback automatico.

    `device`: preferencia do usuario - "auto" | "gpu" | "cpu".
    `kind`:
      - "blur"/"segmentation": pode usar DirectML (roda em qualquer GPU) OU CUDA.
      - "swap"/"detector": NUNCA DirectML - o detector buffalo_l (RetinaFace)
        quebra em DmlExecutionProvider (UnicodeDecodeError no session.run); so
        CUDA ou CPU. Ver o historico do projeto.

    Sempre inclui CPUExecutionProvider no fim: se a GPU falhar em runtime, o ORT
    cai para CPU sozinho. Retorna a lista na ordem de preferencia."""
    if device == "cpu":
        return ["CPUExecutionProvider"]
    try:
        import onnxruntime as ort
        avail = set(ort.get_available_providers())
    except Exception:
        return ["CPUExecutionProvider"]

    gpu: list[str] = []
    if kind in ("swap", "detector", "faceswap"):
        # So CUDA (DirectML quebra o detector).
        if "CUDAExecutionProvider" in avail:
            gpu.append("CUDAExecutionProvider")
    else:
        # blur/segmentation: DirectML primeiro (roda em qualquer GPU), depois CUDA.
        if "DmlExecutionProvider" in avail:
            gpu.append("DmlExecutionProvider")
        if "CUDAExecutionProvider" in avail:
            gpu.append("CUDAExecutionProvider")
    return gpu + ["CPUExecutionProvider"]


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
    # na pasta do exe. Adiciona qualquer pasta que contenha uma DLL do CUDA que
    # o onnxruntime carrega dinamicamente (cudnn/cublas/cufft/curand) - todas
    # dependencias declaradas do onnxruntime-gpu 1.22.
    for root in _bundle_dirs():
        for dll in ("cudnn64_*.dll", "cublas64_*.dll", "cublasLt64_*.dll",
                    "cufft64_*.dll", "curand64_*.dll"):
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


def _faceswap_model_spec(fp16: bool = False, swap_model_id: str | None = None,
                         swap_model_path: str | None = None):
    """Resolve qual modelo de swap deve existir, respeitando a configuracao."""
    if swap_model_id == "custom":
        if not swap_model_path:
            raise FileNotFoundError("Modelo custom de face swap nao configurado.")
        path = Path(swap_model_path)
        if not path.exists() or path.stat().st_size == 0:
            raise FileNotFoundError(f"Modelo custom nao encontrado: {path}")
        return path.name, None, path

    if swap_model_id:
        from .faceswap import registry
        entry = next((e for e in registry.CATALOG
                      if e.kind == "swapper" and e.id == swap_model_id), None)
        if entry is not None:
            return entry.filename, entry.url, models_dir() / entry.filename

    if fp16:
        return "inswapper_128_fp16.onnx", INSWAPPER_FP16_URL, (
            models_dir() / "inswapper_128_fp16.onnx"
        )
    return "inswapper_128.onnx", INSWAPPER_URL, (
        models_dir() / "inswapper_128.onnx"
    )


def ensure_faceswap_models(progress=None, fp16: bool = False,
                           swap_model_id: str | None = None,
                           swap_model_path: str | None = None) -> dict[str, Path]:
    """Baixa os modelos de troca de rosto sob demanda. Retorna nome -> caminho.

    `progress` pode ser (msg: str) ou (recebidos: int, total: int).
    `fp16=True` escolhe o fp16 quando nao ha modelo explicito configurado.
    """
    insightface_home()  # garante a env antes de o insightface carregar
    name, url, dest = _faceswap_model_spec(
        fp16=fp16,
        swap_model_id=swap_model_id,
        swap_model_path=swap_model_path,
    )
    resolved: dict[str, Path] = {}
    if url is not None and (not dest.exists() or dest.stat().st_size == 0):
        if progress:
            progress(f"Baixando o modelo de troca ({name})...")
        _download(url, dest, on_bytes=_bytes_cb(progress, "o modelo de troca"))
    resolved[name] = dest
    resolved["__selected__"] = dest
    # Detector/reconhecedor buffalo_l: baixamos NOS MESMOS (com timeout e
    # feedback), em vez de deixar o insightface baixar silenciosamente ao rodar
    # get_one_face - se aquele download travava, o app ficava preso em
    # "Verificando modelos de IA..." sem timeout nem progresso.
    ensure_buffalo_l(progress)
    return resolved


BUFFALO_L_URL = (
    "https://github.com/deepinsight/insightface/releases/download/v0.7/"
    "buffalo_l.zip"
)


def buffalo_l_dir() -> Path:
    """Pasta onde o insightface REALMENTE procura o buffalo_l.

    ATENCAO: o insightface usa root='~/.insightface' HARDCODED (nao le a env
    INSIGHTFACE_HOME). Entao baixar para LOCALAPPDATA/CamFX nao adianta - o
    insightface ignora e tenta baixar em ~/.insightface (sem timeout, travando
    o app). Baixamos direto onde ele procura."""
    return Path.home() / ".insightface" / "models" / "buffalo_l"


def ensure_buffalo_l(progress=None) -> Path:
    """Garante o detector buffalo_l baixado e extraido. Idempotente.

    O insightface baixaria isto sozinho ao rodar get_one_face, mas sem timeout
    nem feedback - se travava, o app ficava preso. Aqui usamos nosso _download
    (timeout=120) e extraimos o zip. Retorna a pasta do modelo."""
    dest_dir = buffalo_l_dir()
    # Ja extraido? (o pack tem det_10g.onnx entre outros)
    if (dest_dir / "det_10g.onnx").exists():
        return dest_dir
    if progress:
        progress("Baixando o detector de rosto (buffalo_l ~280 MB)...")
    dest_dir.parent.mkdir(parents=True, exist_ok=True)
    zip_path = dest_dir.parent / "buffalo_l.zip"
    try:
        _download(BUFFALO_L_URL, zip_path,
                  on_bytes=_bytes_cb(progress, "o detector de rosto"))
        if progress:
            progress("Extraindo o detector de rosto...")
        import zipfile
        # O zip do insightface pode conter os arquivos na raiz OU dentro de
        # buffalo_l/. Extrai para buffalo_l/ de qualquer forma.
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            top_has_dir = any(n.startswith("buffalo_l/") for n in names)
            target = dest_dir.parent if top_has_dir else dest_dir
            target.mkdir(parents=True, exist_ok=True)
            zf.extractall(target)
    finally:
        try:
            if zip_path.exists():
                zip_path.unlink()
        except Exception:
            pass
    return dest_dir


def ensure_enhancer_model(progress=None) -> Path:
    """Baixa o modelo de melhoria de rosto (GFPGAN) sob demanda."""
    name = next(iter(_ENHANCER_MODELS))
    url = _ENHANCER_MODELS[name]
    dest = models_dir() / name
    if not dest.exists() or dest.stat().st_size == 0:
        if progress:
            progress(f"Baixando o modelo de melhoria ({name})...")
        _download(url, dest, on_bytes=_bytes_cb(progress, "o modelo de melhoria"))
    return dest


def _bytes_cb(progress, label: str = ""):
    """Adapta (recebidos, total) para o callback de status (msg: str).

    Mostra "Baixando <label> X/Y MB (Z%)" - a mensagem que faltava na saga: um
    download lento de 554 MB parecia "travado em Verificando modelos de IA...".
    Com MB/total visiveis, da para ver que esta PROGREDINDO, nao travado.

    Faz throttle: o _download chama isto a cada chunk de 256 KB (~2200 vezes num
    arquivo de 554 MB). Atualizar a UI a cada chunk e desperdicio; so emitimos
    quando o percentual muda OU a cada ~4 MB (quando o total e desconhecido).
    """
    if progress is None:
        return None

    mb = 1024 * 1024
    what = f"{label} " if label else ""
    state = {"pct": -1, "mb": -1}

    def cb(got, total):
        try:
            if total > 0:
                pct = int(got * 100 / total)
                if pct == state["pct"]:
                    return
                state["pct"] = pct
                progress(f"Baixando {what}{got // mb}/{total // mb} MB ({pct}%)")
            else:
                cur = got // (4 * mb)
                if cur == state["mb"]:
                    return
                state["mb"] = cur
                progress(f"Baixando {what}{got // mb} MB")
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
