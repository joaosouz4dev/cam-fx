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


def _fit_aspect(frame, out_w: int, out_h: int):
    """Redimensiona para out_w x out_h SEM esticar: corta o excesso (crop
    central) para casar o aspecto e so entao redimensiona.

    A camera pode entregar 4:3 (ex.: C505e em 960p = 1280x960) enquanto a saida
    e 16:9 (1280x720). Um cv2.resize direto esticava a imagem (rosto alongado).
    Aqui recortamos a faixa central no aspecto de saida (como os apps de video
    fazem) e redimensionamos, preservando as proporcoes."""
    h, w = frame.shape[:2]
    target = out_w / out_h
    src = w / h
    if abs(src - target) > 0.01:
        if src > target:
            # fonte mais larga: corta as laterais
            new_w = int(round(h * target))
            x0 = (w - new_w) // 2
            frame = frame[:, x0:x0 + new_w]
        else:
            # fonte mais alta (4:3 p/ 16:9): corta topo/base
            new_h = int(round(w / target))
            y0 = (h - new_h) // 2
            frame = frame[y0:y0 + new_h, :]
    if frame.shape[1] != out_w or frame.shape[0] != out_h:
        frame = cv2.resize(frame, (out_w, out_h))
    return frame


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
        # Face swap como ESTAGIO do pipeline unico (Fase 3): SwapStage plugado
        # no _loop quando ligado, entre a captura e o framing/blur.
        self._swap = None           # SwapStage | None
        self._restarting = False    # True entre stop/start de um restart
        self._fps_actual = 0.0
        self.on_error = None  # callback(str) opcional
        self.on_status = None  # callback(str) opcional

    @property
    def running(self) -> bool:
        return self._running.is_set()

    @property
    def fps(self) -> float:
        return self._fps_actual

    def _use_bridge(self) -> bool:
        """True se o estagio de face swap deve ser plugado no loop: swap ligado,
        com foto-fonte e termos aceitos."""
        cfg = self.config
        if not getattr(cfg, "faceswap_enabled", False):
            return False
        if not getattr(cfg, "source_face_path", ""):
            return False
        try:
            from . import terms
            if terms.needs_acceptance(cfg):
                return False
        except Exception:
            return False
        return True

    def _startstop_lock(self):
        if not hasattr(self, "_ss_lock"):
            self._ss_lock = threading.Lock()
        return self._ss_lock

    def start(self) -> None:
        # PIPELINE UNICO (Fase 3): um so loop de captura -> [swap] -> framing ->
        # blur -> saida (o face swap e um ESTAGIO plugado, nao um pipeline
        # paralelo). GARANTIA de thread unica: start() PRIMEIRO encerra qualquer
        # thread _loop anterior (sinaliza running=False e faz join) e SO ENTAO
        # cria a nova - tudo sob _ss_lock. Assim nunca ha duas threads _loop
        # (sem precisar de lock dentro do _loop, que causava deadlock).
        with self._startstop_lock():
            if self.running and self._thread is not None \
                    and self._thread.is_alive():
                return  # ja rodando de verdade
            # Encerra restos de uma thread anterior antes de criar a nova.
            if self._thread is not None:
                self._running.clear()
                self._thread.join(timeout=45)
                self._thread = None
            self._running.set()
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

    def stop(self, join_timeout: float = 45) -> None:
        with self._startstop_lock():
            self._running.clear()
            if self._thread:
                # Espera a thread _loop encerrar (pode estar abrindo a camera ou
                # carregando o motor; ela checa self.running nos pontos-chave e
                # sai). Evita duas threads _loop concorrentes.
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
            # _restarting sinaliza ao demand loop para NAO chamar start() no
            # intervalo entre o stop() e o start() daqui - senao ele via
            # running=False e disparava um segundo _loop, criando duas threads
            # que brigavam pela camera (restart infinito ao ligar o swap).
            self._restarting = True
            try:
                was = self.running
                self.stop()
                if was:
                    self.start()
            finally:
                self._restarting = False

    def _status(self, msg: str) -> None:
        if self.on_status:
            self.on_status(msg)

    def _error(self, msg: str) -> None:
        if self.on_error:
            self.on_error(msg)

    def _loop(self) -> None:
        self._loop_body()

    def _loop_body(self) -> None:
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
        self._backend = _backend

        # Cancelamento: se o pipeline foi parado enquanto a camera abria (uma
        # operacao longa), aborta AQUI - nao segue carregando o motor nem entra
        # no loop de frames. Sem isto, uma thread parada continuava rodando e
        # brigava pela camera com a proxima (a corrida de threads).
        if not self.running:
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass
            return
        # Sem correcao de cor: usar a imagem pura da camera (como os outros
        # apps). Medido: a camera crua e neutra (B/R ~1.03), nao azulada; o
        # azul observado antes vinha de um build quebrado, nao da captura.
        self._needs_color_fix = False

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

        # Estagio de face swap (opcional): plugado no loop se o swap estiver
        # ligado com foto + termos. Carrega o motor DLC (lento ~6-10s) com
        # feedback na UI. Se falhar, o loop segue sem swap (so blur/framing).
        self._swap = None
        if self._use_bridge():
            try:
                from .faceswap.swap_stage import SwapStage
                stage = SwapStage(
                    source_path=cfg.source_face_path,
                    device=cfg.compute_device,
                    mouth_mask=True,
                    on_status=self._status,
                )
                if stage.prepare():
                    self._swap = stage
                else:
                    stage.close()
            except Exception as exc:
                from .log import log as _log
                _log(f"swap: falha ao criar estagio: {exc!r}")

        # Cancelamento: se foi parado durante o carregamento do motor (lento),
        # aborta antes de entrar no loop de frames.
        if not self.running:
            if self._swap is not None:
                try:
                    self._swap.close()
                except Exception:
                    pass
                self._swap = None
            try:
                cap.release()
            except Exception:
                pass
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
                if self._swap:
                    self._swap.close()
                    self._swap = None
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
            # NAO limpar self._running aqui: quem controla o running e o
            # start()/stop(). Se uma thread _loop antiga limpasse o running no
            # finally, poderia apagar o running que um restart/start novo acabou
            # de setar -> a thread nova saia na hora (deadlock/nao processa).
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
        from .log import log as _log

        # Recuperacao: alguns cameras (ex.: C505e) ABREM no MSMF e entregam os
        # primeiros frames, mas depois travam (0 FPS). Se isso acontecer, tenta
        # UMA vez reabrir por DirectShow (que sustenta frames nessa camera) antes
        # de desistir. So faz sentido se ainda nao estamos no DirectShow.
        recovered = False

        try:
            while self.running:
                frame, seq = reader.latest()
                if frame is None:
                    miss += 1
                    camera_dead = miss >= 200 or not reader.alive
                    if camera_dead and not recovered \
                            and getattr(self, "_backend", None) == "MSMF":
                        recovered = True
                        _log("camera MSMF travou; reabrindo por DirectShow")
                        self._status("Recuperando a camera...")
                        try:
                            reader.stop()
                        except Exception:
                            pass
                        try:
                            cap.release()
                        except Exception:
                            pass
                        try:
                            from .capture import DirectShowCapture
                            new_cap = DirectShowCapture(cfg.camera_index)
                            if new_cap.wait_first_frame(timeout=12.0):
                                cap = new_cap
                                self._backend = "DirectShow"
                                _cache_backend("DirectShow")
                                reader = _ThreadedReader(cap)
                                reader.start()
                                last_seq = -1
                                miss = 0
                                _log("recuperacao DirectShow OK")
                                continue
                            new_cap.release()
                        except Exception as exc:
                            _log(f"recuperacao DirectShow falhou: {exc!r}")
                    if camera_dead:
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
                    frame = _fit_aspect(frame, cfg.width, cfg.height)

                # Corrige a cor azulada quando caimos no DirectShow (fallback).
                if getattr(self, "_needs_color_fix", False):
                    frame = _fix_blue_cast(frame)

                ts_ms = int((time.perf_counter() - start) * 1000)

                try:
                    with self._lock:
                        # Face swap PRIMEIRO (no frame cru), depois framing e
                        # blur - a mesma ordem que ficou boa na ponte. O swap e
                        # um estagio plugado (SwapStage), com deteccao assincrona
                        # propria; se nao ha rosto detectado ainda, devolve o
                        # frame original sem travar.
                        if self._swap is not None and self._swap.ready:
                            frame = self._swap.process(frame)
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
                         f"framing={cfg.framing_enabled and self._framing is not None} "
                         f"swap={self._swap is not None and self._swap.ready}")
                now = time.perf_counter()
                if now - last_fps_calc >= 1.0:
                    self._fps_actual = frame_count / (now - last_fps_calc)
                    frame_count = 0
                    last_fps_calc = now
        finally:
            reader.stop()
