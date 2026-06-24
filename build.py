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

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
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

    # Face swap (branch feat/face-swap): so inclui insightface/skimage se a lib
    # estiver instalada no ambiente. Assim o build da main (sem face swap)
    # continua funcionando sem essas dependencias. Os MODELOS (inswapper,
    # buffalo_l) NAO sao embutidos: baixam sob demanda para o cache do usuario.
    import importlib.util
    for mod, collect in (("insightface", "insightface"), ("skimage", "skimage")):
        if importlib.util.find_spec(mod) is not None:
            cmd += ["--collect-all", collect]
            print(f"Incluindo {collect} no bundle (face swap).")

    for entry in add_data:
        cmd += ["--add-data", entry]
    cmd.append("main.py")

    print("Rodando PyInstaller...")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        return result.returncode

    # --onedir gera dist/CamFX/ com o exe e todas as DLLs/dados soltos.
    app_dir = Path("dist") / "CamFX"
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
