"""Saida para a camera virtual CamFX via memoria compartilhada.

Substitui o pyvirtualcam (que dependia do driver do OBS). Escreve frames BGR
num bloco de memoria compartilhada que o driver DirectShow CamFXSource.dll le e
entrega aos aplicativos de video.

O layout do bloco precisa bater com driver/shared.h:
    [ header (magic,width,height,frame_seq,ts_qpc) ][ pixels BGR top-down ]
"""

from __future__ import annotations

import ctypes
import mmap
import struct

import numpy as np

WIDTH = 640
HEIGHT = 480
BPP = 3
FRAME_BYTES = WIDTH * HEIGHT * BPP

# struct CamFXSharedHeader: LONG magic, LONG width, LONG height, LONG frame_seq,
# LONGLONG ts_qpc, LONG consumers  -> 4 int32 + int64 + int32 = 28 bytes (pack 1).
_HEADER_FMT = "<iiiiqi"
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)
# Offset do campo 'consumers' dentro do header (apos magic,w,h,seq,ts).
_CONSUMERS_OFFSET = struct.calcsize("<iiiiq")
SHMEM_BYTES = _HEADER_SIZE + FRAME_BYTES

MAGIC = 0x43414D46  # 'CAMF'
SHMEM_NAME = "Local\\CamFXFrameBuffer"
MUTEX_NAME = "Local\\CamFXFrameMutex"

# Win32 - tipos explicitos para handles/ponteiros 64-bit.
from ctypes import wintypes

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
PAGE_READWRITE = 0x04
FILE_MAP_ALL_ACCESS = 0xF001F
INVALID_HANDLE_VALUE = wintypes.HANDLE(-1)

_kernel32.CreateFileMappingW.restype = wintypes.HANDLE
_kernel32.CreateFileMappingW.argtypes = [
    wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
    wintypes.DWORD, wintypes.DWORD, wintypes.LPCWSTR,
]
_kernel32.MapViewOfFile.restype = ctypes.c_void_p
_kernel32.MapViewOfFile.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t,
]
_kernel32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
_kernel32.CreateMutexW.restype = wintypes.HANDLE
_kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
_kernel32.WaitForSingleObject.restype = wintypes.DWORD
_kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


class CamFXVirtualCamera:
    """Camera virtual baseada no driver CamFX. Mesma interface basica do pyvirtualcam."""

    def __init__(self, width: int = WIDTH, height: int = HEIGHT, fps: int = 30):
        if (width, height) != (WIDTH, HEIGHT):
            # O driver opera em resolucao fixa; reamostramos no send().
            pass
        self.width = WIDTH
        self.height = HEIGHT
        self.fps = fps
        self.device = "CamFX"
        self._seq = 0

        self._h_map = _kernel32.CreateFileMappingW(
            INVALID_HANDLE_VALUE, None, PAGE_READWRITE, 0, SHMEM_BYTES, SHMEM_NAME
        )
        self._view = None
        self._h_mutex = None
        if not self._h_map:
            raise RuntimeError("Nao consegui criar a memoria compartilhada da CamFX.")

        self._view = _kernel32.MapViewOfFile(
            self._h_map, FILE_MAP_ALL_ACCESS, 0, 0, SHMEM_BYTES
        )
        if not self._view:
            raise RuntimeError("Nao consegui mapear a memoria compartilhada da CamFX.")

        self._buf = (ctypes.c_char * SHMEM_BYTES).from_address(self._view)

        self._h_mutex = _kernel32.CreateMutexW(None, False, MUTEX_NAME)
        if not self._h_mutex:
            raise RuntimeError("Nao consegui criar o mutex da CamFX.")

    def send(self, frame_bgr: np.ndarray) -> None:
        """Envia um frame BGR (qualquer tamanho; sera ajustado para 640x480)."""
        import cv2

        if frame_bgr.shape[1] != WIDTH or frame_bgr.shape[0] != HEIGHT:
            frame_bgr = cv2.resize(frame_bgr, (WIDTH, HEIGHT))
        if not frame_bgr.flags["C_CONTIGUOUS"]:
            frame_bgr = np.ascontiguousarray(frame_bgr)

        self._seq += 1
        # Escreve apenas magic..ts (24 bytes); NAO toca em 'consumers', que e
        # mantido pelo driver. O frame vai logo apos o header completo.
        header = struct.pack("<iiiiq", MAGIC, WIDTH, HEIGHT, self._seq, 0)

        _kernel32.WaitForSingleObject(self._h_mutex, 50)
        try:
            ctypes.memmove(self._view, header, len(header))
            ctypes.memmove(self._view + _HEADER_SIZE, frame_bgr.ctypes.data, FRAME_BYTES)
        finally:
            _kernel32.ReleaseMutex(self._h_mutex)

    def consumer_count(self) -> int:
        """Quantos apps estao consumindo a CamFX agora (lido do header)."""
        raw = ctypes.string_at(self._view + _CONSUMERS_OFFSET, 4)
        return struct.unpack("<i", raw)[0]

    def sleep_until_next_frame(self) -> None:
        # O ritmo de entrega e controlado pelo driver; aqui so cedemos a CPU.
        import time

        time.sleep(1.0 / max(1, self.fps))

    def close(self) -> None:
        # Zera o magic para o driver voltar a mostrar a tela de espera.
        try:
            _kernel32.WaitForSingleObject(self._h_mutex, 50)
            ctypes.memset(self._view, 0, _HEADER_SIZE)
            _kernel32.ReleaseMutex(self._h_mutex)
        except Exception:
            pass
        if getattr(self, "_view", None):
            _kernel32.UnmapViewOfFile(self._view)
            self._view = None
        if getattr(self, "_h_map", None):
            _kernel32.CloseHandle(self._h_map)
            self._h_map = None
        if getattr(self, "_h_mutex", None):
            _kernel32.CloseHandle(self._h_mutex)
            self._h_mutex = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def is_driver_registered() -> bool:
    """True se o filtro CamFX estiver registrado como dispositivo de video."""
    try:
        from pygrabber.dshow_graph import FilterGraph

        return "CamFX" in FilterGraph().get_input_devices()
    except Exception:
        return False


class DemandMonitor:
    """Mantem a memoria compartilhada viva e observa o contador de consumidores.

    Permite o modo sob demanda: o app fica dormindo (sem abrir a webcam) e so
    liga o pipeline quando algum aplicativo abre a camera CamFX. Como a shmem
    persiste enquanto o monitor vive, o driver consegue registrar consumidores
    mesmo antes de o pipeline comecar a enviar frames.
    """

    def __init__(self):
        self._h_map = _kernel32.CreateFileMappingW(
            INVALID_HANDLE_VALUE, None, PAGE_READWRITE, 0, SHMEM_BYTES, SHMEM_NAME
        )
        if not self._h_map:
            raise RuntimeError("Nao consegui criar a shmem do monitor CamFX.")
        self._view = _kernel32.MapViewOfFile(
            self._h_map, FILE_MAP_ALL_ACCESS, 0, 0, SHMEM_BYTES
        )
        if not self._view:
            raise RuntimeError("Nao consegui mapear a shmem do monitor CamFX.")

    def consumer_count(self) -> int:
        raw = ctypes.string_at(self._view + _CONSUMERS_OFFSET, 4)
        return struct.unpack("<i", raw)[0]

    def close(self):
        if getattr(self, "_view", None):
            _kernel32.UnmapViewOfFile(self._view)
            self._view = None
        if getattr(self, "_h_map", None):
            _kernel32.CloseHandle(self._h_map)
            self._h_map = None
