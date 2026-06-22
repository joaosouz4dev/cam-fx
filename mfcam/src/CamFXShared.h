// Protocolo de memoria compartilhada entre o app CamFX (Python) e este source
// Media Foundation. O app escreve frames BGR; o source le e entrega aos apps
// de video (Meet, Teams, Zoom, Discord, Camera do Windows, etc.).
#pragma once
#include <windows.h>

#define CAMFX_WIDTH   640
#define CAMFX_HEIGHT  480
#define CAMFX_FPS     30
#define CAMFX_FRAME_BYTES (CAMFX_WIDTH * CAMFX_HEIGHT * 3)   // BGR24

#define CAMFX_SHMEM_NAME  L"Local\\CamFXFrameBuffer"
#define CAMFX_MUTEX_NAME  L"Local\\CamFXFrameMutex"

#pragma pack(push, 1)
struct CamFXSharedHeader {
    volatile LONG magic;        // CAMFX_MAGIC quando o app esta enviando
    volatile LONG width;
    volatile LONG height;
    volatile LONG frame_seq;
    volatile LONGLONG ts_qpc;
    volatile LONG consumers;    // quantos apps consomem a CamFX agora
};
#pragma pack(pop)

#define CAMFX_MAGIC 0x43414D46  // 'CAMF'
#define CAMFX_SHMEM_BYTES (sizeof(CamFXSharedHeader) + CAMFX_FRAME_BYTES)
