"""Termos de uso do CamFX (salvaguarda do recurso de face swap).

O face swap (troca de rosto em tempo real) e uma tecnologia de deepfake. Antes
de habilita-la, o usuario precisa aceitar os termos de uso uma vez. O aceite
fica registrado na config (terms_accepted/terms_version) e tambem num arquivo
de auditoria separado, que sobrevive a um reset da config.

Para revisar os termos (mudanca legal), basta incrementar TERMS_VERSION: o app
volta a exigir o aceite.
"""

from __future__ import annotations

import json
import time

from .config import Config, config_dir
from .log import log

# Incremente quando o texto dos termos mudar de forma relevante.
TERMS_VERSION = 1

_AUDIT_PATH = config_dir() / "terms_accepted.json"

# Texto dos termos. Mantido aqui para versionar junto do codigo; a UI tambem
# pode exibi-lo. Sem travessoes (padrao do projeto).
TERMS_TEXT = """\
CamFX - Termos de Uso do recurso de troca de rosto (face swap)

O recurso de troca de rosto usa inteligencia artificial para substituir, em
tempo real, o rosto captado pela camera pelo rosto de uma foto escolhida por
voce. O resultado e uma imagem sintetica (deepfake).

Ao ativar e usar este recurso, voce declara que concorda com o seguinte:

1. Uso responsavel. Voce nao usara o recurso para fraude, falsa identidade,
   difamacao, conteudo sexual ou intimo nao consentido, assedio, ou qualquer
   finalidade ilegal ou que viole direitos de terceiros.

2. Consentimento. Voce e responsavel por ter o consentimento das pessoas cujas
   imagens (rosto-fonte e rosto-alvo) forem utilizadas.

3. Transparencia. Ao compartilhar ou transmitir o resultado, deixe claro,
   quando apropriado, que a imagem foi gerada ou alterada por IA.

4. Licenca dos modelos. Os modelos de IA usados nesta versao podem ter licenca
   apenas para pesquisa/uso nao comercial. Verifique a licenca antes de
   qualquer uso comercial.

5. Sem garantias. O recurso e fornecido "como esta", sem garantias. Voce e o
   unico responsavel pelo uso que fizer dele.

O nao cumprimento destes termos e de sua inteira responsabilidade. Os autores
do CamFX nao se responsabilizam pelo uso indevido do recurso.
"""


def needs_acceptance(config: Config) -> bool:
    """True se o usuario ainda precisa aceitar (nunca aceitou ou versao antiga)."""
    return not config.terms_accepted or config.terms_version < TERMS_VERSION


def accept(config: Config, app_version: str = "") -> None:
    """Registra o aceite na config e num arquivo de auditoria."""
    config.terms_accepted = True
    config.terms_version = TERMS_VERSION
    config.save()
    try:
        record = {
            "version": TERMS_VERSION,
            "accepted_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "app_version": app_version,
        }
        _AUDIT_PATH.write_text(json.dumps(record, indent=2), encoding="utf-8")
    except Exception as exc:
        log(f"terms: falha ao gravar auditoria: {exc!r}")


def text() -> str:
    return TERMS_TEXT
