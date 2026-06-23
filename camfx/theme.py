"""Tema escuro para a interface Tkinter do CamFX.

O ttk padrao no Windows nao respeita o dark mode do sistema, entao aplicamos
uma paleta escura propria nos widgets. Tambem pinta a barra de titulo de escuro
no Windows 11 (DWM).
"""

from __future__ import annotations

import ctypes
import tkinter as tk
from tkinter import ttk

# Paleta (estilo Windows 11 dark).
BG = "#1e1e1e"          # fundo da janela
SURFACE = "#2b2b2b"     # cartoes/areas
SURFACE_2 = "#333333"   # campos
BORDER = "#3d3d3d"
TEXT = "#e6e6e6"
TEXT_DIM = "#9a9a9a"
ACCENT = "#2f81f7"      # azul de destaque
ACCENT_HOVER = "#4892f9"


def apply(root: tk.Tk) -> ttk.Style:
    root.configure(bg=BG)
    _dark_titlebar(root)

    style = ttk.Style(root)
    style.theme_use("clam")

    style.configure(".", background=BG, foreground=TEXT,
                    fieldbackground=SURFACE_2, bordercolor=BORDER,
                    lightcolor=BG, darkcolor=BG, focuscolor=ACCENT)
    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=SURFACE)
    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure("Card.TLabel", background=SURFACE, foreground=TEXT)
    style.configure("Title.TLabel", background=BG, foreground=TEXT,
                    font=("Segoe UI Semibold", 11))
    style.configure("Section.TLabel", background=SURFACE, foreground=TEXT_DIM,
                    font=("Segoe UI", 9))
    style.configure("Dim.TLabel", background=SURFACE, foreground=TEXT_DIM,
                    font=("Segoe UI", 8))
    style.configure("Status.TLabel", background=BG, foreground=ACCENT,
                    font=("Segoe UI", 9))

    style.configure("TCheckbutton", background=SURFACE, foreground=TEXT,
                    focuscolor=SURFACE)
    style.map("TCheckbutton",
              background=[("active", SURFACE)],
              indicatorcolor=[("selected", ACCENT), ("!selected", SURFACE_2)])

    style.configure("TButton", background=SURFACE_2, foreground=TEXT,
                    bordercolor=BORDER, focusthickness=0, padding=6)
    style.map("TButton", background=[("active", BORDER)])

    style.configure("Accent.TButton", background=ACCENT, foreground="#ffffff",
                    padding=6)
    style.map("Accent.TButton", background=[("active", ACCENT_HOVER)])

    style.configure("TCombobox", fieldbackground=SURFACE_2, background=SURFACE_2,
                    foreground=TEXT, arrowcolor=TEXT, bordercolor=BORDER,
                    padding=4)
    style.map("TCombobox", fieldbackground=[("readonly", SURFACE_2)],
              selectbackground=[("readonly", SURFACE_2)],
              selectforeground=[("readonly", TEXT)])

    style.configure("Horizontal.TScale", background=SURFACE,
                    troughcolor=SURFACE_2, bordercolor=BORDER)
    style.configure("TSeparator", background=BORDER)

    # dropdown da combobox (usa option db do Tk, nao ttk)
    root.option_add("*TCombobox*Listbox.background", SURFACE_2)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
    return style


def _dark_titlebar(root: tk.Tk) -> None:
    """Pinta a barra de titulo de escuro no Windows 11."""
    try:
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        # DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        value = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
    except Exception:
        pass
