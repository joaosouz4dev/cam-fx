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

## Proximos passos

1. Trazer os arquivos restantes do source MF para ca (ou um vcxproj proprio renomeado para CamFX).
2. Renomear "VCamSample"/"VCamSampleSource" -> "CamFX".
3. Fazer o app Python (ou um helper) chamar `MFCreateVirtualCamera` e manter a camera viva, integrando com o modo sob demanda ja existente.
4. Instalador (Inno Setup): copia para Program Files, registra o DLL, cria atalho.
5. Validar no Meet, Teams e Chrome.
