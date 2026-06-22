// CamFX - source filter DirectShow que expoe uma camera virtual chamada "CamFX".
//
// Le frames BGR24 de uma memoria compartilhada preenchida pelo app CamFX
// (Python) e os entrega aos aplicativos de video. Quando nao ha frame fresco,
// mostra uma tela de espera, para a camera nunca "travar" o app consumidor.
//
// Baseado na arquitetura PushSource das DirectShow Base Classes.

#include <streams.h>
#include <stdio.h>
#include "shared.h"
#include "guids.h"

// ---------------------------------------------------------------------------
// Stream de saida: gera os frames.
// ---------------------------------------------------------------------------
class CamFXStream : public CSourceStream
{
public:
    CamFXStream(HRESULT* phr, CSource* pParent, LPCWSTR pName);
    ~CamFXStream();

    // CSourceStream
    HRESULT GetMediaType(CMediaType* pmt);
    HRESULT CheckMediaType(const CMediaType* pmt);
    HRESULT DecideBufferSize(IMemAllocator* pAlloc, ALLOCATOR_PROPERTIES* pRequest);
    HRESULT FillBuffer(IMediaSample* pms);

    // Controle de tempo
    HRESULT OnThreadCreate();
    HRESULT OnThreadDestroy();

private:
    void OpenSharedMemory();
    void CloseSharedMemory();
    void SignalConsumer(LONG delta);    // +1 ao comecar, -1 ao parar de consumir
    void DrawWaitingScreen(BYTE* dst);  // tela quando nao ha frame do app

    REFERENCE_TIME m_rtFrameLength;     // duracao de um frame (100ns)
    REFERENCE_TIME m_rtSampleTime;      // tempo acumulado
    LONG           m_lastSeq;           // ultimo frame_seq consumido

    HANDLE m_hMap;                      // file mapping da memoria compartilhada
    HANDLE m_hMutex;                    // mutex de acesso
    BYTE*  m_pShared;                   // ponteiro mapeado
    CCritSec m_cs;
};

// ---------------------------------------------------------------------------
// Filtro: contem o stream.
// ---------------------------------------------------------------------------
class CamFXSource : public CSource
{
public:
    static CUnknown* WINAPI CreateInstance(LPUNKNOWN lpunk, HRESULT* phr);

private:
    CamFXSource(LPUNKNOWN lpunk, HRESULT* phr);
};

// ===========================================================================
// CamFXSource
// ===========================================================================
CamFXSource::CamFXSource(LPUNKNOWN lpunk, HRESULT* phr)
    : CSource(NAME("CamFX Source"), lpunk, CLSID_CamFXSource)
{
    m_paStreams = (CSourceStream**) new CamFXStream*[1];
    if (m_paStreams == NULL) {
        if (phr) *phr = E_OUTOFMEMORY;
        return;
    }
    m_paStreams[0] = new CamFXStream(phr, this, L"CamFX");
    if (m_paStreams[0] == NULL && phr) *phr = E_OUTOFMEMORY;
}

CUnknown* WINAPI CamFXSource::CreateInstance(LPUNKNOWN lpunk, HRESULT* phr)
{
    CamFXSource* p = new CamFXSource(lpunk, phr);
    if (p == NULL && phr) *phr = E_OUTOFMEMORY;
    return p;
}

// ===========================================================================
// CamFXStream
// ===========================================================================
CamFXStream::CamFXStream(HRESULT* phr, CSource* pParent, LPCWSTR pName)
    : CSourceStream(NAME("CamFX Stream"), phr, pParent, pName),
      m_rtSampleTime(0),
      m_lastSeq(-1),
      m_hMap(NULL),
      m_hMutex(NULL),
      m_pShared(NULL)
{
    m_rtFrameLength = UNITS / CAMFX_FPS;   // UNITS = 10.000.000 (100ns)
}

CamFXStream::~CamFXStream()
{
    CloseSharedMemory();
}

void CamFXStream::OpenSharedMemory()
{
    if (m_pShared) return;
    m_hMutex = CreateMutexW(NULL, FALSE, CAMFX_MUTEX_NAME);
    // Abre como leitura+escrita: o driver le frames e tambem atualiza o
    // contador de consumidores. Se a shmem ainda nao existe (app fechado),
    // cria para conseguir sinalizar a presenca de um consumidor.
    m_hMap = OpenFileMappingW(FILE_MAP_ALL_ACCESS, FALSE, CAMFX_SHMEM_NAME);
    if (!m_hMap) {
        m_hMap = CreateFileMappingW(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE,
            0, CAMFX_SHMEM_BYTES, CAMFX_SHMEM_NAME);
    }
    if (m_hMap) {
        m_pShared = (BYTE*)MapViewOfFile(m_hMap, FILE_MAP_ALL_ACCESS, 0, 0,
            CAMFX_SHMEM_BYTES);
    }
}

void CamFXStream::SignalConsumer(LONG delta)
{
    if (!m_pShared) OpenSharedMemory();
    if (!m_pShared) return;
    CamFXSharedHeader* hdr = (CamFXSharedHeader*)m_pShared;
    InterlockedExchangeAdd(&hdr->consumers, delta);
    if (hdr->consumers < 0) InterlockedExchange(&hdr->consumers, 0);
}

void CamFXStream::CloseSharedMemory()
{
    if (m_pShared) { UnmapViewOfFile(m_pShared); m_pShared = NULL; }
    if (m_hMap)    { CloseHandle(m_hMap);   m_hMap = NULL; }
    if (m_hMutex)  { CloseHandle(m_hMutex); m_hMutex = NULL; }
}

HRESULT CamFXStream::OnThreadCreate()
{
    m_rtSampleTime = 0;
    m_lastSeq = -1;
    OpenSharedMemory();
    SignalConsumer(+1);   // um app comecou a consumir a CamFX
    return NOERROR;
}

HRESULT CamFXStream::OnThreadDestroy()
{
    SignalConsumer(-1);   // o app parou de consumir
    CloseSharedMemory();
    return NOERROR;
}

// Tela de espera: gradiente azul escuro (mesma identidade do icone do app).
void CamFXStream::DrawWaitingScreen(BYTE* dst)
{
    for (int y = 0; y < CAMFX_HEIGHT; y++) {
        for (int x = 0; x < CAMFX_WIDTH; x++) {
            BYTE* px = dst + (y * CAMFX_WIDTH + x) * 3;
            px[0] = (BYTE)(60 + (x * 40 / CAMFX_WIDTH));   // B
            px[1] = (BYTE)(30 + (y * 20 / CAMFX_HEIGHT));  // G
            px[2] = 20;                                    // R
        }
    }
}

HRESULT CamFXStream::FillBuffer(IMediaSample* pms)
{
    CheckPointer(pms, E_POINTER);
    BYTE* pData = NULL;
    pms->GetPointer(&pData);
    long lSize = pms->GetSize();
    if (lSize < CAMFX_FRAME_BYTES) return E_FAIL;

    bool gotFrame = false;

    if (!m_pShared) OpenSharedMemory();

    if (m_pShared) {
        DWORD wait = WaitForSingleObject(m_hMutex, 30);
        if (wait == WAIT_OBJECT_0 || wait == WAIT_ABANDONED) {
            CamFXSharedHeader* hdr = (CamFXSharedHeader*)m_pShared;
            if (hdr->magic == CAMFX_MAGIC &&
                hdr->width == CAMFX_WIDTH && hdr->height == CAMFX_HEIGHT) {
                BYTE* src = m_pShared + sizeof(CamFXSharedHeader);
                // DirectShow RGB de cima para baixo precisa de flip vertical:
                // o app envia top-down BGR; copiamos invertendo as linhas.
                for (int y = 0; y < CAMFX_HEIGHT; y++) {
                    BYTE* srcRow = src + (size_t)y * CAMFX_WIDTH * 3;
                    BYTE* dstRow = pData + (size_t)(CAMFX_HEIGHT - 1 - y) * CAMFX_WIDTH * 3;
                    memcpy(dstRow, srcRow, CAMFX_WIDTH * 3);
                }
                m_lastSeq = hdr->frame_seq;
                gotFrame = true;
            }
            ReleaseMutex(m_hMutex);
        }
    }

    if (!gotFrame) {
        DrawWaitingScreen(pData);
    }

    // Marca os tempos do sample para manter a cadencia de FPS.
    REFERENCE_TIME rtStart = m_rtSampleTime;
    m_rtSampleTime += m_rtFrameLength;
    pms->SetTime(&rtStart, &m_rtSampleTime);
    pms->SetSyncPoint(TRUE);

    // Regula o ritmo: dorme ate o proximo frame.
    Sleep(1000 / CAMFX_FPS);
    return NOERROR;
}

HRESULT CamFXStream::GetMediaType(CMediaType* pmt)
{
    CheckPointer(pmt, E_POINTER);
    CAutoLock cAutoLock(m_pFilter->pStateLock());

    VIDEOINFOHEADER* pvi = (VIDEOINFOHEADER*)pmt->AllocFormatBuffer(sizeof(VIDEOINFOHEADER));
    if (pvi == NULL) return E_OUTOFMEMORY;
    ZeroMemory(pvi, sizeof(VIDEOINFOHEADER));

    pvi->bmiHeader.biSize        = sizeof(BITMAPINFOHEADER);
    pvi->bmiHeader.biWidth       = CAMFX_WIDTH;
    pvi->bmiHeader.biHeight      = CAMFX_HEIGHT;
    pvi->bmiHeader.biPlanes      = 1;
    pvi->bmiHeader.biBitCount    = 24;
    pvi->bmiHeader.biCompression = BI_RGB;
    pvi->bmiHeader.biSizeImage   = CAMFX_FRAME_BYTES;
    pvi->AvgTimePerFrame         = m_rtFrameLength;

    SetRectEmpty(&pvi->rcSource);
    SetRectEmpty(&pvi->rcTarget);

    pmt->SetType(&MEDIATYPE_Video);
    pmt->SetFormatType(&FORMAT_VideoInfo);
    pmt->SetTemporalCompression(FALSE);
    pmt->SetSubtype(&MEDIASUBTYPE_RGB24);
    pmt->SetSampleSize(CAMFX_FRAME_BYTES);
    return NOERROR;
}

HRESULT CamFXStream::CheckMediaType(const CMediaType* pmt)
{
    CheckPointer(pmt, E_POINTER);
    if (*pmt->Type() != MEDIATYPE_Video) return E_INVALIDARG;
    if (*pmt->Subtype() != MEDIASUBTYPE_RGB24) return E_INVALIDARG;
    if (*pmt->FormatType() != FORMAT_VideoInfo) return E_INVALIDARG;
    VIDEOINFOHEADER* pvi = (VIDEOINFOHEADER*)pmt->Format();
    if (pvi == NULL) return E_INVALIDARG;
    if (pvi->bmiHeader.biWidth != CAMFX_WIDTH ||
        pvi->bmiHeader.biHeight != CAMFX_HEIGHT) return E_INVALIDARG;
    return S_OK;
}

HRESULT CamFXStream::DecideBufferSize(IMemAllocator* pAlloc, ALLOCATOR_PROPERTIES* pRequest)
{
    CheckPointer(pAlloc, E_POINTER);
    CheckPointer(pRequest, E_POINTER);
    CAutoLock cAutoLock(m_pFilter->pStateLock());

    if (pRequest->cBuffers == 0) pRequest->cBuffers = 2;
    pRequest->cbBuffer = CAMFX_FRAME_BYTES;

    ALLOCATOR_PROPERTIES Actual;
    HRESULT hr = pAlloc->SetProperties(pRequest, &Actual);
    if (FAILED(hr)) return hr;
    if (Actual.cbBuffer < pRequest->cbBuffer) return E_FAIL;
    return NOERROR;
}

// ===========================================================================
// Registro COM
// ===========================================================================
const AMOVIESETUP_MEDIATYPE sudPinTypes =
{
    &MEDIATYPE_Video,
    &MEDIASUBTYPE_NULL
};

const AMOVIESETUP_PIN sudPin =
{
    L"Output",          // nome do pin
    FALSE,              // rendered
    TRUE,               // output
    FALSE,              // zero instances
    FALSE,              // many instances
    &CLSID_NULL,
    NULL,
    1,
    &sudPinTypes
};

const AMOVIESETUP_FILTER sudFilter =
{
    &CLSID_CamFXSource,
    CAMFX_FILTER_NAME,
    MERIT_DO_NOT_USE,
    1,
    &sudPin
};

CFactoryTemplate g_Templates[] =
{
    {
        CAMFX_FILTER_NAME,
        &CLSID_CamFXSource,
        CamFXSource::CreateInstance,
        NULL,
        &sudFilter
    }
};
int g_cTemplates = sizeof(g_Templates) / sizeof(g_Templates[0]);

// Registra/desregistra tambem na categoria de dispositivos de captura de video,
// para o filtro aparecer como CAMERA nos aplicativos (e nao so como filtro).
STDAPI RegisterFilters(BOOL bRegister)
{
    HRESULT hr;
    IFilterMapper2* pFM2 = NULL;

    hr = CoInitialize(NULL);
    if (bRegister) {
        hr = AMovieDllRegisterServer2(TRUE);
        if (FAILED(hr)) { CoUninitialize(); return hr; }
    }

    hr = CoCreateInstance(CLSID_FilterMapper2, NULL, CLSCTX_INPROC_SERVER,
                          IID_IFilterMapper2, (void**)&pFM2);
    if (SUCCEEDED(hr)) {
        if (bRegister) {
            IMoniker* pMoniker = NULL;
            REGFILTER2 rf2;
            rf2.dwVersion = 1;
            rf2.dwMerit = MERIT_DO_NOT_USE;
            rf2.cPins = 1;
            rf2.rgPins = &sudPin;
            hr = pFM2->RegisterFilter(CLSID_CamFXSource, CAMFX_FILTER_NAME,
                &pMoniker, &CLSID_VideoInputDeviceCategory, NULL, &rf2);
            if (pMoniker) pMoniker->Release();
        } else {
            hr = pFM2->UnregisterFilter(&CLSID_VideoInputDeviceCategory, NULL,
                CLSID_CamFXSource);
        }
        pFM2->Release();
    }

    if (!bRegister) {
        AMovieDllRegisterServer2(FALSE);
    }

    CoUninitialize();
    return hr;
}

STDAPI DllRegisterServer()   { return RegisterFilters(TRUE); }
STDAPI DllUnregisterServer() { return RegisterFilters(FALSE); }

extern "C" BOOL WINAPI DllEntryPoint(HINSTANCE, ULONG, LPVOID);
BOOL APIENTRY DllMain(HANDLE hModule, DWORD dwReason, LPVOID lpReserved)
{
    return DllEntryPoint((HINSTANCE)hModule, dwReason, lpReserved);
}
