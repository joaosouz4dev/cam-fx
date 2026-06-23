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
    ]

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
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
    ]
    for entry in add_data:
        cmd += ["--add-data", entry]
    cmd.append("main.py")

    print("Rodando PyInstaller...")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        return result.returncode

    out = Path("dist") / ("CamFX.exe" if os.name == "nt" else "CamFX")
    print(f"\nApp: {out.resolve()}")

    # Copia o driver MF e o helper para dist/, onde o instalador os pega.
    components = [
        Path("mfref") / "VCamSampleSource" / "x64" / "Release" / "VCamSampleSource.dll",
        Path("mfref") / "VCamSample" / "camfx_vcam.exe",
    ]
    import shutil

    for comp in components:
        if comp.exists():
            shutil.copy2(comp, Path("dist") / comp.name)
            print(f"Componente: dist/{comp.name}")
        else:
            print(f"AVISO: componente nao encontrado: {comp} "
                  "(compile o driver MF e o helper antes do instalador).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
