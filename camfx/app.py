"""Interface grafica do CamFX (Tkinter).

Tela unica com: selecao de camera, liga/desliga blur e auto-framing, controles
de intensidade, autostart e botao para minimizar para a bandeja.
"""

from __future__ import annotations

import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from . import autostart, driver_setup
from .config import Config
from .log import log
from .models import ensure_models
from .pipeline import Pipeline, list_cameras
from .tray import TrayIcon


class CamFXApp:
    def __init__(self, start_minimized: bool = False) -> None:
        self.config = Config.load()
        self.pipeline = Pipeline(self.config)
        self.pipeline.on_error = self._on_pipeline_error
        self.pipeline.on_status = self._on_pipeline_status
        self._demand_monitor = None
        self._demand_thread = None
        self._demand_stop = None
        self._manual_override = False  # True quando o usuario forcou ligar/pausar

        self.root = tk.Tk()
        self.root.title("CamFX - blur e auto-framing da camera")
        self.root.geometry("420x560")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)

        self._status_var = tk.StringVar(value="Pronto.")
        self._cameras = list_cameras()

        self._build_ui()
        self._ensure_models_async()
        self.root.after(500, self._check_driver)

        self.tray = TrayIcon(
            on_show=self.show_window,
            on_toggle=self._toggle_capture,
            on_quit=self.quit,
            is_running=lambda: self.pipeline.running,
        )
        self.tray.run_detached()

        if start_minimized or self.config.start_minimized:
            self.root.after(300, self.hide_to_tray)
        # Modo sob demanda: nao abre a camera ao iniciar. Um monitor liga o
        # pipeline so quando algum app abre a camera CamFX.
        self.root.after(1500, self._start_demand_monitor)

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

    def _check_driver(self) -> None:
        """Na primeira vez, oferece registrar o driver da camera virtual CamFX."""
        if driver_setup.is_registered():
            return
        resp = messagebox.askyesno(
            "CamFX - instalacao do driver",
            "A camera virtual CamFX ainda nao esta instalada neste computador.\n\n"
            "Para que a CamFX apareca como webcam no Zoom, Meet, Discord e OBS, "
            "preciso registrar o driver uma unica vez (vai pedir permissao de "
            "administrador).\n\nInstalar agora?",
        )
        if not resp:
            self._set_status("Driver da CamFX nao instalado. Os efeitos nao sairao como webcam.")
            return
        ok, msg = driver_setup.register()
        if ok:
            self._set_status("Driver CamFX instalado. Selecione 'CamFX' como webcam nos apps.")
            # reenumera cameras (a CamFX deve ser filtrada da entrada)
            self._cameras = list_cameras()
        else:
            messagebox.showwarning("CamFX", f"Nao foi possivel instalar o driver: {msg}")

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

    # ---------- modo sob demanda ----------

    def _start_demand_monitor(self):
        """Monitora consumidores da CamFX e liga/desliga o pipeline sozinho."""
        import threading

        from .virtualcam import DemandMonitor

        try:
            self._demand_monitor = DemandMonitor()
            log("monitor de demanda iniciado")
        except Exception as exc:
            log(f"monitor FALHOU: {exc!r}")
            self._set_status(f"Monitor indisponivel: {exc}. Use 'Ligar camera'.")
            return

        self._demand_stop = threading.Event()

        def loop():
            mon = self._demand_monitor
            empty_since = None  # quando o contador ficou sem consumidores
            last_c = -1
            # Espera antes de desligar: evita liga/desliga em ciclos rapidos
            # (apps frequentemente abrem e fecham a camera ao listar/testar),
            # o que estressa o driver MSMF e pode trava-lo.
            OFF_DELAY = 5.0
            while not self._demand_stop.is_set():
                try:
                    consumers = mon.consumer_count()
                except Exception as exc:
                    log(f"consumer_count erro: {exc!r}")
                    consumers = 0
                if consumers != last_c:
                    log(f"consumers={consumers} running={self.pipeline.running}")
                    last_c = consumers
                if not self._manual_override:
                    if consumers > 0:
                        empty_since = None
                        if not self.pipeline.running:
                            self._set_status("Um app abriu a CamFX. Ligando camera...")
                            self.pipeline.start()
                    elif self.pipeline.running:
                        if empty_since is None:
                            empty_since = time.monotonic()
                        elif time.monotonic() - empty_since >= OFF_DELAY:
                            self._set_status("Nenhum app usando a CamFX. Desligando camera.")
                            threading.Thread(
                                target=self.pipeline.stop, daemon=True
                            ).start()
                            empty_since = None
                self.root.after(0, self._refresh_toggle_label)
                self._demand_stop.wait(1.0)

        self._demand_thread = threading.Thread(target=loop, daemon=True)
        self._demand_thread.start()
        self._set_status("Pronto. A camera liga sozinha quando voce abrir a CamFX num app.")

    # ---------- captura ----------

    def _start_capture(self):
        self.pipeline.start()
        self._refresh_toggle_label()

    def _toggle_capture(self):
        # Botao manual: assume o controle, ignorando o modo sob demanda ate
        # o usuario soltar (volta ao automatico ao ligar de novo sem app).
        if self.pipeline.running:
            self._manual_override = False  # pausar volta ao modo automatico
            threading.Thread(target=self.pipeline.stop, daemon=True).start()
        else:
            self._manual_override = True   # ligar manual mantem ligado
            self.pipeline.start()
        self.root.after(300, self._refresh_toggle_label)

    def _refresh_toggle_label(self):
        self._toggle_btn.config(
            text="Pausar camera" if self.pipeline.running else "Ligar camera"
        )

    # ---------- status ----------

    def _set_status(self, msg: str):
        # Tkinter so e thread-safe pela main loop.
        self.root.after(0, lambda: self._status_var.set(msg))

    def _on_pipeline_status(self, msg: str):
        log("pipeline status: " + msg)
        self._set_status(msg)
        self.root.after(0, self._refresh_toggle_label)

    def _on_pipeline_error(self, msg: str):
        log("pipeline ERRO: " + msg)
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
        if self._demand_stop:
            self._demand_stop.set()
        # Nao bloqueia o fechamento esperando a camera (MSMF pode estar abrindo).
        self.pipeline.stop(join_timeout=2)
        if self._demand_monitor:
            try:
                self._demand_monitor.close()
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
        # Garante o encerramento mesmo se alguma thread nativa (MSMF/regsvr32)
        # ficar presa, evitando processo zumbi do CamFX.
        import os
        os._exit(0)

    def run(self):
        self.root.mainloop()


def main():
    start_minimized = "--minimized" in sys.argv
    app = CamFXApp(start_minimized=start_minimized)
    app.run()
