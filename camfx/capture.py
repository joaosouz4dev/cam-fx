"""Captura da webcam fisica via DirectShow (pygrabber).

Por que nao usar o cv2.VideoCapture: nesta maquina o backend MSMF do OpenCV
leva ~11s para abrir a webcam, e o backend DSHOW do OpenCV falha com
"can't be used to capture by index". O grafo DirectShow do pygrabber abre a
mesma camera em ~2s. Esta classe encapsula esse grafo com uma interface
parecida com a do VideoCapture (open/read/release).
"""

from __future__ import annotations

import threading
import time

import numpy as np


class DirectShowCapture:
    """Captura por DirectShow. read() retorna (ok, frame_bgr)."""

    def __init__(self, index: int):
        from pygrabber.dshow_graph import FilterGraph

        self._graph = FilterGraph()
        self._graph.add_video_input_device(index)
        self._latest = None
        self._lock = threading.Lock()
        self._got = threading.Event()
        self._graph.add_sample_grabber(self._on_frame)
        self._graph.add_null_render()
        self._graph.prepare_preview_graph()
        self._graph.run()

        # Thread que puxa frames continuamente (o pygrabber e pull-based).
        self._stop = threading.Event()
        self._puller = threading.Thread(target=self._pull_loop, daemon=True)
        self._puller.start()

    def _on_frame(self, frame: np.ndarray) -> None:
        # pygrabber entrega RGB; convertemos para BGR (padrao do resto do app).
        with self._lock:
            self._latest = frame[:, :, ::-1].copy()
        self._got.set()

    def _pull_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._graph.grab_frame()
            except Exception:
                pass
            time.sleep(0.02)  # ~50 disparos/s; a camera entrega no seu ritmo

    def isOpened(self) -> bool:
        # Considera aberta se um primeiro frame chegou em ate ~6s.
        return self._got.wait(timeout=6.0)

    def read(self):
        with self._lock:
            if self._latest is None:
                return False, None
            return True, self._latest.copy()

    def wait_first_frame(self, timeout: float = 15.0) -> bool:
        return self._got.wait(timeout=timeout)

    def release(self) -> None:
        self._stop.set()
        if self._puller.is_alive():
            self._puller.join(timeout=2)
        try:
            self._graph.stop()
        except Exception:
            pass
