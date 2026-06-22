// CamFX virtual camera host (headless).
//
// Cria a camera virtual Media Foundation (MFCreateVirtualCamera) apontando para
// o nosso source COM e a mantem viva ate o processo ser encerrado. Sem janela,
// sem dialogo: o app CamFX (Python) inicia e encerra este processo conforme a
// demanda. Enquanto ele vive, "CamFX" aparece como webcam em Meet, Teams,
// Chrome, Zoom, Discord, etc.
//
// Uso: camfx_vcam.exe   (roda ate ser morto/Ctrl-C/WM_CLOSE)

#include <windows.h>
#include <mfapi.h>
#include <mfvirtualcamera.h>
#include <combaseapi.h>
#include <sddl.h>
#include <cstdio>
#include "CamFXShared.h"

#pragma comment(lib, "mfsensorgroup.lib")
#pragma comment(lib, "mfplat.lib")
#pragma comment(lib, "ole32.lib")
#pragma comment(lib, "advapi32.lib")

// Cria a memoria compartilhada Global com DACL aberta (todos podem ler/escrever),
// para que o app (sessao do usuario), este helper e o DLL (Frame Server, Local
// Service) compartilhem o mesmo buffer entre sessoes diferentes.
static HANDLE g_shmem = nullptr;
static void Log(const char* fmt, ...)
{
    CreateDirectoryA("C:\\ProgramData\\CamFX", nullptr);
    FILE* f = nullptr; fopen_s(&f, "C:\\ProgramData\\CamFX\\helper.log", "a");
    if (!f) return;
    va_list ap; va_start(ap, fmt); vfprintf(f, fmt, ap); va_end(ap);
    fprintf(f, "\n"); fclose(f);
}
// CLSID do source CamFX (mesmo do VCamSampleSource / dllmain).
// 3cad447d-f283-4af4-a3b2-6f5363309f52
static const wchar_t* CAMFX_CLSID = L"{3cad447d-f283-4af4-a3b2-6f5363309f52}";
static const wchar_t* CAMFX_NAME = L"CamFX";

static HANDLE g_stop = nullptr;

static BOOL WINAPI CtrlHandler(DWORD)
{
    if (g_stop) SetEvent(g_stop);
    return TRUE;
}

int wmain()
{
    // O buffer compartilhado agora e um arquivo em ProgramData (criado pelo app
    // Python). O helper so cria/garante a camera virtual MF.
    HRESULT hr = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
    if (FAILED(hr)) return 1;

    hr = MFStartup(MF_VERSION);
    if (FAILED(hr)) { CoUninitialize(); return 2; }

    IMFVirtualCamera* vcam = nullptr;
    hr = MFCreateVirtualCamera(
        MFVirtualCameraType_SoftwareCameraSource,
        MFVirtualCameraLifetime_Session,
        MFVirtualCameraAccess_CurrentUser,
        CAMFX_NAME,
        CAMFX_CLSID,
        nullptr,
        0,
        &vcam);
    if (FAILED(hr) || !vcam)
    {
        wprintf(L"MFCreateVirtualCamera falhou: 0x%08X\n", hr);
        MFShutdown();
        CoUninitialize();
        return 3;
    }

    hr = vcam->Start(nullptr);
    if (FAILED(hr))
    {
        wprintf(L"VirtualCamera->Start falhou: 0x%08X\n", hr);
        vcam->Remove();
        vcam->Release();
        MFShutdown();
        CoUninitialize();
        return 4;
    }

    wprintf(L"CamFX virtual camera ATIVA. Aguardando (feche o processo para parar).\n");
    fflush(stdout);

    // Mantem viva ate sinal de parada (Ctrl-C, taskkill) ou WM_QUIT.
    g_stop = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    SetConsoleCtrlHandler(CtrlHandler, TRUE);
    WaitForSingleObject(g_stop, INFINITE);

    vcam->Remove();
    vcam->Release();
    MFShutdown();
    CoUninitialize();
    return 0;
}
