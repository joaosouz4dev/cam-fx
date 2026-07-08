"""Testa a decisao do demand loop (pipeline_wanted).

Trava a regressao do bug em que ligar o face swap fazia o pipeline ser
derrubado pelo demand loop (0 FPS sem swap), porque o loop so mantinha a camera
viva por preview/consumers e IGNORAVA o face swap.

Rodar: python tools/test_demand_logic.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from camfx.webui import pipeline_wanted


def check(desc, got, expected):
    ok = got == expected
    print(f"  [{'OK' if ok else 'FALHOU'}] {desc}: want={got} (esperado {expected})")
    assert ok, desc


def main():
    print("Cenario do BUG (o que quebrava):")
    # preview desligado, nenhum app consumindo, MAS face swap ligado
    check("preview=OFF, consumers=0, faceswap=ON deve MANTER o pipeline",
          pipeline_wanted(0, False, True), True)

    print("Cenarios que NAO podem regredir:")
    check("preview=ON, sem swap -> liga", pipeline_wanted(0, True, False), True)
    check("app consumindo, sem swap -> liga", pipeline_wanted(1, False, False), True)
    check("nada ligado -> desliga", pipeline_wanted(0, False, False), False)
    check("tudo ligado -> liga", pipeline_wanted(2, True, True), True)

    print("\n>>> TODOS OS TESTES DO DEMAND LOOP PASSARAM <<<")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
