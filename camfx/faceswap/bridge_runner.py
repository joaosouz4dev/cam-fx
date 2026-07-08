"""Roda o motor do Deep-Live-Cam EXATAMENTE como a ponte camfx_bridge que ficou
boa: captura (pygrabber) -> deteccao assincrona -> swap_face +
apply_post_processing do motor -> escreve no frame.bin da camera virtual CamFX.

E uma copia fiel do loop da ponte, sem o pipeline do app no meio (framing/blur/
resize/cor), que era o que degradava a qualidade. O app dispara isto numa thread
quando o face swap liga; o preview e a camera CamFX leem o mesmo frame.bin.
"""

from __future__ import annotations

import threading
import time

import cv2
import numpy as np

from ..log import log


class BridgeRunner:
    """Executa o loop da ponte numa thread. start()/stop()."""

    def __init__(self, source_path: str, camera_index: int = 0,
                 device: str = "auto", mouth_mask: bool = True, config=None,
                 on_status=None):
        self._source_path = source_path
        self._camera_index = camera_index
        self._device = device
        self._mouth_mask = mouth_mask
        self._config = config   # para ler blur/framing ao vivo
        self._on_status = on_status   # callback(str) p/ feedback na UI
        self._thread = None
        self._stop = threading.Event()
        self._fps = 0.0
        self._running = False
        self._blur = None
        self._framing = None

    def _status(self, msg: str) -> None:
        if self._on_status:
            try:
                self._on_status(msg)
            except Exception:
                pass

    @property
    def running(self) -> bool:
        return self._running

    @property
    def fps(self) -> float:
        return self._fps

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self, join_timeout: float = 3.0):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=join_timeout)
            self._thread = None
        self._running = False

    def _apply_effects(self, frame):
        """Aplica framing e blur (se ligados na config) apos o swap. Instancia
        os modelos sob demanda e os reusa. Tolerante a falha (se um efeito
        quebrar, segue com o frame sem ele)."""
        cfg = self._config
        if cfg is None:
            return frame
        import time as _t
        ts = int(_t.time() * 1000)
        try:
            if getattr(cfg, "framing_enabled", False):
                if self._framing is None:
                    from ..framing import AutoFraming
                    self._framing = AutoFraming()
                frame = self._framing.process(
                    frame, ts, zoom=cfg.framing_zoom,
                    smoothing=cfg.framing_smoothing)
        except Exception as exc:
            log(f"bridge: framing falhou: {exc!r}")
        try:
            if getattr(cfg, "blur_enabled", False):
                if self._blur is None:
                    from ..segmentation import BackgroundBlur
                    self._blur = BackgroundBlur(device=self._device)
                frame = self._blur.process(
                    frame, ts + 1, blur_strength=cfg.blur_strength,
                    mask_threshold=cfg.mask_threshold,
                    edge_softness=cfg.edge_softness)
        except Exception as exc:
            log(f"bridge: blur falhou: {exc!r}")
        return frame

    def _run(self):
        # COM em MTA para o onnxruntime DirectML/CUDA nesta thread.
        try:
            import ctypes
            ctypes.windll.ole32.CoInitializeEx(None, 0x0)
        except Exception:
            pass

        # DLLs do CUDA no PATH.
        if self._device != "cpu":
            try:
                from ..models import enable_cuda_dlls
                enable_cuda_dlls()
            except Exception as exc:
                log(f"bridge: enable_cuda_dlls falhou: {exc!r}")

        # Registra o motor vendorizado como pacote 'modules' e configura globals.
        # Feedback na UI em cada etapa: o 1o carregamento do motor (detector +
        # inswapper no CUDA) leva ~10-60s; sem status a UI parece travada.
        # Cada etapa loga com tempo decorrido (t0) para diagnosticar onde o
        # carregamento demora, alem do _status na UI.
        t0 = time.time()

        def step(status_msg: str, log_msg: str) -> None:
            self._status(status_msg)
            log(f"bridge[{time.time() - t0:.0f}s]: {log_msg}")

        try:
            step("Preparando o motor de troca de rosto...", "preparando motor")
            from ..vendor.dlc import ensure_engine
            swapper = ensure_engine()
            from ..models import models_dir, ensure_faceswap_models, insightface_home
            insightface_home()
            swapper.models_dir = str(models_dir())

            import modules.globals as G
            # Politica unica: swap/detector so usa CUDA ou CPU (nunca DirectML,
            # que quebra o detector buffalo_l). Fallback CPU automatico.
            from ..models import providers_for
            provs = providers_for(self._device, kind="swap")
            G.execution_providers = provs
            G.source_path = self._source_path
            G.many_faces = False
            G.map_faces = False
            G.mouth_mask = bool(self._mouth_mask)
            G.color_correction = True
            G.nsfw_filter = False
            G.live_mirror = False
            G.frame_processors = ["face_swapper"]
            G.fp_ui = {"face_enhancer": False}

            step("Verificando modelos de IA...", "verificando modelos")
            ensure_faceswap_models(fp16="CUDAExecutionProvider" in provs)
            from modules.face_analyser import get_one_face

            step("Carregando o detector de rosto... (pode levar ~1 min)",
                 "carregando detector (buffalo_l)")
            source_face = get_one_face(cv2.imread(self._source_path))
            if source_face is None:
                # cv2.imread falha com acento no caminho; tenta imdecode.
                data = np.fromfile(self._source_path, dtype=np.uint8)
                img = cv2.imdecode(data, cv2.IMREAD_COLOR)
                source_face = get_one_face(img) if img is not None else None
            if source_face is None:
                log("bridge: nenhum rosto na foto-fonte")
                self._status("Nenhum rosto encontrado na foto escolhida.")
                return

            step("Carregando o modelo de troca...", "carregando inswapper")
            model = swapper.get_face_swapper()
            log(f"bridge[{time.time() - t0:.0f}s]: motor pronto, "
                f"provider={model.session.get_providers()[0]}")
        except Exception as exc:
            import traceback
            log(f"bridge: falha ao preparar motor: {exc!r}\n{traceback.format_exc()}")
            self._status("Falha ao preparar a troca de rosto.")
            return

        # Abre a camera pela MESMA rotina do pipeline normal (open_camera):
        # MSMF -> DirectShow com cache/validacao, uma logica so para os dois
        # caminhos. Se o _loop acabou de liberar a camera, o 1o open pode
        # falhar; tenta algumas vezes antes de desistir.
        self._status("Abrindo a camera...")
        from ..pipeline import open_camera
        cap = None
        for attempt in range(4):
            if self._stop.is_set():
                return
            if attempt > 0:
                time.sleep(1.5)  # da tempo da camera liberar entre tentativas
            try:
                c, backend = open_camera(self._camera_index)
                if c is not None:
                    cap = c
                    log(f"bridge: camera aberta ({backend})")
                    break
            except Exception as exc:
                log(f"bridge: tentativa {attempt + 1} de abrir camera falhou: {exc!r}")
        if cap is None:
            log("bridge: camera nao entregou frame (desistindo)")
            return

        from ..virtualcam import CamFXVirtualCamera
        cam = CamFXVirtualCamera()

        # deteccao assincrona (como a ponte) para nao travar o swap
        det_lock = threading.Lock()
        latest = [None]
        det = {"face": None}
        det_stop = threading.Event()

        def det_loop():
            try:
                import ctypes
                ctypes.windll.ole32.CoInitializeEx(None, 0x0)
            except Exception:
                pass
            while not det_stop.is_set():
                with det_lock:
                    f = latest[0]
                if f is None:
                    time.sleep(0.005)
                    continue
                try:
                    face = get_one_face(f)
                except Exception:
                    face = None
                with det_lock:
                    det["face"] = face

        det_thread = threading.Thread(target=det_loop, daemon=True)
        det_thread.start()

        self._running = True
        log("bridge: transmitindo na CamFX")
        self._status("Troca de rosto ativa.")
        n, t0 = 0, time.time()
        try:
            while not self._stop.is_set():
                ret, frame = cap.read()
                if not ret or frame is None:
                    time.sleep(0.01)
                    continue
                with det_lock:
                    latest[0] = frame
                    tface = det["face"]
                out = frame
                if tface is not None:
                    try:
                        out = swapper.swap_face(source_face, tface, frame)
                        out = swapper.apply_post_processing(
                            out, [tface.bbox.astype(int)])
                    except Exception as exc:
                        log(f"bridge: erro no swap: {exc!r}")
                        out = frame
                # Framing e blur DEPOIS do swap (se ligados na config), na mesma
                # ordem do pipeline normal: framing recorta/zooma, blur desfoca.
                out = self._apply_effects(out)
                try:
                    cam.send(out)
                except Exception:
                    pass
                n += 1
                if time.time() - t0 >= 2.0:
                    self._fps = n / (time.time() - t0)
                    n, t0 = 0, time.time()
        finally:
            det_stop.set()
            try:
                cap.release()
            except Exception:
                pass
            try:
                cam.close()
            except Exception:
                pass
            for eff in (self._blur, self._framing):
                try:
                    if eff is not None:
                        eff.close()
                except Exception:
                    pass
            self._blur = None
            self._framing = None
            self._running = False
            log("bridge: parado")
