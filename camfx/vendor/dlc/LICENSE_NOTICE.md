# Aviso de licença - motor Deep-Live-Cam (vendorizado)

Os arquivos em `camfx/vendor/dlc/modules/` sao derivados do projeto
**Deep-Live-Cam** (hacksider/Deep-Live-Cam), licenciado sob **AGPL-3.0**.

- Fonte original: https://github.com/hacksider/Deep-Live-Cam
- Licenca: GNU Affero General Public License v3.0 (AGPL-3.0)

Modificacoes feitas para o CamFX:
- `modules/core.py` foi substituido por um stub minimo (so `update_status`),
  para nao arrastar a UI Tkinter (customtkinter) e o tensorflow do projeto
  original, que nao sao usados no caminho do face swap.
- O pacote e carregado sob o nome `modules` via `camfx/vendor/dlc/__init__.py`
  (ensure_engine), sem alterar os imports internos dos modulos originais.

**Implicacao de licenca:** por incorporar codigo AGPL-3.0, a distribuicao do
CamFX que inclui este diretorio fica sujeita a AGPL-3.0. Alem disso, o modelo
`inswapper` (InsightFace) usado pelo motor e licenciado apenas para pesquisa /
uso nao comercial. Portanto, esta funcionalidade e destinada a **uso nao
comercial**. Ver `camfx/terms.py` e o README.
