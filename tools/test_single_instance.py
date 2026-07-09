"""Testa a instancia unica (SingleInstance) e o cenario do bug do startup.

BUG COBERTO: no exe PyInstaller o startup leva 10-30s. Antes, a 1a instancia so
respondia ao ack DEPOIS que a janela existia (listen tardio); uma 2a abertura
nesse intervalo nao recebia ack, concluia "travada" e MATAVA a 1a via taskkill
- o app "morria sozinho" no meio do carregamento se o usuario clicasse de novo.
Agora: (1) o listener/ack sobe JA no acquire(); (2) instancia sem ack so e
morta se for VELHA (> _YOUNG_S s, timestamp em instance.pid).

Rodar: python tools/test_single_instance.py  (Windows)
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from camfx.single_instance import (SingleInstance, _read_pid_ts, _write_pid,
                                   _YOUNG_S)


def check(desc, cond):
    print(f"  [{'OK' if cond else 'FALHOU'}] {desc}")
    assert cond, desc


def main():
    if os.name != "nt":
        print("SKIP: SingleInstance so roda no Windows")
        return 0

    # 1) primeira instancia adquire, grava PID+timestamp e JA escuta
    a = SingleInstance()
    check("1a instancia adquire (True)", a.acquire() is True)
    pid, ts = _read_pid_ts()
    check("PID gravado e o nosso", pid == os.getpid())
    check("timestamp gravado (idade conhecida)", ts is not None)
    check("instancia recem-criada e JOVEM (nao seria morta)",
          (time.time() - ts) < _YOUNG_S)

    # 2) CENARIO DO BUG: a 2a abertura chega ANTES do listen() (janela ainda
    #    nao existe). Como o listener sobe no acquire(), a 1a responde ao ack
    #    mesmo sem janela -> a 2a NAO mata e retorna False.
    b = SingleInstance()
    check("2a abertura ANTES do listen: 1a responde ack -> nao assume (False)",
          b.acquire() is False)

    # 3) o pedido de "mostrar janela" feito antes do listen fica pendente e e
    #    entregue quando o listen registrar o callback.
    shown = []
    a.listen(lambda: shown.append(1))
    time.sleep(0.3)
    check("pedido de mostrar feito no startup e entregue no listen",
          len(shown) >= 1)

    # 4) apos o listen, uma nova abertura tambem ve a 1a viva
    c = SingleInstance()
    check("3a abertura (pos-listen) -> nao assume (False)",
          c.acquire() is False)
    time.sleep(0.3)
    check("1a instancia recebeu o novo sinal de mostrar", len(shown) >= 2)

    print("\n>>> TESTE DO SINGLE INSTANCE PASSOU (ack desde o acquire; "
          "jovem nao e morta) <<<")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
