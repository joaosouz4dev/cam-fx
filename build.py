"""Gera o executavel CamFX.exe com PyInstaller.

Embute os modelos .tflite (se ja baixados) e os assets do MediaPipe dentro do
.exe, para o app rodar sem depender de internet na primeira execucao.

Uso:
    python build.py
Saida:
    dist/CamFX.exe
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from camfx.models import ensure_models, models_dir


def main() -> int:
    # Garante que os modelos existam para embutir no .exe.
    print("Verificando modelos...")
    ensure_models(progress=print)

    sep = ";" if os.name == "nt" else ":"
    add_data = [
        # modelos -> pasta "models" dentro do bundle
        f"{models_dir() / 'selfie_segmentation.onnx'}{sep}models",
        f"{models_dir() / 'blaze_face_short_range.tflite'}{sep}models",
        # logo/icone -> pasta "assets" dentro do bundle
        f"{Path('assets') / 'logo.png'}{sep}assets",
        f"{Path('assets') / 'icon.ico'}{sep}assets",
        # UI WebView2 (HTML/CSS/JS) -> pasta "ui" dentro do bundle
        f"{Path('camfx') / 'ui'}{sep}ui",
    ]

    # Diretorio de saida: normalmente "dist"; pode ser trocado via env
    # CAMFX_DISTPATH quando o "dist" padrao esta travado (ex.: Explorer aberto
    # na pasta segura o handle e o --clean falha com WinError 32).
    distpath = os.environ.get("CAMFX_DISTPATH", "dist")

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath", distpath,
        # --onedir (NAO --onefile): no modo onefile o exe extrai o Python e as
        # DLLs para uma pasta temporaria %TEMP%\_MEIxxxx a cada execucao e a
        # apaga ao sair. Durante a auto-atualizacao (fecha e reabre o app) isso
        # causava "Failed to load Python DLL" porque a pasta _MEI da instancia
        # antiga era removida enquanto a nova ainda precisava dela. Com --onedir
        # os arquivos ficam soltos na pasta de instalacao, sem extracao temporaria.
        "--onedir",
        "--windowed",          # sem console
        "--name", "CamFX",
        "--icon", str(Path("assets") / "icon.ico"),  # icone do .exe
        # MediaPipe carrega binarios/grafos via arquivos de dados:
        "--collect-all", "mediapipe",
        # ONNX Runtime (DirectML): binarios/DLLs precisam vir completos.
        "--collect-all", "onnxruntime",
        # comtypes gera wrappers COM; precisa vir completo para o pygrabber
        # (captura DirectShow rapida) funcionar dentro do .exe.
        "--collect-all", "comtypes",
        "--collect-submodules", "pygrabber",
        # pywebview (UI WebView2) - traz os backends e dados.
        "--collect-all", "webview",
    ]

    # Face swap: so inclui as deps se estiverem instaladas (o build sem elas
    # gera um CamFX sem face swap, mais leve). Os MODELOS (inswapper, buffalo_l,
    # etc.) NAO sao embutidos: baixam sob demanda para o cache do usuario.
    import importlib.util
    _faceswap_libs = ("insightface", "skimage", "tqdm", "sklearn",
                      "albumentations", "easydict", "prettytable",
                      # deps de rede que o insightface usa para baixar modelos;
                      # sem elas o exe quebra com ModuleNotFoundError urllib3.
                      "requests", "urllib3", "charset_normalizer", "idna",
                      "certifi",
                      # deps do sklearn/skimage/albumentations que o --no-deps
                      # do CI nao instala; sem elas o exe quebra em cascata
                      # (joblib, depois scipy, depois...). Lista obtida
                      # rastreando os imports reais da cadeia de face swap.
                      "joblib", "threadpoolctl", "scipy", "PIL", "yaml",
                      "packaging", "wcwidth", "colorama", "networkx",
                      "lazy_loader", "tifffile", "imageio", "matplotlib",
                      "narwhals", "six", "cffi", "cycler", "fontTools",
                      "kiwisolver", "pyparsing")
    have_faceswap = importlib.util.find_spec("insightface") is not None
    if have_faceswap:
        for collect in _faceswap_libs:
            if importlib.util.find_spec(collect) is not None:
                cmd += ["--collect-all", collect]
        # Motor do Deep-Live-Cam vendorizado. Ele e importado dinamicamente
        # (o loader registra o pacote como 'modules' em runtime), entao o
        # PyInstaller NAO ve a dependencia. Usar --collect-submodules +
        # hidden-imports para incluir os bytecodes.
        # ATENCAO: NAO usar --add-data camfx/vendor -> isso cria uma pasta
        # fisica _internal/camfx/vendor que o Python passa a tratar como o
        # pacote `camfx`, ESCONDENDO os demais modulos (camfx.faceswap,
        # camfx.pipeline...) que ficam no bytecode. Foi o que quebrou o
        # face swap no exe v0.0.12.
        cmd += ["--collect-submodules", "camfx.vendor"]
        for hidden in (
            "camfx.vendor.dlc",
            "camfx.vendor.dlc.modules.globals",
            "camfx.vendor.dlc.modules.face_analyser",
            "camfx.vendor.dlc.modules.processors.frame.face_swapper",
            "camfx.vendor.dlc.modules.processors.frame.core",
            "camfx.vendor.dlc.modules.utilities",
            "camfx.vendor.dlc.modules.cluster_analysis",
            "camfx.vendor.dlc.modules.gpu_processing",
            "camfx.vendor.dlc.modules.typing",
            "camfx.vendor.dlc.modules.core",
        ):
            cmd += ["--hidden-import", hidden]
        # DLLs do CUDA dos pacotes pip nvidia-*: sem elas o CUDAExecutionProvider
        # nao carrega no PC do usuario. --collect-all pega binarios + dados de
        # cada pacote presente. A lista sao as dependencias declaradas do
        # onnxruntime-gpu 1.22 (extras cuda+cudnn): cudnn, cublas (via cudnn),
        # cuda_nvrtc, cuda_runtime, cufft e curand. NAO incluir nvidia.nvjitlink:
        # nao e dependencia declarada de onnxruntime-gpu nem de cudnn 9, entao o
        # find_spec sempre falha (nunca instalado) - seria no-op.
        for nv in ("nvidia.cudnn", "nvidia.cublas", "nvidia.cuda_nvrtc",
                   "nvidia.cuda_runtime", "nvidia.cufft", "nvidia.curand"):
            if importlib.util.find_spec(nv) is not None:
                cmd += ["--collect-all", nv]
        print("Incluindo face swap (insightface + motor DLC + CUDA) no bundle.")

    for entry in add_data:
        cmd += ["--add-data", entry]
    cmd.append("main.py")

    print("Rodando PyInstaller...")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        return result.returncode

    # --onedir gera <distpath>/CamFX/ com o exe e todas as DLLs/dados soltos.
    app_dir = Path(distpath) / "CamFX"
    out = app_dir / ("CamFX.exe" if os.name == "nt" else "CamFX")
    print(f"\nApp: {out.resolve()}")

    # Copia o driver MF e o helper para dentro da pasta do app, onde o
    # instalador os pega (junto do exe e do restante do bundle onedir).
    components = [
        Path("mfref") / "VCamSampleSource" / "x64" / "Release" / "VCamSampleSource.dll",
        Path("mfref") / "VCamSample" / "camfx_vcam.exe",
    ]
    import shutil

    for comp in components:
        if comp.exists():
            shutil.copy2(comp, app_dir / comp.name)
            print(f"Componente: {app_dir}/{comp.name}")
        else:
            print(f"AVISO: componente nao encontrado: {comp} "
                  "(compile o driver MF e o helper antes do instalador).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
