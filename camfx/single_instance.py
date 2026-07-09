"""Instancia unica do CamFX.

Garante que apenas uma instancia do app rode. Se o usuario abrir de novo, o
segundo processo sinaliza o primeiro (que traz a janela para frente) e sai.

Mecanismo: um named mutex detecta a instancia ja existente; um named event serve
de sinal "mostre a janela" e outro de ACK ("estou vivo"). CRITICO: a primeira
instancia comeca a escutar/responder JA NO acquire() (nao espera a janela
existir) - no exe PyInstaller o startup leva 10-30s, e se o listener so subisse
com a janela, uma segunda abertura nesse intervalo nao recebia ack, concluia
"travada" e MATAVA a instancia que estava inicializando (taskkill). Era a causa
de o app "morrer sozinho" no meio do carregamento quando o usuario clicava de
novo no exe.

ZUMBI: se a instancia anterior TRAVOU de verdade (nao responde ao ack), o novo
processo so a mata se ela for VELHA (viva ha mais de _YOUNG_S segundos, lido do
timestamp gravado em instance.pid). Instancia jovem = provavelmente ainda
inicializando -> nao mata; o novo processo sai e deixa a primeira terminar."""

from __future__ import annotations

import ctypes
import os
import threading
import time
from ctypes import wintypes
from pathlib import Path

_MUTEX_NAME = "Local\\CamFX_SingleInstance_Mutex"
_EVENT_NAME = "Local\\CamFX_ShowWindow_Event"
_ACK_EVENT_NAME = "Local\\CamFX_ShowWindow_Ack"

# Idade minima (s) para considerar uma instancia sem-ack como TRAVADA de
# verdade. Abaixo disso ela provavelmente ainda esta inicializando (o exe
# PyInstaller leva 10-30s ate a janela) e NAO deve ser morta.
_YOUNG_S = 90

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
ERROR_ALREADY_EXISTS = 183

_kernel32.CreateMutexW.restype = wintypes.HANDLE
_kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
_kernel32.CreateEventW.restype = wintypes.HANDLE
_kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
_kernel32.OpenEventW.restype = wintypes.HANDLE
_kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
_kernel32.SetEvent.argtypes = [wintypes.HANDLE]
_kernel32.SetEvent.restype = wintypes.BOOL
_kernel32.ResetEvent.argtypes = [wintypes.HANDLE]
_kernel32.WaitForSingleObject.restype = wintypes.DWORD
_kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

EVENT_MODIFY_STATE = 0x0002
WAIT_OBJECT_0 = 0x0
WAIT_TIMEOUT = 0x102
INFINITE = 0xFFFFFFFF


def _pid_file() -> Path:
    from .config import data_file
    return data_file("instance.pid")


def _write_pid() -> None:
    """Grava "<pid> <timestamp>" - o timestamp permite saber a idade da
    instancia (para nao matar uma que ainda esta inicializando)."""
    try:
        p = _pid_file()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"{os.getpid()} {int(time.time())}", encoding="utf-8")
    except Exception:
        pass


def _read_pid_ts() -> tuple[int | None, int | None]:
    try:
        parts = _pid_file().read_text(encoding="utf-8").split()
        pid = int(parts[0])
        ts = int(parts[1]) if len(parts) > 1 else None
        return pid, ts
    except Exception:
        return None, None


def _kill_pid(pid: int) -> None:
    """Mata o processo antigo travado (usa taskkill /F para matar a arvore)."""
    try:
        import subprocess
        subprocess.run(
            ["taskkill", "/F", "/PID", str(pid)],
            capture_output=True, timeout=5,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
    except Exception:
        pass


class SingleInstance:
    def __init__(self):
        self._mutex = None
        self._event = None
        self._ack = None
        self._on_show = None
        self._show_pending = False
        self.is_first = False

    def acquire(self) -> bool:
        """True se esta e a primeira instancia; False se ja havia outra.

        A primeira instancia comeca a responder ao ack IMEDIATAMENTE (listener
        proprio), antes mesmo da janela existir. Uma instancia existente que nao
        responde so e morta se for VELHA (> _YOUNG_S s de vida)."""
        self._mutex = _kernel32.CreateMutexW(None, False, _MUTEX_NAME)
        already = ctypes.get_last_error() == ERROR_ALREADY_EXISTS
        if not already:
            self.is_first = True
            _write_pid()
            self._start_listener()
            return True

        # Ja ha uma instancia. Ela responde? Sinaliza e espera o ack.
        if self._existing_responds():
            self.is_first = False
            return False

        # Nao respondeu. So mata se for VELHA (travada de verdade). Uma
        # instancia jovem provavelmente ainda esta inicializando (o listener
        # dela ja deveria existir com este codigo, mas versoes antigas ou um
        # arranque muito cedo merecem o benefit of the doubt).
        pid, ts = _read_pid_ts()
        age = (time.time() - ts) if ts else None
        if pid and pid != os.getpid() and (age is None or age > _YOUNG_S):
            _kill_pid(pid)
            time.sleep(0.5)  # da tempo do SO liberar o mutex/camera
            self.is_first = True
            _write_pid()
            self._start_listener()
            return True

        # Instancia jovem sem ack: NAO mata. Este processo sai e deixa a
        # primeira terminar de subir.
        self.is_first = False
        return False

    def _start_listener(self) -> None:
        """Sobe a escuta do sinal "mostrar janela" + resposta de ack JA. Se a
        janela ainda nao existe quando o sinal chega, marca pendencia e o
        listen() posterior mostra a janela assim que registrada."""
        self._event = _kernel32.CreateEventW(None, False, False, _EVENT_NAME)
        if not self._event:
            return
        self._ack = _kernel32.CreateEventW(None, False, False, _ACK_EVENT_NAME)

        def loop():
            while True:
                r = _kernel32.WaitForSingleObject(self._event, INFINITE)
                if r == WAIT_OBJECT_0:
                    # Responde primeiro (prova de vida), depois mostra.
                    if self._ack:
                        _kernel32.SetEvent(self._ack)
                    cb = self._on_show
                    if cb is not None:
                        try:
                            cb()
                        except Exception:
                            pass
                    else:
                        self._show_pending = True

        threading.Thread(target=loop, daemon=True).start()

    def _existing_responds(self, timeout_ms: int = 3000) -> bool:
        """Sinaliza a instancia existente e espera o ack. True se respondeu."""
        ack = _kernel32.CreateEventW(None, False, False, _ACK_EVENT_NAME)
        if ack:
            _kernel32.ResetEvent(ack)
        h = _kernel32.OpenEventW(EVENT_MODIFY_STATE, False, _EVENT_NAME)
        if not h:
            # O evento ainda nao existe: a instancia nao chegou a criar o
            # listener. NAO significa necessariamente travada (pode ser um
            # arranque muito precoce) - a decisao de matar fica com a idade.
            if ack:
                _kernel32.CloseHandle(ack)
            return False
        _kernel32.SetEvent(h)
        _kernel32.CloseHandle(h)
        responded = False
        if ack:
            r = _kernel32.WaitForSingleObject(ack, timeout_ms)
            responded = (r == WAIT_OBJECT_0)
            _kernel32.CloseHandle(ack)
        return responded

    def signal_existing(self) -> None:
        """Pede a instancia ja aberta para mostrar a janela (chamado pelo 2o)."""
        h = _kernel32.OpenEventW(EVENT_MODIFY_STATE, False, _EVENT_NAME)
        if h:
            _kernel32.SetEvent(h)
            _kernel32.CloseHandle(h)

    def listen(self, on_show) -> None:
        """Registra o callback de "mostrar janela". O listener ja esta rodando
        desde o acquire(); aqui so conectamos a janela (e atendemos um pedido
        que tenha chegado durante o startup)."""
        self._on_show = on_show
        if self._show_pending:
            self._show_pending = False
            try:
                on_show()
            except Exception:
                pass
