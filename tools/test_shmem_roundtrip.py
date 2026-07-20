"""Testa o contrato da memoria compartilhada Python <-> driver C++ (round-trip).

O virtualcam (Python) ESCREVE o frame no frame.bin; o driver C++ LE. Este teste
simula o lado C++ EM PYTHON, lendo o frame.bin exatamente como o FrameGenerator
faz (mesmo layout de header, mesmos offsets), e confere que:

  1. o frame escrito e recuperado IDENTICO (round-trip pixel a pixel);
  2. resolucao DINAMICA: 720p e 1080p vao e voltam com o tamanho certo no header;
  3. PASSTHROUGH: um frame ja no tamanho alvo sai sem alteracao;
  4. o campo 'consumers' (heartbeat do driver) NAO e sobrescrito pelo send.

Assim validamos a metade Python do contrato SEM precisar do driver compilado,
da camera ou de admin. Se o layout do header divergir do C++, este teste quebra.

Rodar: python tools/test_shmem_roundtrip.py
"""
import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

import camfx.virtualcam as vc


def check(desc, cond):
    print(f"  [{'OK' if cond else 'FALHOU'}] {desc}")
    assert cond, desc


class FakeCppReader:
    """Le o frame.bin como o FrameGenerator.cpp le: header + pixels BGR.

    Espelha o layout de CamFXShared.h: magic, width, height, frame_seq (int32),
    ts_qpc (int64), consumers (int32), depois os pixels. Le width/height do
    HEADER (resolucao dinamica), nao de constantes."""

    def __init__(self, path):
        self._path = path

    def read_frame(self):
        with open(self._path, "rb") as f:
            data = f.read()
        magic, w, h, seq = struct.unpack("<iiii", data[0:16])
        if magic != vc.MAGIC:
            return None
        # consumers fica em _CONSUMERS_OFFSET (apos ts_qpc); nao afeta os pixels.
        px_start = vc._HEADER_SIZE
        nbytes = w * h * 3
        px = np.frombuffer(data[px_start:px_start + nbytes], dtype=np.uint8)
        return {"w": w, "h": h, "seq": seq, "frame": px.reshape((h, w, 3))}

    def read_consumers(self):
        with open(self._path, "rb") as f:
            f.seek(vc._CONSUMERS_OFFSET)
            return struct.unpack("<i", f.read(4))[0]


def _make_cam(path, w, h):
    """Instancia o CamFXVirtualCamera apontando para um frame.bin de teste."""
    import mmap
    cam = vc.CamFXVirtualCamera.__new__(vc.CamFXVirtualCamera)
    cam.width = max(2, min(w, vc.MAX_WIDTH))
    cam.height = max(2, min(h, vc.MAX_HEIGHT))
    cam.fps = 30
    cam.device = "CamFX"
    cam._seq = 0
    with open(path, "wb") as f:
        f.write(b"\x00" * vc.TOTAL_BYTES)
    cam._fh = open(path, "r+b")
    cam._mm = mmap.mmap(cam._fh.fileno(), vc.TOTAL_BYTES)
    return cam


def _grad(w, h):
    """Frame BGR com um padrao unico por pixel (pega troca de canais/stride)."""
    y = np.arange(h, dtype=np.int32).reshape(h, 1)
    x = np.arange(w, dtype=np.int32).reshape(1, w)
    xx, yy = np.broadcast_arrays(x, y)      # ambos (h, w)
    b = ((xx + yy) % 256).astype(np.uint8)
    g = ((xx * 2) % 256).astype(np.uint8)
    r = ((yy * 3) % 256).astype(np.uint8)
    return np.stack([b, g, r], axis=-1)     # (h, w, 3) BGR


def main():
    tmp = Path(tempfile.gettempdir()) / "camfx_test_frame.bin"

    print("Round-trip em varias resolucoes (o driver le width/height do header):")
    for (w, h) in [(1280, 720), (1920, 1080), (640, 480)]:
        cam = _make_cam(tmp, w, h)
        reader = FakeCppReader(tmp)
        src = _grad(w, h)
        cam.send(src)                       # frame ja no tamanho alvo -> passthrough
        got = reader.read_frame()
        cam._mm.close(); cam._fh.close()
        check(f"{w}x{h}: header traz a resolucao certa",
              got is not None and got["w"] == w and got["h"] == h)
        check(f"{w}x{h}: passthrough devolve o frame IDENTICO (sem resize)",
              np.array_equal(got["frame"], src))

    print("Resolucao maior que o MAX e limitada ao buffer (nao estoura):")
    cam = _make_cam(tmp, 4096, 2160)  # 4K -> deve ser limitado a 1920x1080
    check("4K pedido -> alvo limitado ao MAX (1920x1080)",
          cam.width == vc.MAX_WIDTH and cam.height == vc.MAX_HEIGHT)
    cam._mm.close(); cam._fh.close()

    print("Frame de tamanho diferente do alvo e ajustado (nao quebra):")
    cam = _make_cam(tmp, 1280, 720)
    reader = FakeCppReader(tmp)
    # manda um frame 1080p para um alvo 720p: deve sair 720p (crop/resize)
    cam.send(_grad(1920, 1080))
    got = reader.read_frame()
    check("frame 1080p -> alvo 720p sai 1280x720",
          got["w"] == 1280 and got["h"] == 720)
    cam._mm.close(); cam._fh.close()

    print("O campo 'consumers' (heartbeat do driver) NAO e sobrescrito:")
    cam = _make_cam(tmp, 1280, 720)
    # simula o driver escrevendo o heartbeat no offset de consumers
    cam._mm[vc._CONSUMERS_OFFSET:vc._CONSUMERS_OFFSET + 4] = struct.pack("<i", 123456)
    cam.send(_grad(1280, 720))               # o send NAO pode tocar nesse campo
    reader = FakeCppReader(tmp)
    check("consumers preservado apos o send (heartbeat intacto)",
          reader.read_consumers() == 123456)
    cam._mm.close(); cam._fh.close()

    try:
        tmp.unlink()
    except Exception:
        pass

    print("\n>>> ROUND-TRIP DA SHMEM OK <<<")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
