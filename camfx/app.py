"""Interface grafica do CamFX (Tkinter).

Tela unica com: selecao de camera, liga/desliga blur e auto-framing, controles
de intensidade, autostart e botao para minimizar para a bandeja.
"""

from __future__ import annotations

import sys
import tkinter as tk
from tkinter import messagebox, ttk

from . import autostart
from .config import Config
from .models import ensure_models
from .pipeline import Pipeline, list_cameras
from .tray import TrayIcon


class CamFXApp:
    def __init__(self, start_minimized: bool = False) -> None:
        self.config = Config.load()
        self.pipeline = Pipeline(self.config)
        self.pipeline.on_error = self._on_pipeline_error
        self.pipeline.on_status = self._on_pipeline_status

        self.root = tk.Tk()
        self.root.title("CamFX - blur e auto-framing da camera")
        self.root.geometry("420x560")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)

        self._status_var = tk.StringVar(value="Pronto.")
        self._cameras = list_cameras()

        self._build_ui()
        self._ensure_models_async()

        self.tray = TrayIcon(
            on_show=self.show_window,
            on_toggle=self._toggle_capture,
            on_quit=self.quit,
            is_running=lambda: self.pipeline.running,
        )
        self.tray.run_detached()

        if start_minimized or self.config.start_minimized:
            self.root.after(300, self.hide_to_tray)
        if self.config.autostart_capture:
            self.root.after(1200, self._start_capture)

    # ---------- construcao da UI ----------

    def _build_ui(self) -> None:
        pad = {"padx": 12, "pady": 6}
        frm = ttk.Frame(self.root)
        frm.pack(fill="both", expand=True, padx=8, pady=8)

        ttk.Label(frm, text="Camera de entrada").pack(anchor="w", **pad)
        # _cameras e uma lista de (indice, nome).
        if not self._cameras:
            self._cameras = [(self.config.camera_index, f"Camera {self.config.camera_index}")]
        self._cam_indices = [i for i, _ in self._cameras]
        self._cam_combo = ttk.Combobox(
            frm,
            state="readonly",
            values=[name for _, name in self._cameras],
        )
        idx = (
            self._cam_indices.index(self.config.camera_index)
            if self.config.camera_index in self._cam_indices
            else 0
        )
        self._cam_combo.current(idx)
        # Mantem o config alinhado com o que esta selecionado.
        self.config.camera_index = self._cam_indices[idx]
        self._cam_combo.bind("<<ComboboxSelected>>", self._on_camera_change)
        self._cam_combo.pack(fill="x", **pad)

        # Efeitos
        self._blur_var = tk.BooleanVar(value=self.config.blur_enabled)
        ttk.Checkbutton(
            frm, text="Blur de fundo", variable=self._blur_var,
            command=self._on_toggle_blur,
        ).pack(anchor="w", **pad)
        self._blur_scale = self._slider(
            frm, "Intensidade do blur", 3, 75, self.config.blur_strength,
            self._on_blur_strength,
        )

        self._framing_var = tk.BooleanVar(value=self.config.framing_enabled)
        ttk.Checkbutton(
            frm, text="Auto-framing (segue o rosto)", variable=self._framing_var,
            command=self._on_toggle_framing,
        ).pack(anchor="w", **pad)
        self._zoom_scale = self._slider(
            frm, "Zoom do auto-framing (x10)", 10, 25,
            int(self.config.framing_zoom * 10), self._on_zoom,
        )

        ttk.Separator(frm).pack(fill="x", pady=8)

        # Inicializacao
        self._autostart_var = tk.BooleanVar(value=autostart.is_enabled())
        ttk.Checkbutton(
            frm, text="Iniciar com o Windows (minimizado)",
            variable=self._autostart_var, command=self._on_autostart,
        ).pack(anchor="w", **pad)

        self._startmin_var = tk.BooleanVar(value=self.config.start_minimized)
        ttk.Checkbutton(
            frm, text="Abrir minimizado na bandeja",
            variable=self._startmin_var, command=self._on_startmin,
        ).pack(anchor="w", **pad)

        # Botoes
        btns = ttk.Frame(frm)
        btns.pack(fill="x", **pad)
        self._toggle_btn = ttk.Button(
            btns, text="Ligar camera", command=self._toggle_capture
        )
        self._toggle_btn.pack(side="left", expand=True, fill="x", padx=(0, 4))
        ttk.Button(
            btns, text="Minimizar", command=self.hide_to_tray
        ).pack(side="left", expand=True, fill="x", padx=(4, 0))

        ttk.Label(
            frm, textvariable=self._status_var, foreground="#2563eb", wraplength=380
        ).pack(anchor="w", **pad)

        self._fps_var = tk.StringVar(value="")
        ttk.Label(frm, textvariable=self._fps_var).pack(anchor="w", padx=12)
        self._tick_fps()

    def _slider(self, parent, label, lo, hi, value, callback):
        ttk.Label(parent, text=label).pack(anchor="w", padx=12)
        var = tk.IntVar(value=value)
        scale = ttk.Scale(
            parent, from_=lo, to=hi, variable=var,
            command=lambda _v: callback(var.get()),
        )
        scale.pack(fill="x", padx=12, pady=(0, 6))
        return scale

    # ---------- modelos ----------

    def _ensure_models_async(self) -> None:
        import threading

        def work():
            try:
                ensure_models(progress=lambda m: self._set_status(m))
                self._set_status("Modelos prontos.")
            except Exception as exc:
                self._set_status(f"Falha ao baixar modelos: {exc}")

        threading.Thread(target=work, daemon=True).start()

    # ---------- callbacks de configuracao ----------

    def _on_camera_change(self, _evt=None):
        sel = self._cam_combo.current()
        self.config.camera_index = self._cam_indices[sel]
        self.config.save()
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

    def _on_startmin(self):
        self.config.start_minimized = self._startmin_var.get()
        self.config.save()

    # ---------- captura ----------

    def _start_capture(self):
        self.pipeline.start()
        self._refresh_toggle_label()

    def _toggle_capture(self):
        if self.pipeline.running:
            self.pipeline.stop()
        else:
            self.pipeline.start()
        self.root.after(200, self._refresh_toggle_label)

    def _refresh_toggle_label(self):
        self._toggle_btn.config(
            text="Pausar camera" if self.pipeline.running else "Ligar camera"
        )

    # ---------- status ----------

    def _set_status(self, msg: str):
        # Tkinter so e thread-safe pela main loop.
        self.root.after(0, lambda: self._status_var.set(msg))

    def _on_pipeline_status(self, msg: str):
        self._set_status(msg)
        self.root.after(0, self._refresh_toggle_label)

    def _on_pipeline_error(self, msg: str):
        self._set_status(msg)
        self.root.after(0, lambda: messagebox.showerror("CamFX", msg))
        self.root.after(0, self._refresh_toggle_label)

    def _tick_fps(self):
        if self.pipeline.running:
            self._fps_var.set(f"{self.pipeline.fps:.0f} FPS")
        else:
            self._fps_var.set("")
        self.root.after(500, self._tick_fps)

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
        self.pipeline.stop()
        try:
            self.tray.stop()
        except Exception:
            pass
        self.root.after(0, self.root.destroy)

    def run(self):
        self.root.mainloop()


def main():
    start_minimized = "--minimized" in sys.argv
    app = CamFXApp(start_minimized=start_minimized)
    app.run()
