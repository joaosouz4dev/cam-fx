"""Icone de bandeja do sistema (pystray).

Permite que o app rode minimizado, fora da barra de tarefas, e seja reaberto
ou encerrado pelo menu. Resolve a queixa do NVIDIA Broadcast de abrir sempre
em janela maximizada ao ligar o PC.
"""

from __future__ import annotations

import threading

import pystray
from PIL import Image, ImageDraw


def _make_icon_image() -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((6, 6, 58, 58), fill=(34, 139, 230, 255))   # lente
    draw.ellipse((20, 20, 44, 44), fill=(15, 23, 42, 255))   # miolo
    draw.ellipse((26, 26, 34, 34), fill=(148, 197, 253, 255))  # reflexo
    return img


class TrayIcon:
    def __init__(self, on_show, on_quit, is_running):
        self._on_show = on_show
        self._on_quit = on_quit
        self._is_running = is_running
        self._icon = pystray.Icon(
            "CamFX",
            _make_icon_image(),
            "CamFX",
            menu=pystray.Menu(
                # Status (apenas informativo): reflete se a camera esta em uso.
                pystray.MenuItem(
                    lambda _: ("●  Camera em uso" if self._is_running()
                               else "○  Em espera"),
                    None, enabled=False,
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Abrir janela", self._show, default=True),
                pystray.MenuItem("Sair", self._quit),
            ),
        )

    def _show(self, *_):
        self._on_show()

    def _quit(self, *_):
        self._icon.stop()
        self._on_quit()

    def run_detached(self) -> None:
        """Roda o loop do icone numa thread separada."""
        threading.Thread(target=self._icon.run, daemon=True).start()

    def stop(self) -> None:
        self._icon.stop()
