#include "pch.h"
#include "Undocumented.h"
#include "Tools.h"
#include "EnumNames.h"
#include "MFTools.h"
#include "FrameGenerator.h"
#include "CamFXShared.h"

// Log de diagnostico do DLL (roda no Frame Server). Grava em ProgramData, que
// e acessivel a servicos. Remover depois de validar.
static void CamFXLog(const char* fmt, ...)
{
    CreateDirectoryA("C:\\ProgramData\\CamFX", nullptr);
    FILE* f = nullptr;
    fopen_s(&f, "C:\\ProgramData\\CamFX\\dll.log", "a");
    if (!f) return;
    va_list ap; va_start(ap, fmt);
    vfprintf(f, fmt, ap);
    va_end(ap);
    fprintf(f, "\n");
    fclose(f);
}

// Abre (uma vez) a memoria compartilhada do app CamFX. Cria se ainda nao existe,
// para que o contador de consumidores ja funcione antes do app enviar frames.
void FrameGenerator::OpenCamFXSharedMemory()
{
    if (_camfxShared) return;
    // Abre o arquivo compartilhado com leitura+escrita (le frames, escreve o
    // contador de consumidores) e o mapeia.
    _camfxFile = CreateFileW(CAMFX_FRAME_FILE, GENERIC_READ | GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr, OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL, nullptr);
    DWORD openErr = GetLastError();
    if (_camfxFile != INVALID_HANDLE_VALUE)
    {
        _camfxMap = CreateFileMappingW(_camfxFile, nullptr, PAGE_READWRITE,
            0, CAMFX_SHMEM_BYTES, nullptr);
        if (_camfxMap)
        {
            _camfxShared = (BYTE*)MapViewOfFile(_camfxMap, FILE_MAP_ALL_ACCESS, 0, 0,
                CAMFX_SHMEM_BYTES);
        }
    }
    CamFXLog("OpenSharedMemory(file): file=%p (err=%lu) map=%p mapped=%p",
        (void*)_camfxFile, openErr, (void*)_camfxMap, (void*)_camfxShared);
}

// Copia o frame BGR da shmem para o _bitmap WIC (BGRA premultiplicado).
// Retorna true se havia um frame valido do app; false para usar tela de espera.
bool FrameGenerator::FillBitmapFromCamFX()
{
    if (!_camfxShared) OpenCamFXSharedMemory();
    if (!_camfxShared || !_bitmap) return false;

    auto hdr = (CamFXSharedHeader*)_camfxShared;
    // Heartbeat: grava o tick atual a cada frame pedido. O Generate so e chamado
    // enquanto algum app consome a camera; quando o consumidor para, este tick
    // para de atualizar e o app detecta (timestamp antigo) para desligar a
    // webcam. Usamos o campo 'consumers' para carregar esse tick (ms).
    InterlockedExchange(&hdr->consumers, (LONG)GetTickCount());

    static int logCount = 0;
    if (logCount++ % 60 == 0)
        CamFXLog("FillBitmap: magic=0x%08X want=0x%08X w=%ld h=%ld",
            hdr->magic, CAMFX_MAGIC, hdr->width, hdr->height);

    if (hdr->magic != CAMFX_MAGIC) return false;

    // RESOLUCAO DINAMICA: o app envia frames na resolucao REAL (no header).
    // Idealmente ela casa com a negociada (_width/_height); o app Python entrega
    // na resolucao alvo. Mas por seguranca copiamos so o MINIMO entre o frame
    // do header e o bitmap negociado - nunca lemos/escrevemos alem do menor dos
    // dois (evita estouro se, por um instante de renegociacao, divergirem).
    const LONG fw = hdr->width;
    const LONG fh = hdr->height;
    if (fw <= 0 || fh <= 0) return false;
    if (fw > CAMFX_MAX_WIDTH || fh > CAMFX_MAX_HEIGHT) return false;  // sanidade

    const UINT copyW = (UINT)((fw < (LONG)_width) ? fw : (LONG)_width);
    const UINT copyH = (UINT)((fh < (LONG)_height) ? fh : (LONG)_height);

    bool copied = false;
    const BYTE* src = _camfxShared + sizeof(CamFXSharedHeader);
    wil::com_ptr_nothrow<IWICBitmapLock> lock;
    if (SUCCEEDED(_bitmap->Lock(nullptr, WICBitmapLockWrite, &lock)))
    {
        UINT stride = 0, size = 0;
        WICInProcPointer dst = nullptr;
        lock->GetStride(&stride);
        lock->GetDataPointer(&size, &dst);
        if (dst)
        {
            // BGR (3 bytes) -> BGRA (4 bytes), por linha. O src usa o stride do
            // FRAME (fw*3, resolucao real do header); o dst usa o stride do
            // BITMAP (negociado). Copiamos so copyW x copyH.
            for (UINT y = 0; y < copyH; y++)
            {
                const BYTE* s = src + (size_t)y * fw * 3;
                BYTE* d = dst + (size_t)y * stride;
                for (UINT x = 0; x < copyW; x++)
                {
                    d[0] = s[0]; d[1] = s[1]; d[2] = s[2]; d[3] = 255;
                    s += 3; d += 4;
                }
            }
            copied = true;
        }
    }
    return copied;
}

HRESULT FrameGenerator::EnsureRenderTarget(UINT width, UINT height)
{
	// RESOLUCAO DINAMICA: se a resolucao mudou desde a ultima vez (ex.: o app
	// renegociou de 720p para 1080p), descarta o bitmap/render target CPU antigo
	// para recria-lo no tamanho novo. No caminho GPU a textura e recriada em
	// SetD3DManager; aqui tratamos o caminho CPU.
	if (_bitmap && (_width != width || _height != height))
	{
		_bitmap.reset();
		_renderTarget.reset();
		_whiteBrush.reset();
	}

	if (!HasD3DManager() && !_bitmap)
	{
		// create a D2D1 render target from WIC bitmap
		wil::com_ptr_nothrow<ID2D1Factory> d2d1Factory;
		RETURN_IF_FAILED(D2D1CreateFactory(D2D1_FACTORY_TYPE_MULTI_THREADED, IID_PPV_ARGS(&d2d1Factory)));

		wil::com_ptr_nothrow<IWICImagingFactory> wicFactory;
		RETURN_IF_FAILED(CoCreateInstance(CLSID_WICImagingFactory, nullptr, CLSCTX_ALL, IID_PPV_ARGS(&wicFactory)));

		RETURN_IF_FAILED(wicFactory->CreateBitmap(width, height, GUID_WICPixelFormat32bppPBGRA, WICBitmapCacheOnDemand, &_bitmap));

		D2D1_RENDER_TARGET_PROPERTIES props{};
		props.pixelFormat.format = DXGI_FORMAT_B8G8R8A8_UNORM;
		props.pixelFormat.alphaMode = D2D1_ALPHA_MODE_PREMULTIPLIED;
		RETURN_IF_FAILED(d2d1Factory->CreateWicBitmapRenderTarget(_bitmap.get(), props, &_renderTarget));

		RETURN_IF_FAILED(CreateRenderTargetResources(width, height));
	}

	_prevTime = MFGetSystemTime();
	_frame = 0;
	return S_OK;
}

const bool FrameGenerator::HasD3DManager() const
{
	return _texture != nullptr;
}

HRESULT FrameGenerator::SetD3DManager(IUnknown* manager, UINT width, UINT height)
{
	RETURN_HR_IF_NULL(E_POINTER, manager);
	RETURN_HR_IF(E_INVALIDARG, !width || !height);

	RETURN_IF_FAILED(manager->QueryInterface(&_dxgiManager));
	RETURN_IF_FAILED(_dxgiManager->OpenDeviceHandle(&_deviceHandle));

	wil::com_ptr_nothrow<ID3D11Device> device;
	RETURN_IF_FAILED(_dxgiManager->GetVideoService(_deviceHandle, IID_PPV_ARGS(&device)));

	// create a texture/surface to write
	CD3D11_TEXTURE2D_DESC desc
	(
		DXGI_FORMAT_B8G8R8A8_UNORM,
		width,
		height,
		1,
		1,
		D3D11_BIND_SHADER_RESOURCE | D3D11_BIND_RENDER_TARGET
	);
	RETURN_IF_FAILED(device->CreateTexture2D(&desc, nullptr, &_texture));
	wil::com_ptr_nothrow<IDXGISurface> surface;
	RETURN_IF_FAILED(_texture.copy_to(&surface));

	// create a D2D1 render target from 2D GPU surface
	wil::com_ptr_nothrow<ID2D1Factory> d2d1Factory;
	RETURN_IF_FAILED(D2D1CreateFactory(D2D1_FACTORY_TYPE_MULTI_THREADED, IID_PPV_ARGS(&d2d1Factory)));

	auto props = D2D1::RenderTargetProperties
	(
		D2D1_RENDER_TARGET_TYPE_DEFAULT,
		D2D1::PixelFormat(DXGI_FORMAT_UNKNOWN, D2D1_ALPHA_MODE_PREMULTIPLIED)
	);
	RETURN_IF_FAILED(d2d1Factory->CreateDxgiSurfaceRenderTarget(surface.get(), props, &_renderTarget));

	RETURN_IF_FAILED(CreateRenderTargetResources(width, height));

	// create GPU RGB => NV12 converter
	RETURN_IF_FAILED(CoCreateInstance(CLSID_VideoProcessorMFT, nullptr, CLSCTX_ALL, IID_PPV_ARGS(&_converter)));

	wil::com_ptr_nothrow<IMFAttributes> atts;
	RETURN_IF_FAILED(_converter->GetAttributes(&atts));
	TraceMFAttributes(atts.get(), L"VideoProcessorMFT");

	MFT_OUTPUT_STREAM_INFO info{};
	RETURN_IF_FAILED(_converter->GetOutputStreamInfo(0, &info));
	WINTRACE(L"FrameGenerator::SetD3DManager CLSID_VideoProcessorMFT flags:0x%08X size:%u alignment:%u", info.dwFlags, info.cbSize, info.cbAlignment);

	wil::com_ptr_nothrow<IMFMediaType> inputType;
	RETURN_IF_FAILED(MFCreateMediaType(&inputType));
	inputType->SetGUID(MF_MT_MAJOR_TYPE, MFMediaType_Video);
	inputType->SetGUID(MF_MT_SUBTYPE, MFVideoFormat_RGB32);
	MFSetAttributeSize(inputType.get(), MF_MT_FRAME_SIZE, width, height);
	RETURN_IF_FAILED(_converter->SetInputType(0, inputType.get(), 0));

	wil::com_ptr_nothrow<IMFMediaType> outputType;
	RETURN_IF_FAILED(MFCreateMediaType(&outputType));
	outputType->SetGUID(MF_MT_MAJOR_TYPE, MFMediaType_Video);
	outputType->SetGUID(MF_MT_SUBTYPE, MFVideoFormat_NV12);
	MFSetAttributeSize(outputType.get(), MF_MT_FRAME_SIZE, width, height);
	RETURN_IF_FAILED(_converter->SetOutputType(0, outputType.get(), 0));

	// make sure the video processor works on GPU
	RETURN_IF_FAILED(_converter->ProcessMessage(MFT_MESSAGE_SET_D3D_MANAGER, (ULONG_PTR)manager));
	return S_OK;
}

// common to CPU & GPU
HRESULT FrameGenerator::CreateRenderTargetResources(UINT width, UINT height)
{
	assert(_renderTarget);
	RETURN_IF_FAILED(_renderTarget->CreateSolidColorBrush(D2D1::ColorF(1, 1, 1, 1), &_whiteBrush));

	RETURN_IF_FAILED(DWriteCreateFactory(DWRITE_FACTORY_TYPE_SHARED, __uuidof(IDWriteFactory), (IUnknown**)&_dwrite));
	RETURN_IF_FAILED(_dwrite->CreateTextFormat(L"Segoe UI", nullptr, DWRITE_FONT_WEIGHT_NORMAL, DWRITE_FONT_STYLE_NORMAL, DWRITE_FONT_STRETCH_NORMAL, 40, L"", &_textFormat));
	RETURN_IF_FAILED(_textFormat->SetParagraphAlignment(DWRITE_PARAGRAPH_ALIGNMENT_CENTER));
	RETURN_IF_FAILED(_textFormat->SetTextAlignment(DWRITE_TEXT_ALIGNMENT_CENTER));
	_width = width;
	_height = height;
	return S_OK;
}

HRESULT FrameGenerator::Generate(IMFSample* sample, REFGUID format, IMFSample** outSample)
{
	RETURN_HR_IF_NULL(E_POINTER, sample);
	RETURN_HR_IF_NULL(E_POINTER, outSample);
	*outSample = nullptr;

	// CamFX: no caminho CPU (Meet/Chrome/Teams), preenche o bitmap com o frame
	// processado vindo do app via arquivo compartilhado.
	bool camfxFilled = false;
	if (!HasD3DManager())
	{
		camfxFilled = FillBitmapFromCamFX();
		if (camfxFilled) _everFilled = true;
	}

	// Se ja recebemos algum frame do app, NUNCA mais mostrar a tela de demo:
	// quando nao ha frame novo, mantem o ultimo (o bitmap nao e tocado), o que
	// evita o "piscar" entre o video e a tela colorida.
	bool drawDemo = !camfxFilled && !_everFilled;

	// Tela de espera: preto puro, sem a demo colorida do sample original.
	// Aparece so antes do app comecar a enviar frames (e enquanto a camera
	// fisica abre). Depois do primeiro frame, nunca mais e mostrada.
	if (drawDemo && _renderTarget)
	{
		_renderTarget->BeginDraw();
		_renderTarget->Clear(D2D1::ColorF(0, 0, 0, 1));  // preto
		_renderTarget->EndDraw();
	}

	// build a sample using either D3D/DXGI (GPU) or WIC (CPU)
	wil::com_ptr_nothrow<IMFMediaBuffer> mediaBuffer;
	if (HasD3DManager())
	{
		// remove all existing buffers
		RETURN_IF_FAILED(sample->RemoveAllBuffers());

		// create a buffer from this and add to sample
		RETURN_IF_FAILED(MFCreateDXGISurfaceBuffer(__uuidof(ID3D11Texture2D), _texture.get(), 0, 0, &mediaBuffer));
		RETURN_IF_FAILED(sample->AddBuffer(mediaBuffer.get()));

		// if we're on GPU & format is not RGB, convert using GPU
		if (format == MFVideoFormat_NV12)
		{
			assert(_converter);
			RETURN_IF_FAILED(_converter->ProcessInput(0, sample, 0));

			// let converter build the sample for us, note it works because we gave it the D3DManager
			MFT_OUTPUT_DATA_BUFFER buffer = {};
			DWORD status = 0;
			RETURN_IF_FAILED(_converter->ProcessOutput(0, 1, &buffer, &status));
			*outSample = buffer.pSample;
		}
		else
		{
			sample->AddRef();
			*outSample = sample;
		}

		_frame++;
		return S_OK;
	}

	RETURN_IF_FAILED(sample->GetBufferByIndex(0, &mediaBuffer));
	wil::com_ptr_nothrow<IMF2DBuffer2> buffer2D;
	BYTE* scanline;
	LONG pitch;
	BYTE* start;
	DWORD length;
	RETURN_IF_FAILED(mediaBuffer->QueryInterface(IID_PPV_ARGS(&buffer2D)));
	RETURN_IF_FAILED(buffer2D->Lock2DSize(MF2DBuffer_LockFlags_Write, &scanline, &pitch, &start, &length));

	wil::com_ptr_nothrow<IWICBitmapLock> lock;
	auto hr = _bitmap->Lock(nullptr, WICBitmapLockRead, &lock);
	// now we're using regular COM macros because we want to be sure to unlock (or we could use try/catch)
	if (SUCCEEDED(hr))
	{
		UINT w, h;
		hr = lock->GetSize(&w, &h);
		if (SUCCEEDED(hr))
		{
			UINT wicStride;
			hr = lock->GetStride(&wicStride);
			if (SUCCEEDED(hr))
			{
				UINT wicSize;
				WICInProcPointer wicPointer;
				hr = lock->GetDataPointer(&wicSize, &wicPointer);
				if (SUCCEEDED(hr))
				{
					WINTRACE(L"WIC stride:%u WIC size:%u MF pitch:%u MF length:%u frame:%u format:%s", wicStride, wicSize, pitch, length, _frame, GUID_ToStringW(format).c_str());
					if (format == MFVideoFormat_NV12)
					{
						// note we could use MF's converter too
						hr = RGB32ToNV12(wicPointer, wicSize, wicStride, w, h, scanline, length, pitch);
					}
					else
					{
						hr = (wicSize != length || wicStride != pitch) ? E_FAIL : S_OK;
						if (SUCCEEDED(hr))
						{
							if (assert_true(wicPointer)) // WIC annotation is currently wrong on GetDataPointer wicPointer arg
							{
								CopyMemory(scanline, wicPointer, length);
							}
						}
					}

					if (SUCCEEDED(hr))
					{
						_frame++;
						sample->AddRef();
						*outSample = sample;
					}
				}
			}
		}
		lock.reset();
	}

	buffer2D->Unlock2D();
	return hr;
}
