// Protocolo de memoria compartilhada entre o app CamFX (Python) e o driver
// DirectShow. O app escreve frames BGR aqui; o filtro le e entrega aos
// aplicativos de video (Zoom, Discord, OBS, etc.).
#pragma once
#include <windows.h>

// Resolucao fixa do canal de video virtual. O app deve enviar frames neste
// tamanho (640x480 BGR 24 bits). Mantido simples de proposito.
#define CAMFX_WIDTH   640
#define CAMFX_HEIGHT  480
#define CAMFX_FPS     30
#define CAMFX_BPP     3            // BGR24
#define CAMFX_FRAME_BYTES (CAMFX_WIDTH * CAMFX_HEIGHT * CAMFX_BPP)

// Nomes dos objetos de kernel compartilhados (escopo de sessao do usuario).
#define CAMFX_SHMEM_NAME  L"Local\\CamFXFrameBuffer"
#define CAMFX_MUTEX_NAME  L"Local\\CamFXFrameMutex"

// Layout do bloco compartilhado: cabecalho + pixels.
#pragma pack(push, 1)
struct CamFXSharedHeader {
    volatile LONG magic;        // CAMFX_MAGIC quando o app esta ativo
    volatile LONG width;        // largura atual (deve bater com CAMFX_WIDTH)
    volatile LONG height;       // altura atual
    volatile LONG frame_seq;    // incrementa a cada frame novo do app
    volatile LONGLONG ts_qpc;   // timestamp (QueryPerformanceCounter) do frame
};
#pragma pack(pop)

#define CAMFX_MAGIC 0x43414D46  // 'CAMF'
#define CAMFX_SHMEM_BYTES (sizeof(CamFXSharedHeader) + CAMFX_FRAME_BYTES)
