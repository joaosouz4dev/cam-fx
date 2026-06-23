"""Instancia unica do CamFX.

Garante que apenas uma instancia do app rode. Se o usuario abrir de novo, o
segundo processo sinaliza o primeiro (que traz a janela para frente) e sai.

Mecanismo: um named mutex detecta a instancia ja existente; um named event serve
de sinal "mostre a janela". O primeiro processo cria o mutex e fica escutando o
evento numa thread; o segundo so dispara o evento e encerra.
"""

from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes

_MUTEX_NAME = "Local\\CamFX_SingleInstance_Mutex"
_EVENT_NAME = "Local\\CamFX_ShowWindow_Event"

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
ERROR_ALREADY_EXISTS = 183

_kernel32.CreateMutexW.restype = wintypes.HANDLE
_kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
_kernel32.CreateEventW.restype = wintypes.HANDLE
_kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
_kernel32.OpenEventW.restype = wintypes.HANDLE
_kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
_kernel32.SetEvent.argtypes = [wintypes.HANDLE]
_kernel32.WaitForSingleObject.restype = wintypes.DWORD
_kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]

EVENT_MODIFY_STATE = 0x0002
WAIT_OBJECT_0 = 0x0
INFINITE = 0xFFFFFFFF


class SingleInstance:
    def __init__(self):
        self._mutex = None
        self._event = None
        self.is_first = False

    def acquire(self) -> bool:
        """True se esta e a primeira instancia; False se ja havia outra."""
        self._mutex = _kernel32.CreateMutexW(None, False, _MUTEX_NAME)
        already = ctypes.get_last_error() == ERROR_ALREADY_EXISTS
        self.is_first = not already
        return self.is_first

    def signal_existing(self) -> None:
        """Pede a instancia ja aberta para mostrar a janela (chamado pelo 2o)."""
        h = _kernel32.OpenEventW(EVENT_MODIFY_STATE, False, _EVENT_NAME)
        if h:
            _kernel32.SetEvent(h)
            _kernel32.CloseHandle(h)

    def listen(self, on_show) -> None:
        """Na instancia primaria: escuta o sinal e chama on_show (numa thread)."""
        # Evento auto-reset: dispara on_show a cada SetEvent do segundo processo.
        self._event = _kernel32.CreateEventW(None, False, False, _EVENT_NAME)
        if not self._event:
            return

        def loop():
            while True:
                r = _kernel32.WaitForSingleObject(self._event, INFINITE)
                if r == WAIT_OBJECT_0:
                    try:
                        on_show()
                    except Exception:
                        pass

        threading.Thread(target=loop, daemon=True).start()
