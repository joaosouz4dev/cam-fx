"""Interface do CamFX em WebView2 (HTML/CSS/JS) via pywebview.

A classe Api e exposta ao JavaScript (window.pywebview.api). Mantem a mesma
logica do app antigo: modo sob demanda (camera liga sozinha quando um app abre
a CamFX), preview, controles de efeito e device, bandeja e instancia unica.
"""

from __future__ import annotations

import base64
import json
import struct
import sys
import threading
import time
from pathlib import Path

import webview

from . import autostart
from . import terms
from .branding import icon_path, logo_path
from .config import Config
from .log import log
from .pipeline import Pipeline, list_cameras
from .tray import TrayIcon
from .updater import (
    UpdateChecker, download_installer, run_installer, check_for_update,
)
from .vcam_host import VCamHost
from .version import get_version
from .virtualcam import (
    DemandMonitor, FRAME_FILE, TOTAL_BYTES, _HEADER_SIZE, WIDTH, HEIGHT,
)


def pipeline_wanted(consumers: int, preview_forced: bool,
                    faceswap_enabled: bool = False) -> bool:
    """Decide se o pipeline (camera + efeitos) deve ficar LIGADO.

    So fica ligado se ALGUEM esta usando a camera: (a) um app consome a CamFX,
    ou (b) o preview esta ligado. O face swap NAO entra aqui: ele e uma
    CONFIGURACAO (como processar), nao uma demanda - manter a camera ligada so
    porque o swap esta ativo deixava a webcam "gravando" mesmo com o preview
    desligado e sem nenhum app usando. O bug de "ligar o swap derruba o
    pipeline" e resolvido no restart (que respeita o preview/consumer atuais),
    nao mantendo a camera ligada a toa. Funcao pura, testavel."""
    return bool(consumers > 0 or preview_forced)


def _ui_dir() -> Path:
    if hasattr(sys, "_MEIPASS"):
        p = Path(sys._MEIPASS) / "ui"  # type: ignore[attr-defined]
        if p.exists():
            return p
    return Path(__file__).resolve().parent / "ui"


class Api:
    def __init__(self):
        self.config = Config.load()
        self.pipeline = Pipeline(self.config)
        self.pipeline.on_status = self._on_status
        self.pipeline.on_error = self._on_status
        self._status = "Iniciando..."
        self._cameras = list_cameras() or [(self.config.camera_index, "Camera 0")]
        self._cam_indices = [i for i, _ in self._cameras]
        self._preview_forced = False
        self._window = None
        self._vcam_host = None
        self._demand_monitor = None
        self._demand_stop = None
        self._logo_data = self._encode_logo()
        self._update_checker = None
        self._update_info = None

    # ---------- ciclo de vida ----------

    def set_window(self, window):
        self._window = window

    def start_services(self):
        """Inicia host da camera virtual + monitor de demanda + bandeja."""
        from .single_instance import SingleInstance  # noqa: F401 (mantido no main)

        self._vcam_host = VCamHost()
        self._vcam_host.start()
        try:
            self._demand_monitor = DemandMonitor()
        except Exception as exc:
            log(f"monitor FALHOU: {exc!r}")
        self._demand_stop = threading.Event()
        threading.Thread(target=self._demand_loop, daemon=True).start()

        self.tray = TrayIcon(
            on_show=self.show_window, on_quit=self.quit,
            is_running=lambda: self.pipeline.running,
        )
        self.tray.run_detached()

        # Auto-atualizacao: checa ao abrir (com pequeno atraso) e a cada 6h.
        self._update_checker = UpdateChecker(on_update=self._on_update_found)
        self._update_checker.start()

    def _on_update_found(self, info):
        """Chamado pelo checker quando ha uma versao nova. Mostra o banner."""
        self._update_info = info
        self._push_update_banner(info)

    def _push_update_banner(self, info):
        if not self._window or not info:
            return
        try:
            ver = json.dumps(info["version"])
            notes = json.dumps(info.get("notes", "")[:600])
            self._window.evaluate_js(
                f"window.camfxUpdateAvailable && window.camfxUpdateAvailable({ver}, {notes})"
            )
        except Exception as exc:
            log(f"updater: falha ao notificar UI: {exc!r}")

    def _demand_loop(self):
        empty_since = None
        OFF_DELAY = 5.0
        while not self._demand_stop.is_set():
            try:
                consumers = self._demand_monitor.consumer_count() if self._demand_monitor else 0
            except Exception:
                consumers = 0
            want = pipeline_wanted(
                consumers, self._preview_forced,
                getattr(self.config, "faceswap_enabled", False))
            if want:
                empty_since = None
                if not self.pipeline.running:
                    self.pipeline.start()
            elif self.pipeline.running:
                if empty_since is None:
                    empty_since = time.monotonic()
                elif time.monotonic() - empty_since >= OFF_DELAY:
                    log(f"demand: parando pipeline (consumers={consumers} "
                        f"preview_forced={self._preview_forced})")
                    threading.Thread(target=self.pipeline.stop, daemon=True).start()
                    empty_since = None
            self._demand_stop.wait(1.0)

    def _on_status(self, msg):
        self._status = msg

    # ---------- API exposta ao JS ----------

    def get_logo(self):
        return self._logo_data

    def get_state(self):
        pos = (self._cam_indices.index(self.config.camera_index)
               if self.config.camera_index in self._cam_indices else 0)
        self.config.camera_index = self._cam_indices[pos]
        from .segmentation import available_devices
        devs = available_devices()
        labels = {"gpu": "GPU (DirectML)", "cpu": "CPU"}
        dev_opts = [{"value": "auto", "label": "Automático"}]
        for d in devs:
            dev_opts.append({"value": d, "label": labels.get(d, d)})
        return {
            "cameras": [{"name": n} for _, n in self._cameras],
            "camera_index_pos": pos,
            "devices": dev_opts,
            "compute_device": self.config.compute_device,
            "blur_enabled": self.config.blur_enabled,
            "blur_strength": self.config.blur_strength,
            "framing_enabled": self.config.framing_enabled,
            "framing_zoom": self.config.framing_zoom,
            "autostart": autostart.is_enabled(),
            "faceswap_enabled": self.config.faceswap_enabled,
            "faceswap_enhance": self.config.faceswap_enhance,
            "has_source_face": bool(self.config.source_face_path),
            "terms_accepted": not terms.needs_acceptance(self.config),
        }

    def get_status(self):
        if self.pipeline.running:
            fps = self.pipeline.fps
            # Enquanto o motor ainda esta subindo (FPS=0), mostra o status de
            # carregamento (ex.: "Carregando o detector...") em vez de
            # "0 FPS" - senao a UI parece travada durante o carregamento.
            if fps < 1 and self._status and "ativa" not in self._status.lower():
                return self._status
            return f"Transmitindo na CamFX  ·  {fps:.0f} FPS"
        return self._status

    # ---------- termos de uso (face swap) ----------

    def get_terms(self):
        return {"text": terms.text(), "version": terms.TERMS_VERSION}

    def terms_status(self):
        return {"needs_acceptance": terms.needs_acceptance(self.config)}

    def accept_terms(self):
        terms.accept(self.config, app_version=get_version())
        return {"accepted": True}

    # ---------- face swap ----------

    def set_faceswap_enabled(self, on):
        log(f"set_faceswap_enabled({on!r}) chamado; "
            f"needs_terms={terms.needs_acceptance(self.config)}")
        # Gate de seguranca: nunca liga sem aceite dos termos.
        if on and terms.needs_acceptance(self.config):
            log("set_faceswap_enabled: BLOQUEADO por termos nao aceitos")
            return {"ok": False, "needs_terms": True}
        self.config.faceswap_enabled = bool(on)
        self.config.save()
        log(f"set_faceswap_enabled: salvo faceswap_enabled={self.config.faceswap_enabled}")
        if self.pipeline.running:
            threading.Thread(target=self.pipeline.restart, daemon=True).start()
        return {"ok": True}

    def set_faceswap_enhance(self, on):
        self.config.faceswap_enhance = bool(on)
        self.config.save()
        if self.pipeline.running:
            threading.Thread(target=self.pipeline.restart, daemon=True).start()

    # ---------- seletor de modelos (swapper / enhancer) ----------

    def get_models(self):
        """Lista os modelos do catalogo (swappers e enhancers) com estado."""
        from .faceswap import registry
        def pack(kind):
            out = []
            for e in registry.list_models(kind):
                out.append({
                    "id": e.id, "name": e.name, "size_mb": e.size_mb,
                    "license": e.license, "note": e.note,
                    "downloaded": registry.is_downloaded(e),
                })
            return out
        return {
            "swappers": pack("swapper"),
            "enhancers": pack("enhancer"),
            "swap_model_id": getattr(self.config, "swap_model_id", "inswapper_128"),
            "enhance_model_id": getattr(self.config, "enhance_model_id", ""),
            "swap_model_path": getattr(self.config, "swap_model_path", ""),
            "enhance_model_path": getattr(self.config, "enhance_model_path", ""),
        }

    def download_model(self, model_id):
        """Baixa um modelo do catalogo (em thread). Progresso via JS."""
        from .faceswap import registry
        entry = next((e for e in registry.CATALOG if e.id == model_id), None)
        if entry is None:
            return {"ok": False, "error": "Modelo desconhecido."}

        def worker():
            def prog(msg):
                self._push_model_progress(model_id, msg)
            try:
                registry.download(entry, progress=prog)
                self._push_model_progress(model_id, "ok")
            except Exception as exc:
                log(f"download_model {model_id}: {exc!r}")
                self._push_model_progress(model_id, "erro")

        threading.Thread(target=worker, daemon=True).start()
        return {"ok": True}

    def _push_model_progress(self, model_id, msg):
        if not self._window:
            return
        try:
            self._window.evaluate_js(
                f"window.camfxModelProgress && window.camfxModelProgress("
                f"{json.dumps(model_id)}, {json.dumps(msg)})"
            )
        except Exception:
            pass

    def set_swap_model(self, model_id):
        self.config.swap_model_id = model_id
        self.config.save()
        if self.pipeline.running:
            threading.Thread(target=self.pipeline.restart, daemon=True).start()
        return {"ok": True}

    def set_enhance_model(self, model_id):
        self.config.enhance_model_id = model_id or ""
        self.config.save()
        if self.pipeline.running:
            threading.Thread(target=self.pipeline.restart, daemon=True).start()
        return {"ok": True}

    def choose_model_file(self, kind):
        """Escolhe um .onnx proprio do disco para swapper ou enhancer."""
        try:
            result = self._window.create_file_dialog(
                webview.OPEN_DIALOG, allow_multiple=False,
                file_types=("Modelo ONNX (*.onnx)",),
            )
        except Exception as exc:
            log(f"choose_model_file: dialogo falhou: {exc!r}")
            return {"error": "Nao foi possivel abrir o seletor."}
        if not result:
            return {}
        path = result[0] if isinstance(result, (list, tuple)) else result
        from .faceswap import registry
        if kind == "swapper":
            self.config.swap_model_id = registry.CUSTOM_ID
            self.config.swap_model_path = path
        else:
            self.config.enhance_model_id = registry.CUSTOM_ID
            self.config.enhance_model_path = path
        self.config.save()
        if self.pipeline.running:
            threading.Thread(target=self.pipeline.restart, daemon=True).start()
        import os
        return {"name": os.path.basename(path)}

    def choose_source_face(self):
        """Abre um dialogo para escolher a foto do rosto-fonte.

        Valida que ha um rosto na imagem (quando o backend estiver disponivel) e
        retorna um thumbnail base64 para a UI. Por ora (esqueleto) so valida que
        e uma imagem legivel; a deteccao de rosto entra com o backend.
        """
        try:
            result = self._window.create_file_dialog(
                webview.OPEN_DIALOG, allow_multiple=False,
                file_types=("Imagens (*.jpg;*.jpeg;*.png;*.bmp;*.webp)",),
            )
        except Exception as exc:
            log(f"choose_source_face: dialogo falhou: {exc!r}")
            return {"error": "Nao foi possivel abrir o seletor de arquivos."}
        if not result:
            return {}
        path = result[0] if isinstance(result, (list, tuple)) else result
        thumb = self._make_face_thumb(path)
        if not thumb:
            return {"error": "Nao foi possivel ler essa imagem."}
        self.config.source_face_path = path
        self.config.save()
        # O swap roda no BridgeRunner (motor DLC), que le a foto ao iniciar.
        # Trocar a foto exige reiniciar o pipeline para o bridge recarregar.
        if self.pipeline.running:
            threading.Thread(
                target=self.pipeline.restart, daemon=True).start()
        return {"thumb": thumb}

    def get_source_face_thumb(self):
        if not self.config.source_face_path:
            return None
        return self._make_face_thumb(self.config.source_face_path)

    def _make_face_thumb(self, path):
        """Le a imagem e devolve um thumbnail quadrado em data URL (JPEG)."""
        try:
            import os
            if not path or not os.path.exists(path):
                return None
            import cv2, numpy as np
            data = np.fromfile(path, dtype=np.uint8)  # suporta acentos no caminho
            img = cv2.imdecode(data, cv2.IMREAD_COLOR)
            if img is None:
                return None
            h, w = img.shape[:2]
            side = min(h, w)
            y0, x0 = (h - side) // 2, (w - side) // 2
            crop = img[y0:y0 + side, x0:x0 + side]
            crop = cv2.resize(crop, (96, 96))
            ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if not ok:
                return None
            return "data:image/jpeg;base64," + base64.b64encode(buf).decode("ascii")
        except Exception as exc:
            log(f"_make_face_thumb: {exc!r}")
            return None

    # ---------- atualizacao ----------

    def get_app_version(self):
        return get_version()

    def check_update_now(self):
        """Checa na hora (botao manual). Retorna info ou None."""
        info = check_for_update()
        self._update_info = info
        if info:
            self._update_info = info
        return info

    def download_and_install_update(self):
        """Baixa o instalador da versao nova e o executa, encerrando o app.

        Retorna {ok: bool, error?: str}. Em caso de sucesso o processo sai;
        o instalador silencioso assume a partir dai.
        """
        info = self._update_info or check_for_update()
        if not info:
            return {"ok": False, "error": "Nenhuma atualizacao disponivel."}

        def worker():
            self._update_progress(0, 0)
            path = download_installer(
                info["url"], on_progress=self._update_progress,
                filename=info.get("asset_name"))
            if not path:
                self._update_progress(-1, -1)
                return
            if run_installer(path):
                # Da um instante para o instalador iniciar e entao encerra o
                # app para que ele possa sobrescrever os arquivos.
                self._update_progress(100, 100)
                time.sleep(1.5)
                self.quit()
            else:
                self._update_progress(-1, -1)

        threading.Thread(target=worker, daemon=True).start()
        return {"ok": True}

    def open_releases_page(self):
        from .version import GITHUB_OWNER, GITHUB_REPO
        info = self._update_info or {}
        url = info.get("html_url") or (
            f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
        )
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass

    def _update_progress(self, got, total):
        if not self._window:
            return
        try:
            self._window.evaluate_js(
                f"window.camfxUpdateProgress && window.camfxUpdateProgress({got}, {total})"
            )
        except Exception:
            pass

    def set_camera(self, pos):
        self.config.camera_index = self._cam_indices[int(pos)]
        self.config.save()
        if self.pipeline.running:
            threading.Thread(target=self.pipeline.restart, daemon=True).start()

    def set_device(self, value):
        self.config.compute_device = value
        self.config.save()
        if self.pipeline.running:
            threading.Thread(target=self.pipeline.restart, daemon=True).start()

    def set_blur_enabled(self, on):
        self.config.blur_enabled = bool(on); self.config.save()

    def set_blur_strength(self, v):
        self.config.blur_strength = int(v); self.config.save()

    def set_framing_enabled(self, on):
        self.config.framing_enabled = bool(on); self.config.save()

    def set_zoom(self, v10):
        self.config.framing_zoom = int(v10) / 10.0; self.config.save()

    def set_autostart(self, on):
        autostart.set_enabled(bool(on))

    def set_preview(self, on):
        self._preview_forced = bool(on)
        if on and not self.pipeline.running:
            self.pipeline.start()

    def get_preview_frame(self):
        """Retorna o ultimo frame da CamFX como data URL (JPEG base64)."""
        try:
            import os
            if not os.path.exists(FRAME_FILE):
                return None
            with open(FRAME_FILE, "rb") as f:
                data = f.read(TOTAL_BYTES)
            if len(data) < TOTAL_BYTES:
                return None
            if struct.unpack("<i", data[0:4])[0] != 0x43414D46:
                return None
            import numpy as np, cv2
            arr = np.frombuffer(data[_HEADER_SIZE:TOTAL_BYTES], dtype=np.uint8)
            arr = arr.reshape((HEIGHT, WIDTH, 3))
            small = cv2.resize(arr, (WIDTH // 2, HEIGHT // 2))
            ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 70])
            if not ok:
                return None
            return "data:image/jpeg;base64," + base64.b64encode(buf).decode("ascii")
        except Exception:
            return None

    def minimize(self):
        if self._window:
            self._window.hide()

    # ---------- janela / bandeja ----------

    def show_window(self):
        if self._window:
            try:
                self._window.show()
                self._window.restore()
            except Exception:
                pass

    def quit(self):
        self.config.save()
        if self._demand_stop:
            self._demand_stop.set()
        try:
            self.pipeline.stop(join_timeout=2)
        except Exception:
            pass
        if self._vcam_host:
            try:
                self._vcam_host.stop()
            except Exception:
                pass
        try:
            self.tray.stop()
        except Exception:
            pass
        import os
        os._exit(0)

    def _encode_logo(self):
        p = logo_path()
        if p and p.exists():
            return "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode("ascii")
        return ""


def run(start_minimized: bool = False, instance=None):
    api = Api()
    ui = _ui_dir()
    window = webview.create_window(
        "CamFX",
        url=str(ui / "index.html"),
        js_api=api,
        width=1180, height=760, min_size=(900, 560),
        background_color="#0e1013",
        hidden=start_minimized,
    )
    api.set_window(window)

    def on_start():
        api.start_services()
        # Instancia unica: nova tentativa de abrir traz esta janela para frente.
        if instance is not None:
            instance.listen(api.show_window)

    def on_closing():
        # Fecha para a bandeja em vez de encerrar.
        api.minimize()
        return False

    window.events.closing += on_closing
    webview.start(on_start, icon=str(icon_path()) if icon_path() else None)
