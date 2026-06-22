# CamFX

Blur de fundo e auto-framing aplicados **apenas na webcam**. Um substituto leve e focado do NVIDIA Broadcast: os efeitos rodam so na camera, nunca no microfone nem nos alto-falantes, e o app inicia minimizado na bandeja em vez de abrir maximizado no boot.

Inspirado no projeto web [MediaPipe-Background-Blur-and-Auto-Framing](https://github.com/TakashiYoshinaga/MediaPipe-Background-Blur-and-Auto-Framing) de Takashi Yoshinaga, reescrito como app de desktop em Python com **driver de camera virtual proprio**, para o video processado aparecer como uma webcam chamada **CamFX** em Zoom, Google Meet, Discord, Teams e OBS, sem depender do OBS.

## Funcionalidades

- Selecao da camera de entrada do PC.
- Blur de fundo via MediaPipe Image Segmenter (selfie segmentation).
- Auto-framing que segue o rosto via MediaPipe Face Detector, com movimento suavizado.
- Saida como camera virtual **CamFX** (driver DirectShow proprio, sem OBS), selecionavel em qualquer app de video.
- Bandeja do sistema: rodar minimizado, pausar e retomar.
- Iniciar com o Windows ja minimizado (opcional).
- Configuracoes persistidas entre execucoes.

## Requisitos

- Windows 10/11.
- Python 3.10+ (testado em 3.12).
- Nenhum software de terceiros. O CamFX traz e registra o proprio driver de camera virtual (`driver/CamFXSource.dll`) na primeira execucao, com permissao de administrador.

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
