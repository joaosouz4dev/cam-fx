"""Estagio de troca de rosto (face swap) para o pipeline unificado.

Encapsula o motor Deep-Live-Cam (vendorizado) como um ESTAGIO plugavel:
carrega o motor uma vez, roda deteccao de rosto ASSINCRONA (thread propria,
para nao travar o loop principal) e expoe process(frame) -> frame com o rosto
trocado. O pipeline unico chama isto entre a captura e o framing/blur.

Substitui o BridgeRunner (que era um pipeline paralelo inteiro). Agora ha UM so
loop de captura -> [swap] -> framing -> blur -> saida. A logica de swap (motor
DLC puro + deteccao assincrona) e a MESMA que ficou boa na ponte; so deixou de
ter captura/envio proprios (que duplicavam o pipeline).
"""

from __future__ import annotations

import threading
import time

import cv2
import numpy as np

from ..log import log


class SwapStage:
    """Estagio de face swap. prepare() carrega o motor; process(frame) troca o
    rosto. close() libera. A deteccao roda numa thread propria (assincrona)."""

    def __init__(self, source_path: str, device: str = "auto",
                 mouth_mask: bool = True, on_status=None):
        self._source_path = source_path
        self._device = device
        self._mouth_mask = mouth_mask
        self._on_status = on_status
        self._swapper = None
        self._source_face = None
        self._get_one_face = None
        self.ready = False
        # deteccao assincrona
        self._det_lock = threading.Lock()
        self._latest = None
        self._face = None
        self._det_stop = threading.Event()
        self._det_thread = None

    def _status(self, msg: str) -> None:
        if self._on_status:
            try:
                self._on_status(msg)
            except Exception:
                pass

    def prepare(self) -> bool:
        """Carrega o motor DLC + detector + foto-fonte. Retorna True se pronto.

        Loga cada etapa com tempo (t0) - o 1o carregamento (detector + inswapper
        no CUDA) leva ~6-10s. Sem isso a UI parece travada."""
        t0 = time.time()

        def step(status_msg: str, log_msg: str) -> None:
            self._status(status_msg)
            log(f"swap[{time.time() - t0:.0f}s]: {log_msg}")

        try:
            if self._device != "cpu":
                try:
                    from ..models import enable_cuda_dlls
                    enable_cuda_dlls()
                except Exception as exc:
                    log(f"swap: enable_cuda_dlls falhou: {exc!r}")

            step("Preparando o motor de troca de rosto...", "preparando motor")
            from ..vendor.dlc import ensure_engine
            swapper = ensure_engine()
            from ..models import (models_dir, ensure_faceswap_models,
                                  insightface_home, providers_for)
            insightface_home()
            swapper.models_dir = str(models_dir())

            import modules.globals as G
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
            self._get_one_face = get_one_face

            step("Carregando o detector de rosto... (pode levar ~1 min)",
                 "carregando detector (buffalo_l)")
            src = get_one_face(cv2.imread(self._source_path))
            if src is None:
                data = np.fromfile(self._source_path, dtype=np.uint8)
                img = cv2.imdecode(data, cv2.IMREAD_COLOR)
                src = get_one_face(img) if img is not None else None
            if src is None:
                log("swap: nenhum rosto na foto-fonte")
                self._status("Nenhum rosto encontrado na foto escolhida.")
                return False
            self._source_face = src

            step("Carregando o modelo de troca...", "carregando inswapper")
            model = swapper.get_face_swapper()
            self._swapper = swapper
            log(f"swap[{time.time() - t0:.0f}s]: motor pronto, "
                f"provider={model.session.get_providers()[0]}")

            # thread de deteccao assincrona
            self._det_thread = threading.Thread(target=self._det_loop, daemon=True)
            self._det_thread.start()
            self.ready = True
            return True
        except Exception as exc:
            import traceback
            log(f"swap: falha ao preparar motor: {exc!r}\n{traceback.format_exc()}")
            self._status("Falha ao preparar a troca de rosto.")
            return False

    def _det_loop(self) -> None:
        try:
            import ctypes
            ctypes.windll.ole32.CoInitializeEx(None, 0x0)
        except Exception:
            pass
        while not self._det_stop.is_set():
            with self._det_lock:
                f = self._latest
            if f is None:
                time.sleep(0.005)
                continue
            try:
                face = self._get_one_face(f)
            except Exception:
                face = None
            with self._det_lock:
                self._face = face

    def process(self, frame):
        """Troca o rosto no frame (usa o ultimo rosto detectado, assincrono).
        Se ainda nao ha rosto detectado, retorna o frame original."""
        if not self.ready or self._swapper is None:
            return frame
        with self._det_lock:
            self._latest = frame
            tface = self._face
        if tface is None:
            return frame
        try:
            out = self._swapper.swap_face(self._source_face, tface, frame)
            out = self._swapper.apply_post_processing(
                out, [tface.bbox.astype(int)])
            return out
        except Exception as exc:
            log(f"swap: erro no swap: {exc!r}")
            return frame

    def close(self) -> None:
        self._det_stop.set()
        if self._det_thread is not None:
            self._det_thread.join(timeout=2)
            self._det_thread = None
        self.ready = False
        self._swapper = None
        self._source_face = None
