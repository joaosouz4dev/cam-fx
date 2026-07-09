"""Estagio de troca de rosto (face swap) para o pipeline unificado.

Encapsula o motor Deep-Live-Cam (vendorizado) como um ESTAGIO plugavel do
pipeline: captura -> [SwapStage] -> framing -> blur -> saida.

CRITICO - COM/THREAD: o onnxruntime CUDA do motor DLC TRAVA se rodar numa thread
com COM em STA (apartment). O _loop do pipeline inicializa COM em STA (exigido
pelo DirectShow/pygrabber da captura). Por isso o SwapStage roda TODO o trabalho
do motor (carregar + detectar + trocar) numa THREAD WORKER PROPRIA com
CoInitializeEx MTA - exatamente como a ponte que funcionava. O process() apenas
troca frames com esse worker (envia o frame cru, recebe o frame trocado); o
worker faz o swap em MTA. Sem isso, o app trava em "carregando o motor" (o
selftest passava porque rodava sem o STA do _loop).
"""

from __future__ import annotations

import os
import threading
import time

import cv2
import numpy as np

from ..log import log, log_debug


def _detect_every() -> int:
    """Frequencia da deteccao de rosto (1 = todo frame). Detectar e caro
    (RetinaFace/buffalo_l); rodar em TODO frame limita o FPS sem ganho visivel,
    pois entre frames o rosto quase nao se move. Detectamos a cada N frames e
    reusamos a ultima face nos intermediarios - abordagem do Deep-Live-Cam.
    Ajustavel por CAMFX_DETECT_EVERY (padrao 3)."""
    try:
        n = int(os.environ.get("CAMFX_DETECT_EVERY", "3"))
        return n if n >= 1 else 1
    except Exception:
        return 3


class SwapStage:
    """Estagio de face swap. prepare() sobe o worker MTA e carrega o motor;
    process(frame) devolve o frame com o rosto trocado; close() encerra."""

    def __init__(self, source_path: str, device: str = "auto",
                 mouth_mask: bool = True, on_status=None,
                 swap_model_id: str | None = None,
                 swap_model_path: str | None = None):
        self._source_path = source_path
        self._device = device
        self._mouth_mask = mouth_mask
        self._on_status = on_status
        self._swap_model_id = swap_model_id
        self._swap_model_path = swap_model_path
        self.ready = False

        # troca de frames com o worker MTA
        self._lock = threading.Lock()
        self._in_frame = None       # frame cru a processar (do pipeline)
        self._out_frame = None      # ultimo frame trocado (para o pipeline)
        self._face = None           # ultimo rosto detectado (assincrono)
        self._stop = threading.Event()
        self._prepared = threading.Event()   # sinaliza fim do prepare
        self._prepare_ok = False
        self._worker = None

    def _status(self, msg: str) -> None:
        if self._on_status:
            try:
                self._on_status(msg)
            except Exception:
                pass

    def prepare(self) -> bool:
        """Sobe a thread worker MTA e espera o motor carregar. True se pronto."""
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()
        # espera o worker terminar de carregar o motor (ou falhar). Timeout
        # generoso: o 1o load CUDA pode levar ~30s.
        self._prepared.wait(timeout=180)
        self.ready = self._prepare_ok
        return self._prepare_ok

    def _worker_loop(self) -> None:
        """Roda em MTA: carrega o motor, detecta e troca o rosto. Um so lugar
        que toca no onnxruntime CUDA - fora do STA do _loop."""
        # COM em MTA (0x0) - obrigatorio para o onnxruntime CUDA nesta thread.
        try:
            import ctypes
            ctypes.windll.ole32.CoInitializeEx(None, 0x0)
        except Exception:
            pass

        swapper = None
        source_face = None
        get_one_face = None
        t0 = time.time()

        def step(status_msg: str, log_msg: str) -> None:
            self._status(status_msg)
            log_debug(f"swap[{time.time() - t0:.0f}s]: {log_msg}")

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
            log_debug(f"swap[{time.time() - t0:.0f}s]: -> ensure_faceswap_models")
            resolved_models = ensure_faceswap_models(
                progress=self._status,
                fp16="CUDAExecutionProvider" in provs,
                swap_model_id=self._swap_model_id,
                swap_model_path=self._swap_model_path,
            )
            selected_model = resolved_models.get("__selected__")
            if selected_model is not None:
                G.face_swapper_model_path = str(selected_model)
                log(f"swap: modelo selecionado {selected_model}")
            log_debug(f"swap[{time.time() - t0:.0f}s]: <- ensure_faceswap_models "
                      "ok; importando get_one_face")
            from modules.face_analyser import get_one_face
            log_debug(f"swap[{time.time() - t0:.0f}s]: get_one_face importado")

            step("Carregando o detector de rosto... (pode levar ~1 min)",
                 "carregando detector (buffalo_l)")
            log_debug(f"swap[{time.time() - t0:.0f}s]: -> 1a chamada get_one_face "
                      "(inicializa FaceAnalysis no provider)")
            src = get_one_face(cv2.imread(self._source_path))
            log_debug(f"swap[{time.time() - t0:.0f}s]: <- get_one_face "
                      f"(rosto={'ok' if src is not None else 'None'})")
            if src is None:
                data = np.fromfile(self._source_path, dtype=np.uint8)
                img = cv2.imdecode(data, cv2.IMREAD_COLOR)
                src = get_one_face(img) if img is not None else None
            if src is None:
                log("swap: nenhum rosto na foto-fonte")
                self._status("Nenhum rosto encontrado na foto escolhida.")
                self._prepare_ok = False
                self._prepared.set()
                return
            source_face = src

            step("Carregando o modelo de troca...", "carregando inswapper")
            model = swapper.get_face_swapper()
            log(f"swap: motor pronto, "
                f"provider={model.session.get_providers()[0]}")
            self._prepare_ok = True
        except Exception as exc:
            import traceback
            log(f"swap: falha ao preparar motor: {exc!r}\n{traceback.format_exc()}")
            self._status("Falha ao preparar a troca de rosto.")
            self._prepare_ok = False
            self._prepared.set()
            return

        # sinaliza que o prepare terminou (pipeline libera o loop de frames)
        self._prepared.set()

        # loop de trabalho (MTA). O pipeline entrega frames via process() e le
        # _out_frame. Detectar o rosto e a parte cara; rodar a cada frame nao
        # melhora o resultado (entre frames o rosto quase nao anda), so derruba
        # o FPS. Detectamos a cada N frames e reusamos a ultima face detectada
        # nos frames intermediarios - como o Deep-Live-Cam. Se a deteccao falhar
        # (rosto saiu de quadro), zeramos a face para nao trocar num lugar velho.
        detect_every = _detect_every()
        last_face = None
        i = 0
        while not self._stop.is_set():
            with self._lock:
                frame = self._in_frame
            if frame is None:
                time.sleep(0.005)
                continue
            if i % detect_every == 0:
                try:
                    last_face = get_one_face(frame)
                except Exception:
                    last_face = None
            i += 1
            face = last_face
            out = frame
            if face is not None:
                try:
                    out = swapper.swap_face(source_face, face, frame)
                    out = swapper.apply_post_processing(
                        out, [face.bbox.astype(int)])
                except Exception as exc:
                    log(f"swap: erro no swap: {exc!r}")
                    out = frame
            with self._lock:
                self._out_frame = out

    def process(self, frame):
        """Entrega o frame cru ao worker MTA e devolve o ultimo frame trocado.
        Nao bloqueia: se o worker ainda nao produziu, devolve o frame atual
        (assim o pipeline nunca trava esperando o swap)."""
        if not self.ready:
            return frame
        with self._lock:
            self._in_frame = frame
            out = self._out_frame
        return out if out is not None else frame

    def close(self) -> None:
        self._stop.set()
        if self._worker is not None:
            self._worker.join(timeout=3)
            self._worker = None
        self.ready = False
