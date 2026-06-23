"""Interface grafica do CamFX (Tkinter).

Modelo de uso: a camera virtual "CamFX" funciona em modo automatico. O app fica
na bandeja; quando algum aplicativo (Meet, Teams, Zoom, Discord, OBS) abre a
CamFX, a webcam fisica liga sozinha com os efeitos e desliga quando ninguem
mais usa (a luz da webcam indica). A janela serve para pre-visualizar o
resultado e ajustar os efeitos; nao ha botao de ligar/pausar manual.
"""

from __future__ import annotations

import struct
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from PIL import Image, ImageTk

from . import autostart
from .config import Config
from .log import log
from .models import ensure_models
from .pipeline import Pipeline, list_cameras
from .tray import TrayIcon
from .vcam_host import VCamHost
from .virtualcam import (
    DemandMonitor,
    FRAME_FILE,
    HEIGHT,
    TOTAL_BYTES,
    WIDTH,
    _HEADER_SIZE,
)


class CamFXApp:
    def __init__(self, start_minimized: bool = False) -> None:
        self.config = Config.load()
        self.pipeline = Pipeline(self.config)
        self.pipeline.on_error = self._on_pipeline_error
        self.pipeline.on_status = self._on_pipeline_status
        self._demand_monitor = None
        self._demand_thread = None
        self._demand_stop = None
        self._vcam_host = None

        self.root = tk.Tk()
        self.root.title("CamFX")
        self.root.geometry("940x520")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)

        from . import theme
        self._theme = theme
        theme.apply(self.root)

        self._status_var = tk.StringVar(value="Iniciando...")
        self._cameras = list_cameras()

        self._build_ui()
        self._ensure_models_async()
        self.root.after(500, self._check_driver)

        self.tray = TrayIcon(
            on_show=self.show_window,
            on_toggle=self.show_window,  # clique na bandeja so abre a janela
            on_quit=self.quit,
            is_running=lambda: self.pipeline.running,
        )
        self.tray.run_detached()

        if start_minimized:
            self.root.after(300, self.hide_to_tray)
        self.root.after(1200, self._start_demand_monitor)
        self._tick_preview()
        self._tick_status()

    # ---------- construcao da UI ----------

    # Dimensoes do preview (metade do frame 1280x720).
    _PV_W = WIDTH // 2
    _PV_H = HEIGHT // 2

    def _build_ui(self) -> None:
        th = self._theme
        outer = ttk.Frame(self.root, padding=14)
        outer.pack(fill="both", expand=True)

        # ---- Esquerda: preview ----
        left = ttk.Frame(outer)
        left.grid(row=0, column=0, sticky="n")

        header = ttk.Frame(left)
        header.pack(fill="x")
        ttk.Label(header, text="Pre-visualizacao", style="Title.TLabel").pack(side="left")
        self._preview_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(header, text="Mostrar", variable=self._preview_var,
                        command=self._on_toggle_preview, style="TCheckbutton").pack(side="right")

        # Moldura do preview (borda arredondada simulada com Frame escuro)
        pv_frame = tk.Frame(left, bg=th.BORDER, bd=0)
        pv_frame.pack(pady=(8, 6))
        self._preview_label = tk.Label(pv_frame, bg="#000000", bd=0)
        self._preview_label.pack(padx=1, pady=1)
        self._preview_placeholder = ImageTk.PhotoImage(
            Image.new("RGB", (self._PV_W, self._PV_H), (14, 16, 20)))
        self._preview_label.configure(image=self._preview_placeholder)

        ttk.Label(left, textvariable=self._status_var, style="Status.TLabel",
                  wraplength=self._PV_W).pack(anchor="w", pady=(2, 0))

        # ---- Direita: painel de controles ----
        panel = tk.Frame(outer, bg=th.SURFACE)
        panel.grid(row=0, column=1, sticky="ns", padx=(14, 0))
        pad = {"padx": 14}

        def section(text):
            ttk.Label(panel, text=text.upper(), style="Section.TLabel").pack(
                anchor="w", pady=(14, 4), **pad)

        # Camera
        section("Camera de entrada")
        if not self._cameras:
            self._cameras = [(self.config.camera_index, f"Camera {self.config.camera_index}")]
        self._cam_indices = [i for i, _ in self._cameras]
        self._cam_combo = ttk.Combobox(panel, state="readonly", width=26,
                                       values=[name for _, name in self._cameras])
        idx = (self._cam_indices.index(self.config.camera_index)
               if self.config.camera_index in self._cam_indices else 0)
        self._cam_combo.current(idx)
        self.config.camera_index = self._cam_indices[idx]
        self._cam_combo.bind("<<ComboboxSelected>>", self._on_camera_change)
        self._cam_combo.pack(anchor="w", **pad)

        # Efeitos
        section("Efeitos")
        self._blur_var = tk.BooleanVar(value=self.config.blur_enabled)
        ttk.Checkbutton(panel, text="Desfocar o fundo", variable=self._blur_var,
                        command=self._on_toggle_blur).pack(anchor="w", **pad)
        self._slider(panel, "Intensidade", 3, 75,
                     self.config.blur_strength, self._on_blur_strength)

        self._framing_var = tk.BooleanVar(value=self.config.framing_enabled)
        ttk.Checkbutton(panel, text="Auto-framing (segue o rosto)",
                        variable=self._framing_var,
                        command=self._on_toggle_framing).pack(anchor="w", pady=(6, 0), **pad)
        self._slider(panel, "Zoom", 10, 25,
                     int(self.config.framing_zoom * 10), self._on_zoom)

        ttk.Separator(panel).pack(fill="x", pady=12, **pad)

        # Inicializacao
        self._autostart_var = tk.BooleanVar(value=autostart.is_enabled())
        ttk.Checkbutton(panel, text="Iniciar com o Windows",
                        variable=self._autostart_var,
                        command=self._on_autostart).pack(anchor="w", **pad)

        ttk.Button(panel, text="Minimizar para a bandeja",
                   command=self.hide_to_tray).pack(fill="x", pady=(12, 0), **pad)

        ttk.Label(panel, style="Dim.TLabel", wraplength=240,
                  text="A camera liga sozinha quando voce seleciona "
                       "\"CamFX\" no seu app de video.").pack(
            anchor="w", pady=(12, 14), **pad)

    def _slider(self, parent, label, lo, hi, value, callback):
        th = self._theme
        ttk.Label(parent, text=label, style="Dim.TLabel").pack(
            anchor="w", padx=14, pady=(4, 0))
        var = tk.IntVar(value=value)
        scale = ttk.Scale(parent, from_=lo, to=hi, variable=var, length=232,
                          style="Horizontal.TScale",
                          command=lambda _v: callback(var.get()))
        scale.pack(anchor="w", padx=14, pady=(0, 4))
        return scale

    # ---------- preview ----------

    def _on_toggle_preview(self):
        if not self._preview_var.get():
            # Desligou: volta ao placeholder e para de atualizar.
            self._preview_label.configure(image=self._preview_placeholder)
            self._preview_label.image = self._preview_placeholder

    def _tick_preview(self):
        """Mostra o ultimo frame que esta saindo pela CamFX (lido do arquivo)."""
        if not self._preview_var.get():
            self.root.after(300, self._tick_preview)
            return
        try:
            import os

            if os.path.exists(FRAME_FILE):
                with open(FRAME_FILE, "rb") as f:
                    data = f.read(TOTAL_BYTES)
                if len(data) >= TOTAL_BYTES:
                    magic = struct.unpack("<i", data[0:4])[0]
                    if magic == 0x43414D46:
                        import numpy as np

                        arr = np.frombuffer(data[_HEADER_SIZE:TOTAL_BYTES], dtype=np.uint8)
                        arr = arr.reshape((HEIGHT, WIDTH, 3))[:, :, ::-1]  # BGR->RGB
                        img = Image.fromarray(arr).resize((self._PV_W, self._PV_H))
                        photo = ImageTk.PhotoImage(img)
                        self._preview_label.configure(image=photo)
                        self._preview_label.image = photo
        except Exception:
            pass
        self.root.after(100, self._tick_preview)

    # ---------- modelos ----------

    def _ensure_models_async(self) -> None:
        def work():
            try:
                ensure_models(progress=lambda m: self._set_status(m))
            except Exception as exc:
                self._set_status(f"Falha ao baixar modelos: {exc}")

        threading.Thread(target=work, daemon=True).start()

    def _check_driver(self) -> None:
        if VCamHost is not None:
            from .vcam_host import host_exe_path

            if host_exe_path() is None:
                self._set_status("Camera virtual nao instalada. Rode o instalador do CamFX.")

    # ---------- modo sob demanda (auto total) ----------

    def _start_demand_monitor(self):
        self._vcam_host = VCamHost()
        if self._vcam_host.start():
            log("vcam host MF iniciado")
        else:
            log("vcam host MF nao encontrado")

        try:
            self._demand_monitor = DemandMonitor()
            log("monitor de demanda iniciado")
        except Exception as exc:
            log(f"monitor FALHOU: {exc!r}")
            return

        self._demand_stop = threading.Event()

        def loop():
            mon = self._demand_monitor
            empty_since = None
            last_state = None
            OFF_DELAY = 5.0  # espera antes de desligar (evita liga/desliga rapido)
            while not self._demand_stop.is_set():
                try:
                    consumers = mon.consumer_count()
                except Exception as exc:
                    log(f"consumer_count erro: {exc!r}")
                    consumers = 0
                if consumers != last_state:
                    log(f"demanda: consumers={consumers} pipeline_running={self.pipeline.running}")
                    last_state = consumers
                if consumers > 0:
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

        self._demand_thread = threading.Thread(target=loop, daemon=True)
        self._demand_thread.start()

    # ---------- callbacks de configuracao ----------

    def _on_camera_change(self, _evt=None):
        self.config.camera_index = self._cam_indices[self._cam_combo.current()]
        self.config.save()
        if self.pipeline.running:
            self.pipeline.restart()

    def _on_toggle_blur(self):
        self.config.blur_enabled = self._blur_var.get()
        self.config.save()

    def _on_blur_strength(self, value):
        self.config.blur_strength = int(value)
        self.config.save()

    def _on_toggle_framing(self):
        self.config.framing_enabled = self._framing_var.get()
        self.config.save()

    def _on_zoom(self, value):
        self.config.framing_zoom = int(value) / 10.0
        self.config.save()

    def _on_autostart(self):
        autostart.set_enabled(self._autostart_var.get())

    # ---------- status ----------

    def _set_status(self, msg: str):
        self.root.after(0, lambda: self._status_var.set(msg))

    def _on_pipeline_status(self, msg: str):
        log("pipeline status: " + msg)
        self._set_status(msg)

    def _on_pipeline_error(self, msg: str):
        log("pipeline ERRO: " + msg)
        self._set_status(msg)

    def _tick_status(self):
        if self.pipeline.running:
            self._set_status(f"Transmitindo na CamFX  -  {self.pipeline.fps:.0f} FPS")
        elif self._vcam_host and self._vcam_host.running:
            self._set_status("Pronto. Selecione 'CamFX' no seu app de video.")
        self.root.after(1000, self._tick_status)

    # ---------- janela / bandeja ----------

    def hide_to_tray(self):
        self.root.withdraw()

    def show_window(self):
        self.root.after(0, self._do_show)

    def _do_show(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def quit(self):
        self.config.save()
        if self._demand_stop:
            self._demand_stop.set()
        self.pipeline.stop(join_timeout=2)
        if self._demand_monitor:
            try:
                self._demand_monitor.close()
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
        try:
            self.root.destroy()
        except Exception:
            pass
        import os
        os._exit(0)

    def run(self):
        self.root.mainloop()


def main():
    start_minimized = "--minimized" in sys.argv
    app = CamFXApp(start_minimized=start_minimized)
    app.run()
