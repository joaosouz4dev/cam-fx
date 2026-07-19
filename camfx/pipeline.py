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
        # STARTUP PROGRESSIVO: os efeitos carregam numa thread loader e plugam
        # ao vivo; o video CRU flui desde o inicio. Um loader so pluga um
        # estagio se DUAS coisas ainda valem no momento do plug:
        #   * _run_token: a MESMA execucao do _loop_body ainda esta viva. Cada
        #     _loop_body cria um token novo e o invalida no seu finally; assim
        #     um loader cujo loop morreu (camera caiu, erro) NAO pluga num
        #     pipeline morto (evitaria vazar o estagio no proximo start).
        #   * _loader_gen: a intencao de config nao mudou. Bumpado em stop() E
        #     em apply_effects() - trocar foto/modelo/toggle invalida qualquer
        #     loader em voo, para nunca plugar um estagio de config VELHA.
        # effects_status e o texto "carregando efeito X" exibido pela UI junto
        # ao FPS. _swap_load_lock serializa builds do SwapStage (o motor DLC usa
        # modules.globals, global de processo - dois builds concorrentes
        # corromperiam a config um do outro).
        self._loader_gen = 0
        self._run_token = None
        self.effects_status = ""
        self._swap_load_lock = threading.Lock()

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
            # Invalida IMEDIATAMENTE qualquer stage-loader em voo: sem este
            # bump, num restart (stop+start rapido) um loader da run antiga
            # veria running=True de novo e plugaria um estagio de config VELHA
            # na run nova. Com o bump, o gen capturado por ele ja nao bate.
            self._loader_gen += 1
            # Invalida o token da run atual: um loader tardio (ou o proprio
            # finally do _loop_body) so age se o token que capturou ainda for
            # o vigente. Aqui garantimos que nenhum loader plugue apos o stop.
            self._run_token = None
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
        _dbg("_loop[3]: warmup ok; abrindo camera virtual (efeitos em fundo)")

        # STARTUP PROGRESSIVO: os estagios comecam vazios e o video CRU flui
        # imediatamente; uma thread loader carrega blur/framing/swap em fundo
        # e os pluga ao vivo (sob _lock) quando ficam prontos. Antes, o load
        # era sequencial AQUI e a tela ficava preta ate o motor subir (o swap
        # sozinho leva de 6s a minutos quando baixa modelo). O _run_frames ja
        # tolera estagio None, entao nada muda no loop de frames.
        self._blur = None
        self._framing = None
        self._swap = None
        self.effects_status = ""
        # Token desta execucao do loop: o loader so pluga se ele ainda for o
        # vigente. Invalidado no finally (se ainda for meu) e em stop().
        run_token = object()
        self._run_token = run_token

        try:
            with CamFXVirtualCamera(fps=cfg.fps) as cam:
                _dbg(f"_loop[4]: camera virtual ok ({cam.device}); "
                     "frames crus + loader de efeitos")
                self._status(f"Camera virtual ativa: {cam.device}")
                self._start_loader(run_token)
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
            # Invalida o token DESTA run (se ainda for o vigente): a partir daqui
            # nenhum loader tardio pode plugar um estagio nesta execucao que
            # esta morrendo - crucial quando o loop morre SOZINHO (camera caiu,
            # erro de processamento), sem passar por stop(). So invalida se ainda
            # for meu: se um start novo ja rodou, ele trocou o token e nao devo
            # apagar o token da run nova. Referencia atomica no CPython.
            if self._run_token is run_token:
                self._run_token = None
            # Fecha os modelos sob o mesmo lock do processamento, para nunca
            # destruir o segmenter/detector enquanto um frame esta em process()
            # (evita o erro 'Task runner is currently not running'). Um loader
            # que plugou ANTES deste finally e fechado aqui; um que tentar plugar
            # DEPOIS ve o token invalido (sob o mesmo _lock) e nao pluga.
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

    # ---------- carregamento progressivo de efeitos (stage loader) ----------

    def _set_effects_status(self, msg: str, gen: int | None = None) -> None:
        """Status dos EFEITOS ("Baixando o modelo... 120/554 MB") exibido pela
        UI junto ao FPS - o video cru ja esta no ar, nada fica escondido.

        Quando `gen` e passado (callback do SwapStage de um loader), so escreve
        se essa geracao ainda e a vigente: assim um SwapStage stale que continua
        baixando em background nao "mente" o status da run/config nova."""
        if gen is not None and gen != self._loader_gen:
            return
        self.effects_status = msg

    def _start_loader(self, run_token=None) -> None:
        """Dispara a thread que carrega e pluga os efeitos em fundo.

        Captura (run_token, gen) do momento: o loader so pluga se AMBOS ainda
        valerem no plug (run viva + config inalterada). Se run_token nao for
        dado (chamada de apply_effects a quente), usa o token da run corrente.
        Nome fixo da thread: a simulacao espera os loaders morrerem por ele."""
        token = run_token if run_token is not None else self._run_token
        gen = self._loader_gen
        threading.Thread(target=self._load_stages, args=(token, gen),
                         name="camfx-stage-loader", daemon=True).start()

    def _load_stages(self, run_token, gen: int) -> None:
        """Carrega os efeitos habilitados e os pluga ao vivo, um a um.

        Regras que evitam os bugs historicos de threading deste projeto:
        - constroi FORA do _lock (carregar modelo e lento) e pluga SOB o
          _lock (instantaneo), com dupla checagem running+token+gen+config
          DENTRO do lock;
        - `stale()` cobre tres mortes: pipeline parado (running), run trocada
          ou morta (run_token != _run_token) e config mudada (gen != _loader_gen
          - bumpado por apply_effects). Assim um estagio de config VELHA nunca
          e plugado, nem um estagio numa run morta (que vazaria);
        - nunca toca _ss_lock nem chama start/stop/restart (nao e um segundo
          _loop: so carrega e troca ponteiros);
        - se ficou obsoleto, fecha o que construiu e sai;
        - COM em MTA (0x0): o onnxruntime CUDA trava em STA (swap_stage.py).
        """
        from .log import log as _log
        cfg = self.config

        def stale() -> bool:
            # token None = nenhuma run viva (loop morto/parado): sempre stale.
            return (not self.running) or run_token is None \
                or run_token is not self._run_token \
                or gen != self._loader_gen

        com_ok = False
        try:
            import ctypes
            ctypes.windll.ole32.CoInitializeEx(None, 0x0)
            com_ok = True
        except Exception:
            pass
        try:
            # ---- blur (rapido, ~1-3s) ----
            if cfg.blur_enabled and self._blur is None and not stale():
                self._set_effects_status("Carregando o desfoque de fundo...", gen)
                blur = None
                try:
                    blur = BackgroundBlur(device=cfg.compute_device)
                except Exception as exc:
                    # Nao derruba mais o pipeline: o video cru continua.
                    _log(f"loader: falha ao carregar blur: {exc!r}")
                    self._error(f"Falha ao carregar o desfoque: {exc}")
                if blur is not None:
                    plugged = False
                    with self._lock:
                        if not stale() and self._blur is None \
                                and cfg.blur_enabled:
                            self._blur = blur
                            plugged = True
                            # Le o provider AQUI (sob o lock, ref local): fora do
                            # lock um detach concorrente poderia zerar self._blur
                            # entre o teste e o acesso -> AttributeError.
                            prov = blur.active_provider
                    if plugged:
                        _log(f"loader: blur plugado (provider={prov})")
                    else:  # nao plugou (obsoleto/duplicado): descarta
                        blur.close()
                        if stale():
                            return

            # ---- framing (rapido) ----
            if cfg.framing_enabled and self._framing is None and not stale():
                self._set_effects_status("Carregando o enquadramento...", gen)
                fr = None
                try:
                    fr = AutoFraming()
                except Exception as exc:
                    _log(f"loader: falha ao carregar framing: {exc!r}")
                if fr is not None:
                    plugged = False
                    with self._lock:
                        if not stale() and self._framing is None \
                                and cfg.framing_enabled:
                            self._framing = fr
                            plugged = True
                    if plugged:
                        _log("loader: framing plugado")
                    else:
                        fr.close()
                        if stale():
                            return

            # ---- face swap (o lento: 6s a minutos se baixar modelo) ----
            # _swap_load_lock BLOQUEANTE serializa builds: o motor DLC configura
            # modules.globals (global de processo); dois builds concorrentes
            # corromperiam um ao outro. Quem espera revalida stale() ao acordar.
            if self._use_bridge() and self._swap is None and not stale():
                with self._swap_load_lock:
                    if stale() or self._swap is not None \
                            or not self._use_bridge():
                        return
                    self._set_effects_status("Preparando a troca de rosto...", gen)
                    from .faceswap.swap_stage import SwapStage
                    stage = SwapStage(
                        source_path=cfg.source_face_path,
                        device=cfg.compute_device,
                        mouth_mask=True,
                        on_status=lambda m, g=gen: self._set_effects_status(m, g),
                        swap_model_id=getattr(cfg, "swap_model_id", None),
                        swap_model_path=getattr(cfg, "swap_model_path", None),
                        detect_every=getattr(cfg, "faceswap_detect_every", 3),
                    )
                    ok = False
                    try:
                        ok = stage.prepare()  # bloqueia SO esta thread
                    except Exception as exc:
                        _log(f"loader: falha ao preparar swap: {exc!r}")
                    plugged = False
                    if ok:
                        with self._lock:
                            if not stale() and self._swap is None \
                                    and self._use_bridge():
                                self._swap = stage
                                plugged = True
                    if plugged:
                        _log("loader: swap plugado ao vivo")
                    else:
                        stage.close()
        except Exception as exc:
            _log(f"loader: erro inesperado: {exc!r}")
        finally:
            # So limpa o status se este loader ainda e o dono da geracao (senao
            # apagaria o "carregando..." de um loader mais novo que assumiu).
            if gen == self._loader_gen:
                self.effects_status = ""
            if com_ok:
                try:
                    import ctypes
                    ctypes.windll.ole32.CoUninitialize()
                except Exception:
                    pass

    def apply_effects(self, reload_swap: bool = False) -> None:
        """Aplica mudancas de efeito A QUENTE, sem reiniciar a camera.

        Chamado pela UI ao ligar/desligar um efeito ou trocar foto/modelo do
        swap. Despluga o que foi desligado - destaca sob _lock (instantaneo;
        com o lock em maos nenhum process() esta em voo e o frame loop nao
        alcanca mais o objeto) e fecha FORA do lock em thread propria (o
        close() do swap joina o worker por ate 3s e nao pode congelar o
        video). Depois dispara o loader para carregar o que falta. A camera
        NUNCA para; restart() fica so para troca de camera/device."""
        if not self.running:
            return
        cfg = self.config
        to_close = []
        with self._lock:
            # Bump do gen SOB o _lock: invalida qualquer loader em voo (ex.: um
            # SwapStage ainda carregando a foto ANTIGA). O loader velho, ao
            # tentar plugar, vera stale() e fechara o estagio; o loader novo
            # (disparado abaixo, com o gen novo) constroi com a config atual.
            # Fecha o bug "config velha plugada durante reload em voo".
            self._loader_gen += 1
            if self._swap is not None and (reload_swap or not self._use_bridge()):
                to_close.append(self._swap)
                self._swap = None
            if self._blur is not None and not cfg.blur_enabled:
                to_close.append(self._blur)
                self._blur = None
            if self._framing is not None and not cfg.framing_enabled:
                to_close.append(self._framing)
                self._framing = None
        if to_close:
            def _close_all(objs=to_close):
                for obj in objs:
                    try:
                        obj.close()
                    except Exception:
                        pass
            threading.Thread(target=_close_all, daemon=True).start()
        self._start_loader()

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
                        # frame original sem travar. O check de cfg torna o
                        # toggle-off instantaneo mesmo antes do despluge.
                        if self._swap is not None and self._swap.ready \
                                and getattr(cfg, "faceswap_enabled", False):
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
