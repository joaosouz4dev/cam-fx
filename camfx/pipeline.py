"""Pipeline de processamento: captura -> efeitos -> camera virtual.

Roda numa thread propria para nao travar a UI. O frame segue a ordem:
  1. auto-framing (corta e enquadra no rosto)
  2. blur de fundo (segmenta a pessoa e desfoca o resto)
"""

from __future__ import annotations

import threading
import time

from .camera import ThreadedReader, _cache_backend, list_cameras, open_camera
from .config import Config
from .frameops import fit_aspect, fix_blue_cast
from .framing import AutoFraming
from .segmentation import BackgroundBlur
from .virtualcam import CamFXVirtualCamera

# Reexports para compatibilidade: quem importava `from .pipeline import
# list_cameras/open_camera` continua funcionando apos a extracao para camera.py.
__all__ = ["Pipeline", "list_cameras", "open_camera"]


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


        from .log import log as _log, log_debug as _dbg

        self._status("Abrindo camera... (pode levar alguns segundos)")
        cap, _backend = open_camera(cfg.camera_index, cfg.width, cfg.height, cfg.fps)
        self._backend = _backend
        _dbg(f"_loop[1]: camera aberta backend={_backend} running={self.running}")

        # Cancelamento: se o pipeline foi parado enquanto a camera abria (uma
        # operacao longa), aborta AQUI - nao segue carregando o motor nem entra
        # no loop de frames. Sem isto, uma thread parada continuava rodando e
        # brigava pela camera com a proxima (a corrida de threads).
        if not self.running:
            _dbg("_loop[1a]: cancelado apos abrir camera (running=False)")
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
        _dbg("_loop[2]: warmup da camera")
        if hasattr(cap, "wait_warmed"):
            cap.wait_warmed(timeout=4.0)
        else:
            # MSMF (cv2): descarta ~15 frames para a camera estabilizar.
            for _ in range(15):
                cap.read()
        _dbg("_loop[3]: warmup ok; carregando blur/framing")

        try:
            _dbg(f"_loop[3a]: criando BackgroundBlur (device={cfg.compute_device}, "
                 f"blur_enabled={cfg.blur_enabled})")
            self._blur = (BackgroundBlur(device=cfg.compute_device)
                          if cfg.blur_enabled else None)
            _dbg("_loop[3b]: BackgroundBlur ok; criando AutoFraming "
                 f"(framing_enabled={cfg.framing_enabled})")
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
        _dbg(f"_loop[4]: use_bridge={self._use_bridge()}")
        if self._use_bridge():
            try:
                from .faceswap.swap_stage import SwapStage
                stage = SwapStage(
                    source_path=cfg.source_face_path,
                    device=cfg.compute_device,
                    mouth_mask=True,
                    on_status=self._status,
                    swap_model_id=getattr(cfg, "swap_model_id", None),
                    swap_model_path=getattr(cfg, "swap_model_path", None),
                )
                ok = stage.prepare()
                _dbg(f"_loop[5]: SwapStage.prepare -> {ok}")
                if ok:
                    self._swap = stage
                else:
                    stage.close()
            except Exception as exc:
                _log(f"swap: falha ao criar estagio: {exc!r}")

        # Cancelamento: se foi parado durante o carregamento do motor (lento),
        # aborta antes de entrar no loop de frames.
        if not self.running:
            _dbg("_loop[5a]: cancelado apos motor (running=False)")
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

        _dbg("_loop[6]: abrindo camera virtual")
        try:
            with CamFXVirtualCamera(fps=cfg.fps) as cam:
                _dbg(f"_loop[7]: camera virtual ok ({cam.device}); loop de frames")
                self._status(f"Camera virtual ativa: {cam.device}")
                self._run_frames(cap, cam)
        except RuntimeError as exc:
            self._error(
                "Camera virtual indisponivel. O driver CamFX pode nao estar "
                f"registrado. Reinstale o CamFX. Detalhe: {exc}"
            )
        except Exception as exc:
            import traceback
            from .log import log as _log
            _log(f"_loop: erro no _run_frames/vcam: {exc!r}\n"
                 f"{traceback.format_exc()}")
            self._error(f"Erro no pipeline: {exc}")
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
        reader = ThreadedReader(cap)
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
                                reader = ThreadedReader(cap)
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
                    frame = fit_aspect(frame, cfg.width, cfg.height)

                # Corrige a cor azulada quando caimos no DirectShow (fallback).
                if getattr(self, "_needs_color_fix", False):
                    frame = fix_blue_cast(frame)

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
