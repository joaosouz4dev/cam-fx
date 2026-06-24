"""Worker de face swap em thread dedicada.

O swap e pesado demais para rodar inline no loop de 30 FPS. Aqui ele roda numa
thread propria: o loop principal SUBMETE o frame mais recente e PEGA o ultimo
resultado disponivel, sem nunca bloquear. FPS de saida fica desacoplado do custo
do swap (mesmo principio do _ThreadedReader da captura).

Frame-skip: se chegam frames mais rapido do que o swap processa, so o mais
recente e mantido; os antigos sao descartados (latencia nao cresce sem limite).

Deteccao amortizada: detecta o rosto-alvo a cada `detect_every` frames e reusa a
ultima deteccao entre eles (o custo alto e a deteccao, nao o inswapper).
"""

from __future__ import annotations

import threading
from typing import Optional

import numpy as np

from ..log import log
from .base import FaceSwapperBackend
from .source_face import SourceFace


class FaceSwapWorker:
    def __init__(self, backend: FaceSwapperBackend, source: SourceFace,
                 detect_every: int = 3):
        self._backend = backend
        self._source = source
        self._detect_every = max(1, int(detect_every))

        self._lock = threading.Lock()
        self._pending: Optional[np.ndarray] = None   # ultimo frame a processar
        self._result: Optional[np.ndarray] = None     # ultimo frame ja trocado
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._frame_no = 0

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self, join_timeout: float = 2.0):
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=join_timeout)
            self._thread = None

    def submit(self, frame_bgr: np.ndarray):
        """Entrega o frame mais recente para o worker (descarta o anterior)."""
        with self._lock:
            self._pending = frame_bgr
        self._wake.set()

    def latest_result(self) -> Optional[np.ndarray]:
        """Ultimo frame com rosto trocado, ou None se ainda nao ha resultado."""
        with self._lock:
            return self._result

    def _loop(self):
        # ONNX Runtime DirectML usa COM em modo MULTI-THREADED (MTA). Inicializar
        # esta thread como STA (CoInitialize) pode crashar o processo ao rodar a
        # sessao DirectML. COINIT_MULTITHREADED = 0x0.
        try:
            import ctypes
            ctypes.windll.ole32.CoInitializeEx(None, 0x0)  # MTA
        except Exception:
            pass

        while not self._stop.is_set():
            self._wake.wait(timeout=0.5)
            self._wake.clear()
            if self._stop.is_set():
                break
            with self._lock:
                frame = self._pending
                self._pending = None
            if frame is None:
                continue
            if not self._source.ready:
                continue
            try:
                detect = (self._frame_no % self._detect_every) == 0
                self._frame_no += 1
                res = self._backend.swap_frame(
                    frame, self._source.handle, detect=detect)
                with self._lock:
                    self._result = res.frame
            except Exception as exc:
                log(f"faceswap worker: erro no swap: {exc!r}")
