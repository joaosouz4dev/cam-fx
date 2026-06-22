# CamFX - camera virtual Media Foundation (prova de conceito)

Esta pasta contem a base da **nova** camera virtual do CamFX, usando a API
`MFCreateVirtualCamera` do Windows 11 (Media Foundation). Diferente do driver
DirectShow anterior (em `../driver/`), esta abordagem aparece em **TODOS** os
apps, incluindo **Google Meet, Teams e Chrome**, que so enxergam cameras via
Media Foundation.

## Estado atual (prova de conceito validada)

- ✅ A API `MFCreateVirtualCamera` existe e funciona nesta maquina (Win 11 build 26200).
- ✅ O source MF compila na toolchain local (VS BuildTools v143 + SDK 10.0.26100 + WIL + CppWinRT).
- ✅ A camera virtual foi criada e **capturada via Media Foundation** (a mesma API do Meet) - confirmado com um frame real de 640x480/1280x960.
- ✅ O `FrameGenerator` foi customizado para ler frames BGR da **memoria compartilhada** do app CamFX (`CamFXShared.h`, mesmo protocolo de `../camfx/virtualcam.py`).
- ⏳ Falta: ciclo final de build/instalacao limpo, integracao com o app Python rodando, validacao no Meet e instalador.

## Base

Construido sobre o sample oficial da Microsoft
[smourier/VCamSample](https://github.com/smourier/VCamSample). Os arquivos em
`src/` sao os que customizamos:

| Arquivo | Mudanca |
| --- | --- |
| `CamFXShared.h` | protocolo da memoria compartilhada (frames BGR + contador de consumidores) |
| `FrameGenerator.h/.cpp` | le o frame da shmem e preenche o bitmap (caminho CPU, usado por Meet/Chrome); cai na tela de demo se nao ha frame do app |
| `MediaStream.cpp` | resolucao alinhada para 640x480 (CAMFX_WIDTH/HEIGHT) |

## Como reproduzir o build (ate termos um script proprio)

1. Clonar o VCamSample da Microsoft.
2. Baixar os pacotes NuGet WIL e CppWinRT para `packages/` (sao zips do nuget.org).
3. Copiar os arquivos de `src/` por cima dos do sample.
4. Compilar com MSBuild:
   `msbuild VCamSampleSource\VCamSampleSource.vcxproj /p:Configuration=Release /p:Platform=x64 /p:PlatformToolset=v143 /p:WindowsTargetPlatformVersion=10.0.26100.0`
5. Copiar o DLL para uma pasta acessivel ao Frame Server (ex.: `C:\Program Files\CamFX\`) - NAO deixar em pasta de usuario, da "Acesso negado".
6. `regsvr32` no DLL (admin).
7. Chamar `MFCreateVirtualCamera` (via o exe registrador) para ativar a camera.

## Armadilhas conhecidas

- O DLL precisa estar num local acessivel a servicos do sistema (Frame Server roda como Local Service). Pasta de usuario causa "Acesso negado".
- Entre recompilacoes, o Frame Server (svchost) segura o DLL. E preciso reiniciar o servico FrameServer/FrameServerMonitor ou o PC para liberar o arquivo.
- A camera virtual MF NAO aparece em `Get-PnpDevice`; so via enumeracao Media Foundation (ex.: OpenCV CAP_MSMF, ou os proprios apps).
- A camera vive enquanto o processo que chamou `MFCreateVirtualCamera` estiver rodando. No produto final, o proprio app CamFX deve manter esse processo.

## Progresso (atualizado)

- ✅ Helper headless `camfx_vcam.cpp`: cria a camera MF (`MFCreateVirtualCamera`) e a mantem viva sem janela/dialogo. Funciona: a camera "CamFX" aparece via Media Foundation (640x480).
- ✅ DLL le a shmem e tem o caminho CPU (Meet/Chrome) preparado para copiar o frame BGR do app.
- ⚠️ **BLOQUEIO ATUAL - IPC entre sessoes:** o app Python roda na sessao do usuario; o DLL do source roda no **Frame Server (svchost, Local Service)**, outra sessao. A memoria compartilhada precisa ser do namespace `Global\` para cruzar sessoes. Problemas encontrados:
  - Python sem admin NAO cria/abre `Global\` (erro 5, falta SeCreateGlobalPrivilege).
  - O helper, mesmo elevado, falha ao criar `Global\` (err=5) apesar de `AdjustTokenPrivileges` retornar 0 - o `SeCreateGlobalPrivilege` aparentemente nao esta presente/efetivo no token nesta maquina/politica.

## PROXIMO PASSO RECOMENDADO: trocar shmem por arquivo mapeado em disco

Em vez de `CreateFileMapping(INVALID_HANDLE_VALUE, "Global\\...")`, usar um
**arquivo real mapeado** em `C:\ProgramData\CamFX\frame.bin` (pasta acessivel a
todas as sessoes/contas, incluindo Local Service). File-backed mapping funciona
entre sessoes SEM precisar de namespace Global nem privilegio especial:
- App Python: abre/cria o arquivo, MapViewOfFile, escreve frames.
- DLL (Frame Server): abre o mesmo arquivo, MapViewOfFile, le.
- Sincronizacao: usar o proprio campo `frame_seq` (escrita atomica) ou um mutex
  com DACL aberta; ou ate dispensar mutex (frame parcial ocasional e toleravel).
Garantir DACL aberta na pasta ProgramData\CamFX (o instalador cria com permissao
para todos).

## Demais passos

1. Implementar o file-backed mapping (acima) nos 3 lados (camfx_vcam.cpp/helper, FrameGenerator.cpp/DLL, camfx/virtualcam.py).
2. Validar o video com blur chegando no Meet/Teams/Chrome.
3. Renomear "VCamSample"/"VCamSampleSource" -> "CamFX" e integrar o helper ao modo sob demanda do app.
4. Instalador (Inno Setup): copia para Program Files, registra o DLL, cria pasta ProgramData com DACL, cria atalho.
