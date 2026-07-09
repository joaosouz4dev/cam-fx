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
    print("A camera so fica ligada se ALGUEM usa (preview ou consumer):")
    # BUG que corrigimos: com o swap ligado, preview OFF e sem consumer, a
    # camera ficava "gravando" (LED aceso) a toa. O face swap e config, nao
    # demanda - nao deve manter a camera ligada sozinho.
    check("preview=OFF, consumers=0, faceswap=ON -> DESLIGA (nao grava a toa)",
          pipeline_wanted(0, False, True), False)

    print("Cenarios que devem manter a camera ligada:")
    check("preview=ON -> liga", pipeline_wanted(0, True, False), True)
    check("preview=ON + swap -> liga", pipeline_wanted(0, True, True), True)
    check("app consumindo a CamFX -> liga", pipeline_wanted(1, False, False), True)
    check("app consumindo + swap -> liga", pipeline_wanted(1, False, True), True)
    check("nada usando -> desliga", pipeline_wanted(0, False, False), False)

    print("\n>>> TODOS OS TESTES DO DEMAND LOOP PASSARAM <<<")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
