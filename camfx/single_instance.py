"""Instancia unica do CamFX.

Garante que apenas uma instancia do app rode. Se o usuario abrir de novo, o
segundo processo sinaliza o primeiro (que traz a janela para frente) e sai.

Mecanismo: um named mutex detecta a instancia ja existente; um named event serve
de sinal "mostre a janela". O primeiro processo cria o mutex, grava seu PID e
fica escutando o evento numa thread; o segundo dispara o evento e encerra.

ZUMBI: se a instancia anterior travou (segura o mutex mas nao responde ao
evento), o segundo processo espera um "ack"; se nao vier, MATA a instancia
travada pelo PID e assume o lugar. Sem isto, uma atualizacao que deixe o app
antigo preso faria a versao nova desistir de rodar (o usuario testava a versao
velha achando que a nova nao funcionava)."""

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
SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0x0
WAIT_TIMEOUT = 0x102
INFINITE = 0xFFFFFFFF


def _pid_file() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return Path(base) / "CamFX" / "instance.pid"


def _write_pid() -> None:
    try:
        p = _pid_file()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(os.getpid()), encoding="utf-8")
    except Exception:
        pass


def _read_pid() -> int | None:
    try:
        return int(_pid_file().read_text(encoding="utf-8").strip())
    except Exception:
        return None


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
        self.is_first = False

    def acquire(self) -> bool:
        """True se esta e a primeira instancia; False se ja havia outra.

        Se ja havia outra mas ela esta travada (nao responde ao sinal),
        mata-a e assume o lugar, retornando True."""
        self._mutex = _kernel32.CreateMutexW(None, False, _MUTEX_NAME)
        already = ctypes.get_last_error() == ERROR_ALREADY_EXISTS
        if not already:
            self.is_first = True
            _write_pid()
            return True

        # Ja ha uma instancia. Ela responde? Sinaliza e espera o ack.
        if self._existing_responds():
            self.is_first = False
            return False

        # Instancia travada: mata pelo PID e assume.
        pid = _read_pid()
        if pid and pid != os.getpid():
            _kill_pid(pid)
            time.sleep(0.5)  # da tempo do SO liberar o mutex/camera
        self.is_first = True
        _write_pid()
        return True

    def _existing_responds(self, timeout_ms: int = 1500) -> bool:
        """Sinaliza a instancia existente e espera o ack. True se respondeu."""
        ack = _kernel32.CreateEventW(None, False, False, _ACK_EVENT_NAME)
        if ack:
            _kernel32.ResetEvent(ack)
        h = _kernel32.OpenEventW(EVENT_MODIFY_STATE, False, _EVENT_NAME)
        if not h:
            # Sem o evento de "show", a instancia nem chegou a escutar; trata
            # como travada.
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
        """Na instancia primaria: escuta o sinal e chama on_show (numa thread).

        Responde ao segundo processo setando o ack, para ele saber que estamos
        vivos (senao ele nos mataria por 'travado')."""
        self._event = _kernel32.CreateEventW(None, False, False, _EVENT_NAME)
        if not self._event:
            return
        self._ack = _kernel32.CreateEventW(None, False, False, _ACK_EVENT_NAME)

        def loop():
            while True:
                r = _kernel32.WaitForSingleObject(self._event, INFINITE)
                if r == WAIT_OBJECT_0:
                    # Responde primeiro (ack) para nao ser morto por travado,
                    # depois mostra a janela.
                    if self._ack:
                        _kernel32.SetEvent(self._ack)
                    try:
                        on_show()
                    except Exception:
                        pass

        threading.Thread(target=loop, daemon=True).start()
