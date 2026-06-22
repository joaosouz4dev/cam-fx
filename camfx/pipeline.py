"""Pipeline de processamento: captura -> efeitos -> camera virtual.

Roda numa thread propria para nao travar a UI. O frame segue a ordem:
  1. auto-framing (corta e enquadra no rosto)
  2. blur de fundo (segmenta a pessoa e desfoca o resto)
"""

from __future__ import annotations

import threading
import time

import cv2

from .config import Config
from .framing import AutoFraming
from .segmentation import BackgroundBlur
from .virtualcam import CamFXVirtualCamera


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


def open_camera(index: int, width: int | None = None, height: int | None = None,
                fps: int | None = None):
    """Abre a camera, preferindo DirectShow (pygrabber), que e bem mais rapido.

    Retorna (cap, backend_nome) ou (None, None). O objeto retornado tem a
    interface read()/release()/isOpened() em ambos os casos.
    """
    # 1) DirectShow via pygrabber: abre em ~2s nesta webcam (MSMF leva ~11s).
    try:
        from .capture import DirectShowCapture
        from .log import log

        log(f"open_camera: tentando DirectShow no indice {index}")
        cap = DirectShowCapture(index)
        if cap.wait_first_frame(timeout=12.0):
            log("open_camera: DirectShow OK")
            return cap, "DirectShow"
        log("open_camera: DirectShow sem frame em 12s, caindo para OpenCV")
        cap.release()
    except Exception as exc:
        try:
            from .log import log
            log(f"open_camera: DirectShow EXCECAO: {exc!r}")
        except Exception:
            pass

    # 2) Fallback: backends do OpenCV (MSMF/DSHOW/ANY).
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


class Pipeline:
    def __init__(self, config: Config) -> None:
        self.config = config
        self._thread: threading.Thread | None = None
        self._running = threading.Event()
        self._lock = threading.Lock()
        self._blur: BackgroundBlur | None = None
        self._framing: AutoFraming | None = None
        self._fps_actual = 0.0
        self.on_error = None  # callback(str) opcional
        self.on_status = None  # callback(str) opcional

    @property
    def running(self) -> bool:
        return self._running.is_set()

    @property
    def fps(self) -> float:
        return self._fps_actual

    def start(self) -> None:
        if self.running:
            return
        self._running.set()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self, join_timeout: float = 45) -> None:
        self._running.clear()
        if self._thread:
            # Espera a abertura lenta da camera (MSMF) terminar e a thread
            # encerrar, evitando duas threads _loop concorrentes. No encerramento
            # do app, use join_timeout pequeno para nao travar o fechamento.
            self._thread.join(timeout=join_timeout)
            self._thread = None

    def restart(self) -> None:
        was = self.running
        self.stop()
        if was:
            self.start()

    def _status(self, msg: str) -> None:
        if self.on_status:
            self.on_status(msg)

    def _error(self, msg: str) -> None:
        if self.on_error:
            self.on_error(msg)

    def _loop(self) -> None:
        cfg = self.config
        # O DirectShow (pygrabber/COM) exige COM inicializado NESTA thread.
        # Sem isso, a captura rapida falha com "CoInitialize nao foi chamado" e
        # cai no MSMF lento (~16s). Inicializa em modo apartment (STA).
        _com_initialized = False
        try:
            import pythoncom

            pythoncom.CoInitialize()
            _com_initialized = True
        except Exception:
            try:
                import ctypes

                ctypes.windll.ole32.CoInitialize(None)
                _com_initialized = True
            except Exception:
                pass

        self._status("Abrindo camera... (pode levar alguns segundos)")
        cap, _backend = open_camera(cfg.camera_index, cfg.width, cfg.height, cfg.fps)

        if cap is None:
            self._error(
                f"Nao consegui abrir a camera {cfg.camera_index}. "
                "Verifique se ela nao esta em uso por outro programa e se o "
                "acesso a camera esta liberado em Configuracoes do Windows > "
                "Privacidade e seguranca > Camera."
            )
            self._running.clear()
            return

        try:
            self._blur = BackgroundBlur()
            self._framing = AutoFraming()
        except Exception as exc:  # modelo ausente, etc.
            cap.release()
            self._error(f"Falha ao carregar modelos: {exc}")
            self._running.clear()
            return

        try:
            with CamFXVirtualCamera(fps=cfg.fps) as cam:
                self._status(f"Camera virtual ativa: {cam.device}")
                self._run_frames(cap, cam)
        except RuntimeError as exc:
            self._error(
                "Camera virtual indisponivel. O driver CamFX pode nao estar "
                f"registrado. Reinstale o CamFX. Detalhe: {exc}"
            )
        finally:
            cap.release()
            # Fecha os modelos sob o mesmo lock do processamento, para nunca
            # destruir o segmenter/detector enquanto um frame esta em process()
            # (evita o erro 'Task runner is currently not running').
            with self._lock:
                if self._blur:
                    self._blur.close()
                    self._blur = None
                if self._framing:
                    self._framing.close()
                    self._framing = None
            if _com_initialized:
                try:
                    import pythoncom

                    pythoncom.CoUninitialize()
                except Exception:
                    try:
                        import ctypes

                        ctypes.windll.ole32.CoUninitialize()
                    except Exception:
                        pass
            self._running.clear()
            self._status("Parado.")

    def _run_frames(self, cap, cam) -> None:
        cfg = self.config
        start = time.perf_counter()
        frame_count = 0
        last_fps_calc = start
        miss = 0
        last_good = None

        while self.running:
            ok, frame = cap.read()
            if not ok or frame is None:
                # Tolera quedas momentaneas: o MSMF as vezes solta um frame
                # vazio sem a camera ter caido. So desiste apos varias falhas.
                miss += 1
                if miss >= 60:  # ~2s de falhas seguidas a 30fps
                    self._error(
                        "Perdi o sinal da camera. Verifique se ela nao foi "
                        "desconectada ou tomada por outro programa."
                    )
                    break
                if last_good is not None:
                    cam.send(last_good)  # mantem a saida viva com o ultimo frame
                time.sleep(0.01)
                continue
            miss = 0

            if frame.shape[1] != cfg.width or frame.shape[0] != cfg.height:
                frame = cv2.resize(frame, (cfg.width, cfg.height))

            ts_ms = int((time.perf_counter() - start) * 1000)

            try:
                with self._lock:
                    if cfg.framing_enabled and self._framing:
                        frame = self._framing.process(
                            frame, ts_ms,
                            zoom=cfg.framing_zoom,
                            smoothing=cfg.framing_smoothing,
                        )
                    if cfg.blur_enabled and self._blur:
                        frame = self._blur.process(
                            frame, ts_ms + 1,
                            blur_strength=cfg.blur_strength,
                            mask_threshold=cfg.mask_threshold,
                            edge_softness=cfg.edge_softness,
                        )
            except Exception as exc:
                self._error(f"Erro no processamento: {exc}")
                break

            cam.send(frame)
            last_good = frame
            cam.sleep_until_next_frame()

            frame_count += 1
            now = time.perf_counter()
            if now - last_fps_calc >= 1.0:
                self._fps_actual = frame_count / (now - last_fps_calc)
                frame_count = 0
                last_fps_calc = now
