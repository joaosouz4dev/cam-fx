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


def _fix_blue_cast(frame):
    """Corrige o tom azulado do DirectShow via gray-world white balance.

    O DirectShow entrega a imagem "crua" (B/R ~1.10, azulada). Equalizamos as
    medias dos canais para a media global (gray-world), o que neutraliza o
    excesso de azul aproximando da cor que o MSMF/Meet mostram. Barato (~1ms).
    """
    try:
        import numpy as np
        b, g, r = cv2.split(frame)
        mb, mg, mr = float(b.mean()), float(g.mean()), float(r.mean())
        mgray = (mb + mg + mr) / 3.0
        if mb > 1 and mr > 1 and mg > 1:
            b = cv2.multiply(b, mgray / mb)
            g = cv2.multiply(g, mgray / mg)
            r = cv2.multiply(r, mgray / mr)
            frame = cv2.merge([
                np.clip(b, 0, 255).astype("uint8"),
                np.clip(g, 0, 255).astype("uint8"),
                np.clip(r, 0, 255).astype("uint8"),
            ])
    except Exception:
        pass
    return frame


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

    # 1) MSMF: cores corretas (igual a webcam direta). Lento para abrir (~10s).
    # Tentamos algumas vezes: se a camera acabou de ser liberada por outra
    # instancia/restart, o primeiro open pode falhar. Insistir no MSMF evita
    # cair no DirectShow (que entrega a imagem azulada).
    for attempt in range(3):
        try:
            log(f"open_camera: tentando MSMF no indice {index} (tentativa {attempt + 1})")
            cap = cv2.VideoCapture(index, cv2.CAP_MSMF)
            if cap.isOpened():
                if width:
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
                if height:
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
                if fps:
                    cap.set(cv2.CAP_PROP_FPS, fps)
                ok, _ = cap.read()
                if ok:
                    log("open_camera: MSMF OK")
                    return cap, "MSMF"
            cap.release()
        except Exception as exc:
            log(f"open_camera: MSMF EXCECAO: {exc!r}")
        time.sleep(1.0)  # da tempo da camera ser liberada antes de re-tentar

    # 2) Fallback: DirectShow via pygrabber (rapido, mas cor pode diferir).
    try:
        from .capture import DirectShowCapture

        log("open_camera: MSMF falhou, tentando DirectShow")
        cap = DirectShowCapture(index)
        if cap.wait_first_frame(timeout=12.0):
            log("open_camera: DirectShow OK (fallback)")
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


class _ThreadedReader:
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


class Pipeline:
    def __init__(self, config: Config) -> None:
        self.config = config
        self._thread: threading.Thread | None = None
        self._running = threading.Event()
        self._lock = threading.Lock()
        self._blur: BackgroundBlur | None = None
        self._framing: AutoFraming | None = None
        self._swapper = None        # FaceSwapperBackend | None
        self._swap_worker = None    # FaceSwapWorker | None
        self._source_face = None    # SourceFace | None
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
        # Serializa restarts: varias mudancas seguidas na UI (ligar swap, trocar
        # device, etc.) chamavam restart em paralelo, abrindo a camera enquanto a
        # anterior ainda fechava. Isso fazia o MSMF falhar e cair no DirectShow
        # (cor azulada) e podia travar o app. Com o lock, um restart espera o
        # outro terminar.
        if not hasattr(self, "_restart_lock"):
            self._restart_lock = threading.Lock()
        with self._restart_lock:
            was = self.running
            self.stop()
            if was:
                self.start()

    def update_source_face(self) -> None:
        """Recarrega apenas a foto-fonte do face swap, SEM reiniciar o pipeline.

        Trocar a foto nao exige reabrir a camera nem recarregar o insightface;
        so atualizar o SourceFace. Evita o restart pesado (e a cor azul que vinha
        do fallback DirectShow quando a camera ficava presa no restart)."""
        if not (self.running and self._swapper and self._source_face):
            return
        try:
            with self._lock:
                self._source_face.load(self.config.source_face_path, self._swapper)
        except Exception as exc:
            from .log import log as _log
            _log(f"faceswap: update_source_face falhou: {exc!r}")

    def _status(self, msg: str) -> None:
        if self.on_status:
            self.on_status(msg)

    def _error(self, msg: str) -> None:
        if self.on_error:
            self.on_error(msg)

    def _setup_faceswap(self, cfg) -> None:
        """Carrega o backend de face swap se habilitado e permitido.

        Tudo aqui e tolerante a falha: qualquer problema (modelos, termos, foto)
        apenas desativa o swap e segue o pipeline normal.
        """
        self._swapper = None
        self._swap_worker = None
        self._source_face = None
        if not getattr(cfg, "faceswap_enabled", False):
            return
        try:
            from . import terms
            if terms.needs_acceptance(cfg):
                from .log import log as _log
                _log("faceswap: termos nao aceitos, swap desativado")
                return
            if not cfg.source_face_path:
                self._status("Escolha uma foto de rosto para a troca.")
                return
            self._status("Preparando troca de rosto... (pode baixar modelos)")
            from .faceswap import load_swapper
            from .faceswap.source_face import SourceFace
            from .faceswap.worker import FaceSwapWorker

            self._swapper = load_swapper(
                cfg.faceswap_backend, cfg.compute_device,
                enhance=getattr(cfg, "faceswap_enhance", False),
            )
            self._source_face = SourceFace()
            if not self._source_face.load(cfg.source_face_path, self._swapper):
                self._error("Nenhum rosto encontrado na foto escolhida.")
                self._swapper = None
                self._source_face = None
                return
            self._swap_worker = FaceSwapWorker(
                self._swapper, self._source_face,
                detect_every=getattr(cfg, "faceswap_detect_every", 3),
            )
            self._swap_worker.start()
            from .log import log as _log
            _log("faceswap: worker iniciado")
        except Exception as exc:
            from .log import log as _log
            _log(f"faceswap: setup falhou: {exc!r}")
            self._error(f"Troca de rosto indisponivel: {exc}")
            self._swapper = None
            self._swap_worker = None
            self._source_face = None

    def _teardown_faceswap(self) -> None:
        if self._swap_worker:
            try:
                self._swap_worker.stop()
            except Exception:
                pass
            self._swap_worker = None
        if self._swapper:
            try:
                self._swapper.close()
            except Exception:
                pass
            self._swapper = None
        self._source_face = None

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
        # O DirectShow (fallback) entrega a imagem azulada; sinalizamos para
        # corrigir a cor no processamento. O MSMF ja vem com a cor correta.
        self._needs_color_fix = (_backend == "DirectShow")

        if cap is None:
            self._error(
                f"Nao consegui abrir a camera {cfg.camera_index}. "
                "Verifique se ela nao esta em uso por outro programa e se o "
                "acesso a camera esta liberado em Configuracoes do Windows > "
                "Privacidade e seguranca > Camera."
            )
            self._running.clear()
            return

        # Aguarda a camera estabilizar exposicao/white-balance antes de
        # transmitir (evita os primeiros frames escuros/azulados).
        self._status("Ajustando exposicao da camera...")
        if hasattr(cap, "wait_warmed"):
            cap.wait_warmed(timeout=4.0)
        else:
            # MSMF (cv2): descarta ~15 frames para a camera estabilizar.
            for _ in range(15):
                cap.read()

        try:
            from .log import log as _log

            self._blur = (BackgroundBlur(device=cfg.compute_device)
                          if cfg.blur_enabled else None)
            self._framing = AutoFraming() if cfg.framing_enabled else None
            prov = self._blur.active_provider if self._blur else "-"
            _log(f"modelos carregados: blur={self._blur is not None} "
                 f"framing={self._framing is not None} provider={prov}")
        except Exception as exc:  # modelo ausente, etc.
            cap.release()
            from .log import log as _log

            _log(f"FALHA ao carregar modelos: {exc!r}")
            self._error(f"Falha ao carregar modelos: {exc}")
            self._running.clear()
            return

        # Face swap (opcional, pesado): so carrega se habilitado, com termos
        # aceitos e uma foto-fonte valida. Falha aqui nao derruba o pipeline -
        # apenas segue sem swap.
        self._setup_faceswap(cfg)

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
            # Para o worker de face swap antes de fechar os modelos.
            self._teardown_faceswap()
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

        # Thread de captura: o cap.read() do MSMF e lento (~50ms/frame). Lendo
        # numa thread dedicada, o processamento usa sempre o frame mais recente
        # sem esperar o read, desacoplando o FPS de saida da latencia do read.
        reader = _ThreadedReader(cap)
        reader.start()
        last_seq = -1

        try:
            while self.running:
                frame, seq = reader.latest()
                if frame is None:
                    miss += 1
                    if miss >= 200 or not reader.alive:
                        self._error(
                            "Perdi o sinal da camera. Verifique se ela nao foi "
                            "desconectada ou tomada por outro programa."
                        )
                        break
                    time.sleep(0.005)
                    continue
                # Se nao chegou frame novo, nao reprocessa (economiza CPU e nao
                # introduz atraso reenviando frame velho); espera um pouco.
                if seq == last_seq:
                    time.sleep(0.002)
                    continue
                last_seq = seq
                miss = 0

                if frame.shape[1] != cfg.width or frame.shape[0] != cfg.height:
                    frame = cv2.resize(frame, (cfg.width, cfg.height))

                # Corrige a cor azulada quando caimos no DirectShow (fallback).
                if getattr(self, "_needs_color_fix", False):
                    frame = _fix_blue_cast(frame)

                ts_ms = int((time.perf_counter() - start) * 1000)

                try:
                    with self._lock:
                        if cfg.framing_enabled and self._framing:
                            frame = self._framing.process(
                                frame, ts_ms,
                                zoom=cfg.framing_zoom,
                                smoothing=cfg.framing_smoothing,
                            )
                        # Face swap: roda numa thread propria (worker) para nao
                        # travar o FPS. Submetemos o frame atual e usamos o ultimo
                        # resultado disponivel; se ainda nao ha resultado fresco,
                        # seguimos com o frame nao-trocado.
                        if cfg.faceswap_enabled and self._swap_worker:
                            self._swap_worker.submit(frame)
                            swapped = self._swap_worker.latest_result()
                            if swapped is not None:
                                frame = swapped
                        if cfg.blur_enabled and self._blur:
                            frame = self._blur.process(
                                frame, ts_ms + 1,
                                blur_strength=cfg.blur_strength,
                                mask_threshold=cfg.mask_threshold,
                                edge_softness=cfg.edge_softness,
                            )
                except Exception as exc:
                    from .log import log as _log
                    _log(f"Erro no processamento: {exc!r}")
                    self._error(f"Erro no processamento: {exc}")
                    break

                cam.send(frame)
                last_good = frame
                # Sem sleep: o ritmo ja e ditado pela chegada de frames novos da
                # camera (so processamos quando seq muda). Dormir aqui so somaria
                # latencia ("arrastado").

                frame_count += 1
                if frame_count == 30:  # loga uma vez, ~1s apos comecar
                    from .log import log as _log
                    _log(f"processando: blur={cfg.blur_enabled and self._blur is not None} "
                         f"framing={cfg.framing_enabled and self._framing is not None}")
                now = time.perf_counter()
                if now - last_fps_calc >= 1.0:
                    self._fps_actual = frame_count / (now - last_fps_calc)
                    frame_count = 0
                    last_fps_calc = now
        finally:
            reader.stop()
