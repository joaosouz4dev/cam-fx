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
        self._index = index
        self._latest = None
        self._frame_count = 0
        self._lock = threading.Lock()
        self._got = threading.Event()
        self._warmed = threading.Event()
        # Numero de frames a descartar para a auto-exposicao/white-balance da
        # webcam estabilizar (senao a imagem sai escura/azulada).
        self._warmup_frames = 20

        # Tenta montar o grafo em 1280x720; se o pygrabber nao conectar nesse
        # formato (alguns drivers exigem decoder), faz fallback para o padrao
        # (640x480), que e rapido e estavel.
        if not self._build_graph(want_hd=True):
            self._build_graph(want_hd=False)

        self._stop = threading.Event()
        self._puller = threading.Thread(target=self._pull_loop, daemon=True)
        self._puller.start()

    def _build_graph(self, want_hd: bool) -> bool:
        from pygrabber.dshow_graph import FilterGraph

        try:
            self._graph = FilterGraph()
            self._graph.add_video_input_device(self._index)
            if want_hd:
                self._select_format(1280, 720)
            self._graph.add_sample_grabber(self._on_frame)
            self._graph.add_null_render()
            self._graph.prepare_preview_graph()
            self._graph.run()
            return True
        except Exception:
            try:
                self._graph.stop()
            except Exception:
                pass
            return False

    def _select_format(self, want_w: int, want_h: int) -> None:
        """Pede o formato want_w x want_h a webcam (prefere MJPG), se existir."""
        try:
            dev = self._graph.get_input_device()
            formats = dev.get_formats()
            best = None
            for i, fmt in enumerate(formats):
                w = fmt.get("width")
                h = fmt.get("height")
                mt = (fmt.get("media_type_str") or "").upper()
                if w == want_w and h == want_h:
                    # Prefere formatos nao-comprimidos (YUY2/RGB) que conectam ao
                    # sample grabber sem decoder; MJPG quebra o grafo do pygrabber.
                    score = 0 if "MJPG" in mt else 1
                    if best is None or score > best[0]:
                        best = (score, i)
            if best is not None:
                dev.set_format(best[1])
        except Exception:
            pass

    def _on_frame(self, frame: np.ndarray) -> None:
        # Esta versao do pygrabber ja entrega BGR (o padrao do resto do app).
        # NAO inverter os canais aqui: inverter troca R<->B e deixa a imagem
        # azulada (fone vermelho vira roxo/azul). Confirmado medindo o frame.
        with self._lock:
            self._latest = frame.copy()
            self._frame_count += 1
            if self._frame_count >= self._warmup_frames:
                self._warmed.set()
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

    def wait_warmed(self, timeout: float = 4.0) -> bool:
        """Espera a camera estabilizar exposicao/cor (descarta frames iniciais)."""
        return self._warmed.wait(timeout=timeout)

    def release(self) -> None:
        self._stop.set()
        if self._puller.is_alive():
            self._puller.join(timeout=2)
        try:
            self._graph.stop()
        except Exception:
            pass
