<p align="center">
  <img src="assets/logo.png" alt="CamFX" width="180">
</p>

<h1 align="center">CamFX</h1>
<p align="center"><strong>Câmera Virtual com Blur de Fundo e Auto-Framing para Windows 11</strong></p>

**Desfoque de fundo (background blur) e auto-framing em tempo real para a sua webcam, no Windows 11.** O CamFX cria uma câmera virtual que funciona no **Google Meet, Microsoft Teams, Zoom, Discord, OBS e Chrome** — com a qualidade e as cores da sua webcam, rodando na **GPU**. Uma alternativa leve, gratuita e de código aberto ao NVIDIA Broadcast, que aplica o efeito **só na câmera** (nunca no microfone ou nos alto-falantes).

> ⬇️ **[Baixar o CamFX para Windows 11](https://github.com/joaosouz4dev/cam-fx/releases/latest)** — instalador único, sem configuração.

![Windows 11](https://img.shields.io/badge/Windows-11-0078D6?logo=windows11&logoColor=white)
![GPU DirectML](https://img.shields.io/badge/GPU-DirectML-76B900)
![Licença MIT](https://img.shields.io/badge/license-MIT-green)
[![Download](https://img.shields.io/github/v/release/joaosouz4dev/cam-fx?label=download&logo=github)](https://github.com/joaosouz4dev/cam-fx/releases/latest)

---

## Por que CamFX?

Se você usa o NVIDIA Broadcast só para desfocar o fundo da webcam, conhece os incômodos: ele ativa efeitos em **tudo** (microfone e alto-falantes), abre **maximizado** ao ligar o PC e exige uma GPU NVIDIA RTX. O CamFX nasceu para resolver exatamente isso:

- 🎥 **Só na câmera.** Não toca no seu áudio. Faz uma coisa e faz bem.
- ⚡ **Acelerado por GPU** (DirectML — funciona em qualquer placa, não só NVIDIA), com fallback automático para CPU.
- 🪟 **Inicia minimizado na bandeja** e liga a câmera sozinho, sob demanda.
- 🆓 **Gratuito e open source.** Sem conta, sem nuvem, sem telemetria — tudo roda local.

## Recursos

- **Blur de fundo** em tempo real com segmentação por IA (modelo ONNX de selfie segmentation).
- **Auto-framing** opcional que segue o seu rosto, com movimento suave.
- **Câmera virtual nativa do Windows 11** (`MFCreateVirtualCamera` / Media Foundation): aparece como webcam **"CamFX"** em qualquer app de vídeo.
- **Cores fiéis à sua webcam** — captura via Media Foundation, sem adulterar a imagem.
- **720p · 30 FPS**, com baixa latência.
- **Modo automático (sob demanda):** a webcam física liga quando algum app abre a CamFX e desliga quando ninguém usa (a luz da webcam é o indicador).
- **Pré-visualização ao vivo** e controles de efeito na janela.
- **Tema escuro** integrado ao Windows 11.
- **Início com o Windows** (minimizado) opcional.

## Compatibilidade

| Item | Requisito |
| --- | --- |
| Sistema | **Windows 11, versão 22H2 ou superior** (build 22621+) |
| Por quê | A câmera virtual usa a API `MFCreateVirtualCamera`, exclusiva do Windows 11 22H2+ |
| GPU | Opcional (DirectML acelera; qualquer GPU recente serve). Sem GPU, roda na CPU. |
| Apps testados | Google Meet, Microsoft Teams, Zoom, Discord, OBS, app Câmera do Windows |

> Windows 10 não é suportado (a API de câmera virtual não existe nele).

## Instalação

1. Baixe o **`CamFX-Setup.exe`** na [página de releases](https://github.com/joaosouz4dev/cam-fx/releases/latest).
2. Execute o instalador (ele pede permissão de administrador para registrar a câmera virtual).
3. Abra o **CamFX** e, no seu app de vídeo, selecione a webcam **CamFX**.

Nenhum software de terceiros é necessário. O instalador já inclui tudo (app, câmera virtual e modelos de IA).

### Aviso do Windows SmartScreen

O instalador **não é assinado digitalmente** (o CamFX é gratuito e open source). Por isso o Windows pode mostrar "O Windows protegeu o seu PC" / editor desconhecido ao executar. É seguro prosseguir:

1. Clique em **Mais informações**.
2. Clique em **Executar assim mesmo**.

Você também pode auditar todo o código-fonte aqui neste repositório e compilar você mesmo (veja abaixo).

## Como usar

1. Abra o **CamFX** (menu Iniciar ou atalho na área de trabalho). Ele fica na bandeja do sistema.
2. No **Google Meet / Teams / Zoom / Discord / OBS**, escolha a câmera **CamFX**.
3. A webcam liga sozinha em alguns segundos e o vídeo sai com o fundo desfocado.
4. Para conferir/ajustar, abra a janela do CamFX: ligue a **pré-visualização**, ajuste a **intensidade do blur**, o **auto-framing** e escolha **GPU/CPU**.
5. Marque **Iniciar com o Windows** para tê-lo sempre pronto, minimizado.

A câmera física só fica ativa enquanto algum app está usando a CamFX — a luz da sua webcam indica quando está em uso.

---

## Para desenvolvedores

### Rodar a partir do código-fonte

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

Os modelos de IA são baixados automaticamente para `%LOCALAPPDATA%\CamFX\models` na primeira execução.

### Compilar a câmera virtual (driver C++)

Requer **Visual Studio Build Tools** (workload C++) e **Windows SDK 10.0.26100**.

```bash
cd mfref
# Baixe os pacotes NuGet WIL e CppWinRT para mfref/packages/ (zips do nuget.org)
msbuild VCamSampleSource\VCamSampleSource.vcxproj /p:Configuration=Release /p:Platform=x64 ^
  /p:PlatformToolset=v143 /p:WindowsTargetPlatformVersion=10.0.26100.0
```

### Gerar o executável e o instalador

```bash
python build.py                                   # gera dist/CamFX.exe + componentes
ISCC installer\camfx.iss                          # gera installer\output\CamFX-Setup.exe (Inno Setup)
```

> Releases são gerados automaticamente pelo GitHub Actions a cada push na `main` (versão auto-incrementada).

## Arquitetura

```
webcam física
   └─ captura Media Foundation (cor fiel) — thread dedicada
        └─ segmentação ONNX Runtime (GPU DirectML / CPU)
             └─ blur de fundo + composição (escala reduzida p/ performance)
                  └─ memória compartilhada (ProgramData\CamFX\frame.bin)
                       └─ câmera virtual Media Foundation (MFCreateVirtualCamera)
                            └─ webcam "CamFX" em Meet / Teams / Zoom / Discord / OBS
```

| Componente | Responsabilidade |
| --- | --- |
| `camfx/pipeline.py` | orquestra captura (thread) → efeitos → câmera virtual, sob demanda |
| `camfx/segmentation.py` | blur de fundo com segmentação ONNX (GPU/CPU) |
| `camfx/framing.py` | auto-framing (MediaPipe Face Detector) |
| `camfx/virtualcam.py` | escreve frames na memória compartilhada do driver |
| `camfx/vcam_host.py` | mantém a câmera virtual MF viva |
| `camfx/app.py` | interface (tema escuro), preview e controles |
| `mfref/` | câmera virtual Media Foundation em C++ (`MFCreateVirtualCamera`) |
| `installer/camfx.iss` | instalador (Inno Setup) |

## Créditos

Inspirado no projeto web [MediaPipe-Background-Blur-and-Auto-Framing](https://github.com/TakashiYoshinaga/MediaPipe-Background-Blur-and-Auto-Framing) de Takashi Yoshinaga. Câmera virtual baseada no sample [VCamSample](https://github.com/smourier/VCamSample) da Microsoft.

## Licença

[MIT](LICENSE) — use, modifique e distribua livremente.
