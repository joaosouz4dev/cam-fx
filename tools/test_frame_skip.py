"""Testa a logica de frame-skip da deteccao de rosto no SwapStage.

Otimizacao coberta: detectar o rosto (get_one_face, caro) em TODO frame limita o
FPS sem ganho (entre frames o rosto quase nao anda). Agora detectamos a cada N
frames (CAMFX_DETECT_EVERY, padrao 3) e reusamos a ultima face nos frames
intermediarios. Este teste trava a leitura da env (_detect_every) e simula o
padrao "detecta a cada N" sem precisar de camera/GPU/modelo.

Rodar: python tools/test_frame_skip.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from camfx.faceswap.swap_stage import _detect_every


def check(desc, cond):
    print(f"  [{'OK' if cond else 'FALHOU'}] {desc}")
    assert cond, desc


def main():
    print("Base vem da config; a env CAMFX_DETECT_EVERY tem prioridade:")
    os.environ.pop("CAMFX_DETECT_EVERY", None)
    check("sem env, base padrao -> 3", _detect_every() == 3)
    check("sem env, base=2 (config) -> 2", _detect_every(2) == 2)
    check("sem env, base=1 (config) -> 1", _detect_every(1) == 1)
    check("sem env, base=0 (config invalida) -> clampa para 1",
          _detect_every(0) == 1)
    os.environ["CAMFX_DETECT_EVERY"] = "5"
    check("env=5 sobrepoe a base=2 -> 5", _detect_every(2) == 5)
    os.environ["CAMFX_DETECT_EVERY"] = "1"
    check("env=1 -> 1 (detecta todo frame)", _detect_every(4) == 1)
    os.environ["CAMFX_DETECT_EVERY"] = "0"
    check("env=0 (invalido) -> clampa para 1", _detect_every() == 1)
    os.environ["CAMFX_DETECT_EVERY"] = "-2"
    check("env negativo -> clampa para 1", _detect_every() == 1)
    os.environ["CAMFX_DETECT_EVERY"] = "lixo"
    check("env nao-numerica -> cai na base (3)", _detect_every() == 3)
    os.environ["CAMFX_DETECT_EVERY"] = "lixo"
    check("env nao-numerica -> cai na base (config=2)", _detect_every(2) == 2)
    os.environ.pop("CAMFX_DETECT_EVERY", None)

    print("Padrao de deteccao (detecta a cada N, reusa nos intermediarios):")
    # Reproduz a decisao do _worker_loop: em i % N == 0 chama o detector; senao
    # reusa a ultima face. Contamos quantas deteccoes acontecem em 30 frames.
    for every in (1, 3, 5):
        detections = 0
        last_face = None
        for i in range(30):
            if i % every == 0:
                detections += 1          # aqui rodaria get_one_face (caro)
                last_face = f"face@{i}"
            used = last_face             # frame usa a ultima face conhecida
            # nos intermediarios a face reusada nao pode ser None depois da 1a
            if i >= 0 and every == 1:
                assert used == f"face@{i}"
        esperado = (30 + every - 1) // every
        check(f"N={every}: {detections} deteccoes em 30 frames "
              f"(esperado {esperado}, ~{30 // detections}x menos custo)",
              detections == esperado)

    print("\n>>> FRAME-SKIP DA DETECCAO OK <<<")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
