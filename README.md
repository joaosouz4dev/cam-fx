# CamFX

Blur de fundo e auto-framing aplicados **apenas na webcam**. Um substituto leve e focado do NVIDIA Broadcast: os efeitos rodam so na camera, nunca no microfone nem nos alto-falantes, e o app inicia minimizado na bandeja em vez de abrir maximizado no boot.

Inspirado no projeto web [MediaPipe-Background-Blur-and-Auto-Framing](https://github.com/TakashiYoshinaga/MediaPipe-Background-Blur-and-Auto-Framing) de Takashi Yoshinaga, reescrito como app de desktop em Python com **camera virtual Media Foundation propria**, para o video processado aparecer como uma webcam chamada **CamFX** em **Google Meet, Microsoft Teams, Chrome, Zoom, Discord e OBS**, sem depender do OBS.

## Funcionalidades

- Blur de fundo via MediaPipe Image Segmenter (selfie segmentation).
- Auto-framing que segue o rosto via MediaPipe Face Detector, com movimento suavizado.
- Saida como camera virtual **CamFX** via Media Foundation (`MFCreateVirtualCamera`), enxergada por Meet/Teams/Chrome e todos os apps de video.
- Cores iguais as da webcam (captura via Media Foundation, sem adulterar a cor).
- 30 FPS.
- **Modo automatico (sob demanda):** a webcam fisica liga sozinha quando um app abre a CamFX e desliga quando ninguem usa (a luz da webcam indica). Sem botao de ligar/pausar.
- Janela com pre-visualizacao ao vivo e controles de efeito.
- Bandeja do sistema e inicio com o Windows (minimizado).

## Instalacao (usuario final)

Rode o **CamFX-Setup.exe** (gerado em `installer/output/`). Ele instala o app, o
driver de camera virtual e registra tudo automaticamente (pede permissao de
administrador). Nenhum software de terceiros e necessario.

## Requisitos

- **Windows 11 22H2+** (a API `MFCreateVirtualCamera` exige; build 22621 ou superior).
- Para desenvolvimento: Python 3.10+ (testado em 3.12) e, para compilar o driver, Visual Studio Build Tools com C++ e Windows SDK 10.0.26100.

## O driver de camera virtual

O `driver/` contem um source filter DirectShow em C++ que registra a camera "CamFX" no Windows. O app escreve os frames processados numa memoria compartilhada (`camfx/virtualcam.py`) que o driver le e entrega aos aplicativos de video.

Para compilar o driver (requer Visual Studio Build Tools com C++ e Windows SDK):

```bash
cd driver
build_baseclasses.bat   # compila o DirectShow BaseClasses (uma vez)
build_driver.bat        # gera CamFXSource.dll
```

O DirectShow BaseClasses e baixado em `driver/baseclasses/` a partir do repositorio Windows-classic-samples da Microsoft.

## Instalacao (a partir do fonte)

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

Na primeira execucao, os modelos `.tflite` do MediaPipe sao baixados automaticamente para `%LOCALAPPDATA%\CamFX\models`.

## Gerar o executavel

```bash
pip install pyinstaller
python build.py
```

O `CamFX.exe` aparece em `dist/`. Os modelos e o `CamFXSource.dll` ficam embutidos no executavel.

## Como usar

1. Abra o CamFX. Na primeira vez, ele pede permissao para instalar o driver da camera virtual (admin, uma unica vez).
2. Escolha sua camera.
3. Ligue blur e/ou auto-framing e ajuste a intensidade.
4. Clique em **Ligar camera**.
5. No Zoom/Meet/Discord/OBS, selecione a webcam **CamFX**.
6. Clique em **Minimizar** para mandar para a bandeja.

Para iniciar junto com o Windows minimizado, marque **Iniciar com o Windows**.

### Observacoes sobre a camera

- A captura tenta os backends do Windows em ordem (Media Foundation, depois DirectShow), porque algumas webcams nao abrem por DirectShow. A mensagem "Abrindo camera..." aparece enquanto isso acontece.
- Algumas webcams levam de 5 a 15 segundos para abrir pelo Media Foundation na primeira vez. E normal; aguarde o status "Camera virtual ativa".
- A resolucao padrao e 640x480, que abre rapido na maioria das cameras. Resolucoes mais altas funcionam, mas podem aumentar bastante o tempo de abertura.
- Se aparecer erro ao abrir, confira se a camera nao esta em uso por outro programa e se o acesso esta liberado em Configuracoes do Windows > Privacidade e seguranca > Camera.

## Arquitetura

```
camera fisica
   -> OpenCV (captura)
      -> auto-framing  (FaceDetector + corte suavizado)
         -> blur de fundo (ImageSegmenter + composicao alpha)
            -> memoria compartilhada (camfx/virtualcam.py)
               -> driver DirectShow CamFX (driver/CamFXSource.dll)
                  -> webcam "CamFX" em Zoom / Meet / Discord / OBS
```

| Modulo | Responsabilidade |
| --- | --- |
| `camfx/segmentation.py` | mascara da pessoa e blur do fundo |
| `camfx/framing.py` | deteccao de rosto e enquadramento |
| `camfx/pipeline.py` | thread de captura -> efeitos -> camera virtual |
| `camfx/virtualcam.py` | escreve frames na memoria compartilhada do driver |
| `camfx/driver_setup.py` | registro do driver CamFX (regsvr32 elevado) |
| `camfx/app.py` | interface Tkinter |
| `camfx/tray.py` | icone e menu da bandeja |
| `camfx/autostart.py` | registro de inicio com o Windows |
| `camfx/models.py` | download e cache dos modelos |
| `camfx/config.py` | configuracoes persistidas |
| `driver/` | source filter DirectShow em C++ (camera virtual CamFX) |

## Licenca

MIT.
