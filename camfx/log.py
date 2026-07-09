"""Log simples em arquivo, util para diagnosticar o .exe (que roda sem console).

Licao da saga do face swap: o log ANTES engolia qualquer erro de escrita
(`except: pass`) e nao dizia QUAL processo/thread gerou cada linha. Com varias
copias do exe rodando (instalado vs build local), as linhas se misturavam e as
conclusoes saiam erradas. Agora cada linha leva pid+thread, e um erro de escrita
cai num fallback (startup.log) em vez de sumir. Ver a memoria
"isolar-evidencia-antes-de-teorizar".
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from .config import config_dir

_LOG_PATH = config_dir() / "camfx.log"

# Logs de debug (marcos finos de _loop/swap) so vao ao arquivo se ligado. Assim
# producao fica limpa, mas da para reativar o rastreamento sem rebuild:
# defina CAMFX_DEBUG=1 no ambiente antes de abrir o app.
_DEBUG = os.environ.get("CAMFX_DEBUG", "").strip().lower() in ("1", "true", "on", "yes")


def _fallback(msg: str) -> None:
    """Ultimo recurso quando nem o camfx.log aceita a escrita: grava no
    startup.log (mesma pasta do log de startup do main). Nunca levanta."""
    try:
        from .config import data_file
        with open(data_file("startup.log"), "a", encoding="utf-8") as f:
            f.write(f"[log-fallback] {msg}\n")
    except Exception:
        pass


def _write(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    pid = os.getpid()
    tid = threading.current_thread().name
    line = f"{ts} [{pid}/{tid}] {msg}\n"
    try:
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as exc:
        # Nao engole em silencio: registra o proprio erro no fallback para nao
        # perder linhas sem deixar rastro (o que aconteceu na saga).
        _fallback(f"falha ao escrever no camfx.log ({exc!r}): {msg}")


def log(msg: str) -> None:
    """Log de nivel normal (INFO): sempre gravado."""
    _write(msg)


def log_debug(msg: str) -> None:
    """Log de diagnostico fino (marcos de _loop/swap): so gravado com
    CAMFX_DEBUG ligado. Mantem producao limpa sem perder a instrumentacao."""
    if _DEBUG:
        _write(f"DEBUG {msg}")


def debug_enabled() -> bool:
    return _DEBUG


def log_path() -> Path:
    return _LOG_PATH
