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

# RESOLUCAO DINAMICA: a saida acompanha a resolucao real da camera (nao mais
# fixa em 720p, que rebaixava cameras 1080p). O buffer compartilhado e
# dimensionado pelo MAXIMO suportado (1080p); cada frame grava sua largura/
# altura REAIS no header, e o driver C++ le width/height do header para saber
# o tamanho do frame corrente. WIDTH/HEIGHT abaixo sao o DEFAULT/legado; o
# tamanho do buffer e MAX_*.
WIDTH = 1280
HEIGHT = 720
BPP = 3

# Teto do buffer compartilhado. Precisa bater com CAMFX_MAX_* em CamFXShared.h.
MAX_WIDTH = 1920
MAX_HEIGHT = 1080
MAX_FRAME_BYTES = MAX_WIDTH * MAX_HEIGHT * BPP

# struct CamFXSharedHeader: LONG magic, LONG width, LONG height, LONG frame_seq,
# LONGLONG ts_qpc, LONG consumers  -> 4 int32 + int64 + int32 = 28 bytes (pack 1).
_HEADER_FMT = "<iiiiqi"
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)
_CONSUMERS_OFFSET = struct.calcsize("<iiiiq")
# O arquivo mapeado tem sempre o tamanho MAXIMO; cada frame usa so os primeiros
# width*height*3 bytes apos o header.
TOTAL_BYTES = _HEADER_SIZE + MAX_FRAME_BYTES

MAGIC = 0x43414D46  # 'CAMF'

FRAME_DIR = os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "CamFX")
FRAME_FILE = os.path.join(FRAME_DIR, "frame.bin")


def _ensure_file() -> None:
    os.makedirs(FRAME_DIR, exist_ok=True)
    # Cria o arquivo com o tamanho MAXIMO se ainda nao existe ou esta menor
    # (ex.: arquivo antigo dimensionado para 720p de uma versao anterior).
    if not os.path.exists(FRAME_FILE) or os.path.getsize(FRAME_FILE) < TOTAL_BYTES:
        with open(FRAME_FILE, "wb") as f:
            f.write(b"\x00" * TOTAL_BYTES)


class CamFXVirtualCamera:
    """Escreve frames no arquivo compartilhado. Mesma interface do pyvirtualcam.

    A resolucao e DINAMICA: cada frame enviado define width/height no header
    (limitados ao MAX). O driver C++ le o header para saber o tamanho corrente.
    """

    def __init__(self, width: int = WIDTH, height: int = HEIGHT, fps: int = 30):
        # Resolucao alvo desta sessao (o send ajusta o frame recebido para ela,
        # limitada ao maximo do buffer). Nao mais travada em 720p.
        self.width = max(2, min(int(width) or WIDTH, MAX_WIDTH))
        self.height = max(2, min(int(height) or HEIGHT, MAX_HEIGHT))
        self.fps = fps
        self.device = "CamFX"
        self._seq = 0

        _ensure_file()
        self._fh = open(FRAME_FILE, "r+b")
        self._mm = mmap.mmap(self._fh.fileno(), TOTAL_BYTES)

    def send(self, frame_bgr: np.ndarray) -> None:
        """Envia um frame BGR na resolucao ALVO desta sessao (self.width x
        self.height, dinamica). Se o frame ja tem esse tamanho, e enviado SEM
        redimensionar (passthrough - preserva a nitidez da camera). So ajusta
        se o tamanho difere, e sem esticar (crop central no aspecto de saida)."""
        w_out, h_out = self.width, self.height

        if frame_bgr.shape[1] != w_out or frame_bgr.shape[0] != h_out:
            import cv2
            # Crop central no aspecto de saida para nao esticar cameras com
            # proporcao diferente (ex.: 4:3 para 16:9).
            h, w = frame_bgr.shape[:2]
            target = w_out / h_out
            src = w / h
            if abs(src - target) > 0.01:
                if src > target:
                    new_w = int(round(h * target))
                    x0 = (w - new_w) // 2
                    frame_bgr = frame_bgr[:, x0:x0 + new_w]
                else:
                    new_h = int(round(w / target))
                    y0 = (h - new_h) // 2
                    frame_bgr = frame_bgr[y0:y0 + new_h, :]
            frame_bgr = cv2.resize(frame_bgr, (w_out, h_out))
        if not frame_bgr.flags["C_CONTIGUOUS"]:
            frame_bgr = np.ascontiguousarray(frame_bgr)

        self._seq += 1
        # Escreve os pixels do frame (w_out*h_out*3 bytes) e depois o header com
        # a resolucao REAL. O buffer e maior (1080p), mas so usamos o inicio.
        # NAO sobrescreve 'consumers' (offset 24, heartbeat do driver): so os
        # 24 primeiros bytes (magic..ts_qpc).
        nbytes = w_out * h_out * BPP
        self._mm[_HEADER_SIZE:_HEADER_SIZE + nbytes] = frame_bgr.tobytes()
        header = struct.pack("<iiiiq", MAGIC, w_out, h_out, self._seq, 0)
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
