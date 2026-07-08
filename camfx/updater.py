"""Auto-atualizacao do CamFX.

Consulta a ultima release publicada no GitHub, compara com a versao instalada
e, se houver uma mais nova, permite baixar e executar o instalador.

- Sem dependencias externas (urllib da stdlib).
- Comparacao por semver (x.y.z).
- Comportamento: AVISA e pergunta (a UI mostra um banner; o usuario decide).
- Download do CamFX-Setup.exe anexado a release, na pasta de dados do usuario.
- Instalacao: roda o instalador (modo silencioso do Inno Setup) e encerra o app
  para que ele possa sobrescrever os arquivos.
"""

from __future__ import annotations

import json
import os
import re
import ssl
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path

from .config import config_dir
from .log import log
from .version import GITHUB_OWNER, GITHUB_REPO, get_version

_API_URL = (
    f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
)
_ASSET_NAME = "CamFX-Setup.exe"
_USER_AGENT = "CamFX-Updater"


def _parse_semver(tag: str) -> tuple[int, int, int]:
    """'v1.2.3' / '1.2.3' -> (1, 2, 3). Partes ausentes viram 0."""
    nums = re.findall(r"\d+", tag or "")
    parts = [int(n) for n in nums[:3]]
    while len(parts) < 3:
        parts.append(0)
    return parts[0], parts[1], parts[2]


def is_newer(remote: str, local: str) -> bool:
    return _parse_semver(remote) > _parse_semver(local)


def _http_get_json(url: str, timeout: float = 8.0) -> dict | None:
    req = urllib.request.Request(url, headers={
        "User-Agent": _USER_AGENT,
        "Accept": "application/vnd.github+json",
    })
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return json.loads(resp.read().decode("utf-8"))


def check_for_update() -> dict | None:
    """Retorna info da atualizacao se houver uma mais nova, senao None.

    {version, tag, url (download do exe), notes, html_url}
    """
    local = get_version()
    try:
        data = _http_get_json(_API_URL)
    except Exception as exc:
        log(f"updater: falha ao consultar GitHub: {exc!r}")
        return None
    if not data:
        return None
    tag = data.get("tag_name") or ""
    remote = tag.lstrip("v")
    if not remote or not is_newer(remote, local):
        return None

    # O instalador pode se chamar "CamFX-Setup.exe" (antigo) ou
    # "CamFX-Setup-<versao>.exe" (novo, com a versao no nome). Casa por prefixo
    # + sufixo para funcionar com os dois.
    asset_url = None
    asset_name = None
    for asset in data.get("assets", []) or []:
        name = asset.get("name") or ""
        low = name.lower()
        if low.startswith("camfx-setup") and low.endswith(".exe"):
            asset_url = asset.get("browser_download_url")
            asset_name = name
            break
    if not asset_url:
        log("updater: release sem CamFX-Setup*.exe anexado")
        return None

    return {
        "version": remote,
        "tag": tag,
        "url": asset_url,
        "asset_name": asset_name,
        "notes": data.get("body") or "",
        "html_url": data.get("html_url") or "",
    }


def _updates_dir() -> Path:
    d = config_dir() / "updates"
    d.mkdir(parents=True, exist_ok=True)
    return d


def download_installer(url: str, on_progress=None,
                       filename: str | None = None) -> Path | None:
    """Baixa o instalador. on_progress(recebidos, total) e opcional.

    `filename`: nome do arquivo de saida (o asset da release, ex.:
    CamFX-Setup-0.0.20.exe). Se ausente, usa o nome padrao."""
    name = filename or _ASSET_NAME
    if not name.lower().endswith(".exe"):
        name = _ASSET_NAME
    dest = _updates_dir() / name
    tmp = dest.with_suffix(".part")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            got = 0
            with open(tmp, "wb") as f:
                while True:
                    chunk = resp.read(256 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    got += len(chunk)
                    if on_progress:
                        try:
                            on_progress(got, total)
                        except Exception:
                            pass
        if dest.exists():
            dest.unlink()
        tmp.rename(dest)
        return dest
    except Exception as exc:
        log(f"updater: falha no download: {exc!r}")
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
        return None


def run_installer(installer: Path) -> bool:
    """Roda o instalador (Inno Setup silencioso) e devolve True se iniciou.

    /SILENT mostra a barra de progresso mas nao faz perguntas; /CLOSEAPPLICATIONS
    e /RESTARTAPPLICATIONS deixam o Inno Setup fechar/reabrir o CamFX. Como o
    instalador precisa de admin, o UAC vai aparecer.
    """
    if not installer.exists():
        return False
    try:
        subprocess.Popen(
            [str(installer), "/SILENT", "/CLOSEAPPLICATIONS", "/RESTARTAPPLICATIONS"],
            close_fds=True,
        )
        return True
    except Exception as exc:
        log(f"updater: falha ao iniciar instalador: {exc!r}")
        return False


class UpdateChecker:
    """Checa atualizacoes ao iniciar e a cada `interval_s` (padrao 6h).

    Quando encontra uma nova versao, chama on_update(info) (uma vez por versao).
    A checagem roda numa thread daemon; nunca derruba o app em caso de erro.
    """

    def __init__(self, on_update, interval_s: float = 6 * 60 * 60):
        self._on_update = on_update
        self._interval = interval_s
        self._stop = threading.Event()
        self._thread = None
        self._notified_version = None

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def check_now(self) -> dict | None:
        info = check_for_update()
        if info and info["version"] != self._notified_version:
            self._notified_version = info["version"]
            try:
                self._on_update(info)
            except Exception as exc:
                log(f"updater: on_update falhou: {exc!r}")
        return info

    def _loop(self):
        # Primeira checagem logo apos abrir (pequeno atraso para nao competir
        # com a inicializacao da janela/servicos).
        if self._stop.wait(8.0):
            return
        while not self._stop.is_set():
            self.check_now()
            if self._stop.wait(self._interval):
                return
