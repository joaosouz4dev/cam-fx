# Plano técnico: resolução dinâmica no driver CamFX (C++ Media Foundation)

Estado: **a base Python + o contrato da shmem já estão feitos e testados**
(commit `feat(shmem): resolucao dinamica...`). Falta a reescrita do driver C++
(`mfcam/src/`). Este documento é o guia cirúrgico dessa reescrita, para ser
feita **num ambiente com Visual Studio / MSBuild** (a máquina atual não tem
toolchain C++, então nada aqui foi compilado nem testado).

## Por que o driver precisa mudar

Hoje o driver trava a saída em **1280×720 @ 30fps** em três pontos que já
foram parcialmente ajustados no contrato, mas o código de runtime ainda assume
720p fixo:

- `MediaStream.cpp` anuncia aos apps só 1 tamanho (720p) — `NUM_IMAGE_COLS/ROWS`.
- `FrameGenerator.cpp` cria o render target/bitmap/converter no tamanho
  negociado e **rejeita** frames cujo header não bata (`linha 69`).
- O buffer da shmem já foi ampliado para 1080p (`CAMFX_MAX_*`, feito).

## Contrato já estabelecido (feito, não mexer)

- `CamFXShared.h`: buffer = `CAMFX_MAX_FRAME_BYTES` (1920×1080×3). Cada frame
  carrega `width`/`height` reais no `CamFXSharedHeader`.
- `virtualcam.py`: grava a resolução real no header; `send()` faz passthrough
  quando o frame já está no tamanho alvo.

## As 4 mudanças no C++ (em ordem de dependência)

### 1. `MediaStream.cpp` — anunciar múltiplas resoluções

Hoje anuncia 2 media types (RGB32 + NV12), ambos 720p. Trocar por uma **lista
de resoluções comuns**, cada uma em RGB32 e NV12:

- 1280×720, 1920×1080 (e opcionalmente 640×480 para compat).
- Para cada (w,h): criar rgbType + nv12Type como hoje, mas com `MF_MT_FRAME_SIZE`
  = (w,h) e `MF_MT_DEFAULT_STRIDE` recalculado.
- O array `types` passa de tamanho 2 para 2×N. `MFCreateStreamDescriptor`
  recebe o array inteiro.
- **Risco**: alguns apps pegam o PRIMEIRO media type; ordenar do maior para o
  menor faz o app preferir HD, mas alguns apps de baixa banda podem querer o
  menor. Testar em Meet/Teams/Zoom/Discord.

### 2. `MediaStream::Start` — capturar a resolução escolhida

Hoje `Start(type)` só lê o `MF_MT_SUBTYPE`. Adicionar: ler
`MFGetAttributeSize(type, MF_MT_FRAME_SIZE, &w, &h)` e passar (w,h) para
`_generator.EnsureRenderTarget(w, h)` em vez de `NUM_IMAGE_COLS/ROWS` fixos.
Mesmo para `SetD3DManager` (que hoje passa 720p fixo, linha 119).

### 3. `FrameGenerator` — render target dinâmico + ler resolução do header

- `EnsureRenderTarget(w,h)` / `SetD3DManager(...,w,h)` já recebem w/h; garantir
  que os chamadores passem a resolução negociada (item 2), não a constante.
- `FillBitmapFromCamFX` (linha 51): **remover a rejeição** da linha 69
  (`if (hdr->width != _width ...) return false;`). Em vez disso:
  - Se `hdr->width/height` == `_width/_height`: copiar direto (caso comum).
  - Se diferem: o app negociou X mas o Python está enviando Y. Duas opções:
    (a) o Python deve enviar exatamente a resolução negociada — ver item 4;
    (b) o C++ reescala (custa CPU/GPU). Preferir (a): o Python entrega na
    resolução certa, e o C++ só copia.
- O loop BGR→BGRA (linhas 83-92) usa `_width/_height` — ok, desde que batam
  com o frame. Manter.

### 4. Python descobre a resolução negociada (o loop bidirecional)

Este é o ponto mais sutil. O app de vídeo escolhe UMA das resoluções
anunciadas; o Python precisa entregar NAQUELA. Como o Python sabe qual?

- Adicionar um campo no `CamFXSharedHeader`: `neg_width`, `neg_height`
  (a resolução que o driver negociou com o app). O driver ESCREVE esses
  campos em `Start`/`SetCurrentMediaType`; o Python LÊ e ajusta `self.width/
  height` do `CamFXVirtualCamera` para casar.
- Requer estender a struct em `CamFXShared.h` E em `virtualcam.py` (o
  `_HEADER_FMT`) de forma sincronizada. Cuidado com o offset de `consumers`
  (o heartbeat) — não deslocar sem atualizar os dois lados.
- Enquanto o app não negociou (ou negociou 0), o Python usa um default (a
  resolução da câmera, limitada ao MAX).

## Sequência de teste (com build disponível)

1. Compilar o driver (`build_driver.ps1`) — corrigir erros de compilação.
2. Reinstalar (`setup_driver.ps1`, admin).
3. Testar UMA resolução por vez: começar anunciando só 1080p; confirmar que o
   frame aparece certo e sem judder em Camera do Windows.
4. Adicionar 720p à lista; testar a negociação em Meet, Teams, Zoom, Discord
   (cada um negocia diferente).
5. Só então o loop bidirecional (item 4) para casar Python↔app.

## Riscos conhecidos (não-testados)

- **Renegociação em runtime**: se o app troca de resolução no meio (raro), o
  render target precisa ser recriado — hoje não é. Pode travar.
- **NV12 stride**: 1080p tem stride diferente; recalcular em todos os pontos.
- **Pool de allocator** (`InitializeSampleAllocator(10, type)`): 10 buffers de
  1080p = mais memória e latência; considerar reduzir para 3-4.
- **Apps que só aceitam 720p**: manter 720p na lista como fallback.

## Alternativa mais segura (se a dinâmica der muito trabalho)

Fixar a saída em **1080p** (constantes, sem negociação): muda `NUM_IMAGE_COLS/
ROWS` para 1920×1080 nos 3 pontos, sem lista nem loop bidirecional. Câmeras
720p são ampliadas (leve perda), mas é uma mudança de ~10 linhas testável de
uma vez. ~90% do ganho de qualidade com uma fração do risco.
