"""Abertura, listagem e leitura da camera de entrada.

Extraido do pipeline.py para separar a maquinaria de captura (backends MSMF/
DirectShow, cache do que funciona, leitura em thread) da orquestracao do
pipeline. A classe Pipeline consome open_camera() e ThreadedReader daqui.
"""

from __future__ import annotations

import threading
import time

import cv2

# Nomes de saidas virtuais que NAO devem aparecer como camera de entrada,
# para evitar loop (capturar a propria saida da CamFX ou a tela do OBS).
_VIRTUAL_HINTS = ("obs virtual", "obs-camera", "obs cam", "camfx")

# Backends de captura, em ordem de tentativa. Algumas webcams nao abrem por
# DirectShow ("can't be used to capture by index") mas funcionam por Media
# Foundation (MSMF), e vice-versa. Tentamos os dois antes de desistir.
_CAPTURE_BACKENDS = (
    (cv2.CAP_MSMF, "MSMF"),
    (cv2.CAP_DSHOW, "DSHOW"),
    (cv2.CAP_ANY, "ANY"),
)


def _backend_cache_path():
    from .config import config_dir
    return config_dir() / "camera_backend.txt"


def _cached_backend() -> str | None:
    try:
        p = _backend_cache_path()
        if p.exists():
            return p.read_text(encoding="utf-8").strip() or None
    except Exception:
        pass
    return None


def _cache_backend(name: str) -> None:
    try:
        _backend_cache_path().write_text(name, encoding="utf-8")
    except Exception:
        pass


def open_camera(index: int, width: int | None = None, height: int | None = None,
                fps: int | None = None):
    """Abre a camera, preferindo MSMF (Media Foundation).

    MSMF e o MESMO backend que o Meet/Chrome usam direto, entao entrega as cores
    processadas pela camera (white balance correto). O DirectShow/pygrabber abre
    mais rapido, mas entrega cores "cruas" (azuladas) diferentes das que o
    usuario ve na webcam direta. Por isso priorizamos MSMF mesmo sendo ~10s mais
    lento; o DirectShow fica so como fallback.

    Retorna (cap, backend_nome) ou (None, None).
    """
    from .log import log

    # Cache: se numa execucao anterior a camera so abriu por DirectShow, pula o
    # MSMF (que trava/demora nessa webcam) e vai direto ao que funciona. Isso
    # elimina os ~10-30s perdidos tentando o MSMF. O cache fica em
    # LOCALAPPDATA/CamFX/camera_backend.txt.
    prefer_dshow = _cached_backend() == "DirectShow"

    # 1) MSMF: cores corretas. So tenta 1x (nao 3x) e so se nao houver cache
    # dizendo que e DirectShow - a maioria das falhas de MSMF nesta camera nao
    # se resolve com retry, so custa tempo.
    if not prefer_dshow:
        try:
            log(f"open_camera: tentando MSMF no indice {index}")
            cap = cv2.VideoCapture(index, cv2.CAP_MSMF)
            if cap.isOpened():
                if width:
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
                if height:
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
                if fps:
                    cap.set(cv2.CAP_PROP_FPS, fps)
                # Valida o MSMF de verdade: alguns cameras (ex.: C505e) ABREM
                # e entregam o 1o frame, mas depois travam (0 FPS). Exigir varios
                # reads seguidos filtra esse caso e cai pro DirectShow, que
                # funciona nessa camera. NAO cacheamos "MSMF": o cache so serve
                # de atalho para "DirectShow" (pular o MSMF lento); gravar "MSMF"
                # so forcava justamente o backend que trava.
                good = 0
                for _ in range(8):
                    ok, _frame = cap.read()
                    if ok and _frame is not None:
                        good += 1
                    else:
                        break
                if good >= 5:
                    log("open_camera: MSMF OK")
                    return cap, "MSMF"
                log(f"open_camera: MSMF instavel ({good}/8 frames), "
                    "tentando DirectShow")
            cap.release()
        except Exception as exc:
            log(f"open_camera: MSMF EXCECAO: {exc!r}")

    # 2) Fallback: DirectShow via pygrabber (rapido, mas cor pode diferir).
    try:
        from .capture import DirectShowCapture

        log("open_camera: tentando DirectShow")
        cap = DirectShowCapture(index)
        if cap.wait_first_frame(timeout=12.0):
            log("open_camera: DirectShow OK")
            _cache_backend("DirectShow")
            return cap, "DirectShow"
        cap.release()
    except Exception as exc:
        log(f"open_camera: DirectShow EXCECAO: {exc!r}")

    # 3) Ultimo recurso: outros backends do OpenCV.
    for backend, name in _CAPTURE_BACKENDS:
        cap = cv2.VideoCapture(index, backend)
        if cap.isOpened():
            if width:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            if height:
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
            if fps:
                cap.set(cv2.CAP_PROP_FPS, fps)
            ok, _ = cap.read()
            if ok:
                return cap, name
        cap.release()
    return None, None


def list_cameras() -> list[tuple[int, str]]:
    """Lista (indice, nome) das cameras de entrada, sem a camera virtual.

    Usa DirectShow (pygrabber) para obter os nomes na mesma ordem de indice do
    OpenCV. Cai num fallback por sondagem se o pygrabber nao estiver disponivel.
    """
    try:
        from pygrabber.dshow_graph import FilterGraph

        devices = FilterGraph().get_input_devices()
        result = [
            (i, name)
            for i, name in enumerate(devices)
            if not any(hint in name.lower() for hint in _VIRTUAL_HINTS)
        ]
        if result:
            return result
    except Exception:
        pass
    return _probe_cameras()


def _probe_cameras(max_index: int = 8) -> list[tuple[int, str]]:
    found = []
    for index in range(max_index):
        cap, _backend = open_camera(index)
        if cap is not None:
            found.append((index, f"Camera {index}"))
            cap.release()
    return found


class ThreadedReader:
    """Le frames da camera numa thread dedicada.

    O cap.read() do MSMF e lento (~50ms). Lendo continuamente em background, o
    pipeline sempre pega o frame mais recente sem bloquear, desacoplando o FPS
    de saida da latencia do read.
    """

    def __init__(self, cap):
        self._cap = cap
        self._latest = None
        self._seq = 0          # incrementa a cada frame NOVO capturado
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self.alive = True

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        fails = 0
        while not self._stop.is_set():
            ok, frame = self._cap.read()
            if ok and frame is not None:
                fails = 0
                with self._lock:
                    self._latest = frame
                    self._seq += 1
            else:
                fails += 1
                if fails >= 60:
                    self.alive = False
                    break
                time.sleep(0.01)

    def latest(self):
        with self._lock:
            return self._latest, self._seq

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
