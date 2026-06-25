"""Catalogo e resolucao de modelos de face swap (swappers e enhancers).

Permite ao usuario:
- escolher entre modelos conhecidos (catalogo) e baixa-los sob demanda;
- apontar um .onnx proprio do disco.

A escolha fica na config (swap_model_id / enhance_model_id e os caminhos
custom). O backend usa o que estiver selecionado. Os swappers continuam atras da
interface FaceSwapperBackend, entao trocar a familia do modelo (ex.: reswapper)
e adicionar uma entrada aqui + suporte no backend, sem mexer no resto.

ATENCAO LICENCA: o inswapper (InsightFace) e research-only / nao comercial. O
catalogo marca a licenca de cada modelo em `license` para o app avisar.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models import models_dir


@dataclass
class ModelEntry:
    id: str
    name: str            # rotulo na UI
    kind: str            # "swapper" | "enhancer"
    filename: str        # nome do .onnx no cache
    url: str             # download
    size_mb: int         # aproximado (UI)
    license: str         # ex.: "research" | "free"
    note: str = ""       # observacao curta (UI)


# Mirror estavel do facefusion para os enhancers; HF do inswapper para o swapper.
_FF = "https://huggingface.co/facefusion/models-3.0.0/resolve/main"

CATALOG: list[ModelEntry] = [
    # --- swappers ---
    ModelEntry(
        id="inswapper_128", name="InSwapper 128 (padrao)", kind="swapper",
        filename="inswapper_128.onnx",
        url="https://huggingface.co/ezioruan/inswapper_128.onnx/resolve/main/inswapper_128.onnx",
        size_mb=246, license="research",
        note="Qualidade boa. Licenca apenas pesquisa/nao comercial.",
    ),
    ModelEntry(
        id="inswapper_128_fp16", name="InSwapper 128 fp16 (rapido)", kind="swapper",
        filename="inswapper_128_fp16.onnx",
        url=f"{_FF}/inswapper_128_fp16.onnx",
        size_mb=130, license="research",
        note="Mais leve/rapido na GPU. Licenca apenas pesquisa.",
    ),
    # --- enhancers ---
    ModelEntry(
        id="gfpgan_1.4", name="GFPGAN 1.4", kind="enhancer",
        filename="gfpgan_1.4.onnx", url=f"{_FF}/gfpgan_1.4.onnx",
        size_mb=333, license="free",
        note="Restauracao de rosto classica. Bom custo/beneficio.",
    ),
    ModelEntry(
        id="gpen_bfr_512", name="GPEN-BFR 512", kind="enhancer",
        filename="gpen_bfr_512.onnx", url=f"{_FF}/gpen_bfr_512.onnx",
        size_mb=284, license="free",
        note="Detalhe alto em 512. Recomendado para webcam.",
    ),
    ModelEntry(
        id="gpen_bfr_256", name="GPEN-BFR 256 (leve)", kind="enhancer",
        filename="gpen_bfr_256.onnx", url=f"{_FF}/gpen_bfr_256.onnx",
        size_mb=80, license="free",
        note="Mais rapido, menos detalhe.",
    ),
    ModelEntry(
        id="codeformer", name="CodeFormer", kind="enhancer",
        filename="codeformer.onnx", url=f"{_FF}/codeformer.onnx",
        size_mb=359, license="free",
        note="Restauracao forte; pode suavizar demais.",
    ),
]

# id especial para "usar um .onnx proprio do disco".
CUSTOM_ID = "custom"


def _by_id(model_id: str) -> ModelEntry | None:
    for e in CATALOG:
        if e.id == model_id:
            return e
    return None


def list_models(kind: str) -> list[ModelEntry]:
    return [e for e in CATALOG if e.kind == kind]


def is_downloaded(entry: ModelEntry) -> bool:
    p = models_dir() / entry.filename
    return p.exists() and p.stat().st_size > 0


def path_for(entry: ModelEntry):
    return models_dir() / entry.filename


def download(entry: ModelEntry, progress=None):
    """Baixa o modelo do catalogo (reusa o downloader de models.py)."""
    from ..models import _download, _bytes_cb
    dest = models_dir() / entry.filename
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    _download(entry.url, dest, on_bytes=_bytes_cb(progress))
    return dest


def resolve_swapper(config) -> str | None:
    """Caminho do .onnx do swapper escolhido na config (ou None se ausente).

    Usa o arquivo proprio se swap_model_id == 'custom' e ha caminho; senao o
    modelo do catalogo (se ja baixado).
    """
    mid = getattr(config, "swap_model_id", "inswapper_128")
    if mid == CUSTOM_ID:
        p = getattr(config, "swap_model_path", "")
        return p or None
    entry = _by_id(mid) or _by_id("inswapper_128")
    if entry and is_downloaded(entry):
        return str(path_for(entry))
    return None


def resolve_enhancer(config) -> str | None:
    """Caminho do .onnx do enhancer escolhido, ou None se 'nenhum'/ausente."""
    mid = getattr(config, "enhance_model_id", "")
    if not mid or mid == "none":
        return None
    if mid == CUSTOM_ID:
        p = getattr(config, "enhance_model_path", "")
        return p or None
    entry = _by_id(mid)
    if entry and is_downloaded(entry):
        return str(path_for(entry))
    return None
