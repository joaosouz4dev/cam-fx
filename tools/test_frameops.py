"""Testa as operacoes de frame puras (frameops), extraidas do pipeline.py.

fit_aspect corta o excesso (crop central) para casar o aspecto SEM esticar, e
so entao redimensiona - o bug que deixava o rosto alongado (4:3 -> 16:9).
fix_blue_cast neutraliza o tom azulado do DirectShow sem estourar shapes.

Rodar: python tools/test_frameops.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from camfx.frameops import fit_aspect, fix_blue_cast


def check(desc, cond):
    print(f"  [{'OK' if cond else 'FALHOU'}] {desc}")
    assert cond, desc


def main():
    print("fit_aspect: saida sempre no tamanho pedido, sem esticar:")
    # 4:3 (1280x960) -> 16:9 (1280x720): deve cortar topo/base, nao esticar.
    src43 = np.zeros((960, 1280, 3), dtype=np.uint8)
    out = fit_aspect(src43, 1280, 720)
    check("4:3 -> 16:9 sai 1280x720", out.shape[:2] == (720, 1280))

    # ja no aspecto certo, tamanho diferente: so redimensiona.
    src169 = np.zeros((360, 640, 3), dtype=np.uint8)
    out = fit_aspect(src169, 1280, 720)
    check("16:9 menor -> 1280x720", out.shape[:2] == (720, 1280))

    # fonte mais larga que a saida (21:9 -> 16:9): corta as laterais.
    srcwide = np.zeros((720, 1680, 3), dtype=np.uint8)
    out = fit_aspect(srcwide, 1280, 720)
    check("ultrawide -> 1280x720", out.shape[:2] == (720, 1280))

    # ja exatamente no tamanho: passa igual.
    exact = np.zeros((720, 1280, 3), dtype=np.uint8)
    out = fit_aspect(exact, 1280, 720)
    check("ja 1280x720 -> mantem 1280x720", out.shape[:2] == (720, 1280))

    print("fix_blue_cast: preserva shape/dtype e nao estoura:")
    # imagem azulada (B alto, R baixo): a correcao deve aproximar os canais.
    img = np.zeros((100, 100, 3), dtype=np.uint8)
    img[:, :, 0] = 200  # B
    img[:, :, 1] = 120  # G
    img[:, :, 2] = 80   # R
    out = fix_blue_cast(img)
    check("mantem shape (100,100,3)", out.shape == (100, 100, 3))
    check("mantem dtype uint8", out.dtype == np.uint8)
    check("valores dentro de [0,255]", out.min() >= 0 and out.max() <= 255)
    # depois da correcao gray-world, a diferenca media B-R deve encolher.
    before = float(img[:, :, 0].mean()) - float(img[:, :, 2].mean())
    after = float(out[:, :, 0].mean()) - float(out[:, :, 2].mean())
    check("reduz o excesso de azul (B-R menor)", after < before)

    print("\n>>> FRAMEOPS OK <<<")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
