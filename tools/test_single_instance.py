"""Testa a deteccao de instancia travada (zumbi) no SingleInstance.

Cenario do bug: uma atualizacao deixa o CamFX antigo preso (segura o mutex mas
nao responde). Sem a correcao, a versao nova desistia de rodar e o usuario
testava a versao velha. Agora a nova detecta a travada e assume.

Rodar: python tools/test_single_instance.py  (Windows)
"""
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from camfx.single_instance import SingleInstance, _read_pid


def check(desc, cond):
    print(f"  [{'OK' if cond else 'FALHOU'}] {desc}")
    assert cond, desc


def main():
    if os.name != "nt":
        print("SKIP: SingleInstance so roda no Windows")
        return 0

    # 1) primeira instancia adquire e grava o PID
    a = SingleInstance()
    check("1a instancia adquire (True)", a.acquire() is True)
    check("PID gravado e o nosso", _read_pid() == os.getpid())

    # 2) instancia primaria escutando -> a segunda deve VER que ela responde
    #    (nao mata) e retornar False.
    shown = []
    a.listen(lambda: shown.append(1))
    time.sleep(0.2)  # da tempo da thread de listen subir

    b = SingleInstance()
    # como 'a' esta viva e escutando, b deve receber o ack e NAO assumir
    got = b.acquire()
    check("2a instancia ve a 1a viva -> nao assume (False)", got is False)
    time.sleep(0.2)
    check("a 1a instancia recebeu o sinal de mostrar janela", len(shown) >= 1)

    print("\n>>> TESTE DO SINGLE INSTANCE PASSOU (deteccao viva/ack ok) <<<")
    print("    (o caminho 'zumbi -> mata e assume' exige um processo real")
    print("     travado; validado manualmente no app.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
