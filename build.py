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
        f"{models_dir() / 'selfie_segmenter.tflite'}{sep}models",
        f"{models_dir() / 'blaze_face_short_range.tflite'}{sep}models",
    ]

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",          # sem console
        "--name", "CamFX",
        # MediaPipe carrega binarios/grafos via arquivos de dados:
        "--collect-all", "mediapipe",
    ]
    for entry in add_data:
        cmd += ["--add-data", entry]
    cmd.append("main.py")

    print("Rodando PyInstaller...")
    result = subprocess.run(cmd)
    if result.returncode == 0:
        out = Path("dist") / ("CamFX.exe" if os.name == "nt" else "CamFX")
        print(f"\nPronto: {out.resolve()}")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
