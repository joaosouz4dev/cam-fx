"""Interface do CamFX em WebView2 (HTML/CSS/JS) via pywebview.

A classe Api e exposta ao JavaScript (window.pywebview.api). Mantem a mesma
logica do app antigo: modo sob demanda (camera liga sozinha quando um app abre
a CamFX), preview, controles de efeito e device, bandeja e instancia unica.
"""

from __future__ import annotations

import base64
import struct
import sys
import threading
import time
from pathlib import Path

import webview

from . import autostart
from .branding import icon_path, logo_path
from .config import Config
from .log import log
from .pipeline import Pipeline, list_cameras
from .tray import TrayIcon
from .vcam_host import VCamHost
from .virtualcam import (
    DemandMonitor, FRAME_FILE, TOTAL_BYTES, _HEADER_SIZE, WIDTH, HEIGHT,
)


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

    def _demand_loop(self):
        empty_since = None
        OFF_DELAY = 5.0
        while not self._demand_stop.is_set():
            try:
                consumers = self._demand_monitor.consumer_count() if self._demand_monitor else 0
            except Exception:
                consumers = 0
            want = consumers > 0 or self._preview_forced
            if want:
                empty_since = None
                if not self.pipeline.running:
                    self.pipeline.start()
            elif self.pipeline.running:
                if empty_since is None:
                    empty_since = time.monotonic()
                elif time.monotonic() - empty_since >= OFF_DELAY:
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
        }

    def get_status(self):
        if self.pipeline.running:
            return f"Transmitindo na CamFX  ·  {self.pipeline.fps:.0f} FPS"
        return self._status

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
        width=1180, height=680, min_size=(1000, 620),
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
