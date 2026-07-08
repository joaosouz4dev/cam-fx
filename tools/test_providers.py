"""Testa a politica unica de escolha GPU/CPU (models.providers_for).

Garante as invariantes que custaram caro:
- swap/detector NUNCA usa DirectML (buffalo_l quebra em DML).
- sempre ha CPUExecutionProvider no fim (fallback automatico).
- device="cpu" forca so CPU.

Rodar: python tools/test_providers.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from camfx.models import providers_for


def check(desc, cond):
    print(f"  [{'OK' if cond else 'FALHOU'}] {desc}")
    assert cond, desc


def main():
    for kind in ("swap", "detector", "blur", "segmentation"):
        p = providers_for("gpu", kind=kind)
        check(f"{kind}: termina em CPU (fallback)", p[-1] == "CPUExecutionProvider")
        p_cpu = providers_for("cpu", kind=kind)
        check(f"{kind}: device=cpu -> so CPU", p_cpu == ["CPUExecutionProvider"])

    # swap/detector nunca podem usar DirectML (quebra o buffalo_l)
    for kind in ("swap", "detector", "faceswap"):
        p = providers_for("gpu", kind=kind)
        check(f"{kind}: NUNCA DirectML", "DmlExecutionProvider" not in p)

    print("\n>>> POLITICA DE PROVIDERS OK <<<")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
