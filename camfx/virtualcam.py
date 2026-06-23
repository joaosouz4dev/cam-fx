"""Saida para a camera virtual CamFX via ARQUIVO mapeado.

O app escreve frames BGR num arquivo em C:\\ProgramData\\CamFX\\frame.bin, que o
source Media Foundation (rodando no Frame Server, outra sessao) le e entrega aos
aplicativos de video (Meet, Teams, Chrome, Zoom, Discord, etc.).

Usamos arquivo (nao memoria nomeada Global\\) porque o arquivo em ProgramData
cruza sessoes/contas sem exigir SeCreateGlobalPrivilege.

Layout (bate com mfcam/src/CamFXShared.h):
    [ header (magic,width,height,frame_seq,ts_qpc,consumers) ][ pixels BGR top-down ]
"""

from __future__ import annotations

import mmap
import os
import struct

import numpy as np

WIDTH = 1280
HEIGHT = 720
BPP = 3
FRAME_BYTES = WIDTH * HEIGHT * BPP

# struct CamFXSharedHeader: LONG magic, LONG width, LONG height, LONG frame_seq,
# LONGLONG ts_qpc, LONG consumers  -> 4 int32 + int64 + int32 = 28 bytes (pack 1).
_HEADER_FMT = "<iiiiqi"
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)
_CONSUMERS_OFFSET = struct.calcsize("<iiiiq")
TOTAL_BYTES = _HEADER_SIZE + FRAME_BYTES

MAGIC = 0x43414D46  # 'CAMF'

FRAME_DIR = os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "CamFX")
FRAME_FILE = os.path.join(FRAME_DIR, "frame.bin")


def _ensure_file() -> None:
    os.makedirs(FRAME_DIR, exist_ok=True)
    # Cria o arquivo com o tamanho exato se ainda nao existe ou esta menor.
    if not os.path.exists(FRAME_FILE) or os.path.getsize(FRAME_FILE) < TOTAL_BYTES:
        with open(FRAME_FILE, "wb") as f:
            f.write(b"\x00" * TOTAL_BYTES)


class CamFXVirtualCamera:
    """Escreve frames no arquivo compartilhado. Mesma interface do pyvirtualcam."""

    def __init__(self, width: int = WIDTH, height: int = HEIGHT, fps: int = 30):
        self.width = WIDTH
        self.height = HEIGHT
        self.fps = fps
        self.device = "CamFX"
        self._seq = 0

        _ensure_file()
        self._fh = open(FRAME_FILE, "r+b")
        self._mm = mmap.mmap(self._fh.fileno(), TOTAL_BYTES)

    def send(self, frame_bgr: np.ndarray) -> None:
        """Envia um frame BGR (qualquer tamanho; ajustado para 640x480)."""
        import cv2

        if frame_bgr.shape[1] != WIDTH or frame_bgr.shape[0] != HEIGHT:
            frame_bgr = cv2.resize(frame_bgr, (WIDTH, HEIGHT))
        if not frame_bgr.flags["C_CONTIGUOUS"]:
            frame_bgr = np.ascontiguousarray(frame_bgr)

        self._seq += 1
        # Escreve os pixels primeiro, depois o header. NAO sobrescreve o campo
        # 'consumers' (offset 24) - ele e o heartbeat gerenciado pelo driver.
        # Por isso escrevemos so os primeiros 24 bytes (magic..ts_qpc).
        self._mm[_HEADER_SIZE:TOTAL_BYTES] = frame_bgr.tobytes()
        header = struct.pack("<iiiiq", MAGIC, WIDTH, HEIGHT, self._seq, 0)
        self._mm[0:24] = header

    def sleep_until_next_frame(self) -> None:
        # Dorme apenas o tempo restante ate o proximo frame, descontando o tempo
        # ja gasto no processamento. Antes dormia o intervalo inteiro, somando ao
        # tempo de processo e derrubando o FPS pela metade.
        import time

        period = 1.0 / max(1, self.fps)
        now = time.perf_counter()
        nxt = getattr(self, "_next_t", None)
        if nxt is None:
            self._next_t = now + period
            return
        remaining = nxt - now
        if remaining > 0:
            time.sleep(remaining)
            self._next_t = nxt + period
        else:
            # Atrasado: nao dorme e realinha o relogio para nao acumular.
            self._next_t = now + period

    def close(self) -> None:
        # Zera o magic para o leitor voltar a tela de espera.
        try:
            self._mm[0:4] = struct.pack("<i", 0)
        except Exception:
            pass
        try:
            self._mm.close()
        except Exception:
            pass
        try:
            self._fh.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def is_driver_registered() -> bool:
    """True se a camera CamFX estiver disponivel (via enumeracao do sistema)."""
    try:
        from pygrabber.dshow_graph import FilterGraph

        return "CamFX" in FilterGraph().get_input_devices()
    except Exception:
        return False


class DemandMonitor:
    """Detecta se a camera CamFX esta sendo consumida por algum app.

    O driver grava um heartbeat (GetTickCount, em ms) no campo 'consumers' a cada
    frame pedido. So ha pedido enquanto algum app consome a CamFX. Aqui lemos
    esse tick e comparamos com o tick atual: se foi atualizado recentemente, ha
    consumidor ativo; se ficou parado, ninguem esta usando.
    """

    _IDLE_MS = 1500  # sem heartbeat por mais que isso = ninguem consumindo

    def __init__(self):
        _ensure_file()
        self._fh = open(FRAME_FILE, "r+b")
        self._mm = mmap.mmap(self._fh.fileno(), TOTAL_BYTES)
        import ctypes

        self._GetTickCount = ctypes.windll.kernel32.GetTickCount

    def _heartbeat(self) -> int:
        raw = bytes(self._mm[_CONSUMERS_OFFSET:_CONSUMERS_OFFSET + 4])
        # tick e gravado como LONG (pode dar negativo apos ~24.8 dias de uptime);
        # tratamos como uint32.
        return struct.unpack("<I", raw)[0]

    def in_use(self) -> bool:
        last = self._heartbeat()
        if last == 0:
            return False
        now = self._GetTickCount() & 0xFFFFFFFF
        delta = (now - last) & 0xFFFFFFFF
        return delta < self._IDLE_MS

    # Compatibilidade: 1 se em uso, 0 caso contrario.
    def consumer_count(self) -> int:
        return 1 if self.in_use() else 0

    def close(self):
        try:
            self._mm.close()
        except Exception:
            pass
        try:
            self._fh.close()
        except Exception:
            pass
