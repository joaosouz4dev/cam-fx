#include "pch.h"
#include "Undocumented.h"
#include "Tools.h"
#include "EnumNames.h"
#include "MFTools.h"
#include "FrameGenerator.h"
#include "MediaStream.h"
#include "MediaSource.h"
#include "CamFXShared.h"   // CAMFX_FPS, CAMFX_MAX_WIDTH/HEIGHT

HRESULT MediaStream::Initialize(IMFMediaSource* source, int index)
{
	RETURN_HR_IF_NULL(E_POINTER, source);
	_source = source;
	_index = index;

	RETURN_IF_FAILED(SetGUID(MF_DEVICESTREAM_STREAM_CATEGORY, PINNAME_VIDEO_CAPTURE));
	RETURN_IF_FAILED(SetUINT32(MF_DEVICESTREAM_STREAM_ID, index));
	RETURN_IF_FAILED(SetUINT32(MF_DEVICESTREAM_FRAMESERVER_SHARED, 1));
	RETURN_IF_FAILED(SetUINT32(MF_DEVICESTREAM_ATTRIBUTE_FRAMESOURCE_TYPES, MFFrameSourceTypes::MFFrameSourceTypes_Color));

	RETURN_IF_FAILED(MFCreateEventQueue(&_queue));

	// RESOLUCAO DINAMICA: anunciamos varias resolucoes (maior primeiro, para o
	// app preferir HD), cada uma em RGB32 e NV12. O app de video escolhe uma; a
	// escolhida vem em Start(type) e define o render target. As resolucoes
	// precisam caber no buffer da shmem (<= CAMFX_MAX_*).
	struct Res { UINT w; UINT h; };
	static const Res kResolutions[] = {
		{ 1920, 1080 },
		{ 1280, 720 },
		{ 640, 480 },
	};
	const UINT kFps = CAMFX_FPS;
	const size_t nRes = ARRAYSIZE(kResolutions);

	// Array CRU de ponteiros crus, como MFCreateStreamDescriptor exige
	// (IMFMediaType**). Preenchemos com .detach() (posse transferida) e, apos o
	// MFCreateStreamDescriptor (que faz seu proprio AddRef), liberamos com
	// Release. 2 formatos (RGB32 + NV12) por resolucao.
	IMFMediaType* types[ARRAYSIZE(kResolutions) * 2] = {};
	size_t ti = 0;
	HRESULT hrInit = S_OK;

	for (size_t i = 0; i < nRes && SUCCEEDED(hrInit); i++)
	{
		const UINT w = kResolutions[i].w;
		const UINT h = kResolutions[i].h;

		wil::com_ptr_nothrow<IMFMediaType> rgbType;
		hrInit = MFCreateMediaType(&rgbType);
		if (SUCCEEDED(hrInit))
		{
			rgbType->SetGUID(MF_MT_MAJOR_TYPE, MFMediaType_Video);
			rgbType->SetGUID(MF_MT_SUBTYPE, MFVideoFormat_RGB32);
			MFSetAttributeSize(rgbType.get(), MF_MT_FRAME_SIZE, w, h);
			rgbType->SetUINT32(MF_MT_DEFAULT_STRIDE, w * 4);
			rgbType->SetUINT32(MF_MT_INTERLACE_MODE, MFVideoInterlace_Progressive);
			rgbType->SetUINT32(MF_MT_ALL_SAMPLES_INDEPENDENT, TRUE);
			MFSetAttributeRatio(rgbType.get(), MF_MT_FRAME_RATE, kFps, 1);
			rgbType->SetUINT32(MF_MT_AVG_BITRATE, (uint32_t)(w * h * 4 * 8 * kFps));
			MFSetAttributeRatio(rgbType.get(), MF_MT_PIXEL_ASPECT_RATIO, 1, 1);
			types[ti++] = rgbType.detach();
		}

		wil::com_ptr_nothrow<IMFMediaType> nv12Type;
		if (SUCCEEDED(hrInit)) hrInit = MFCreateMediaType(&nv12Type);
		if (SUCCEEDED(hrInit))
		{
			nv12Type->SetGUID(MF_MT_MAJOR_TYPE, MFMediaType_Video);
			nv12Type->SetGUID(MF_MT_SUBTYPE, MFVideoFormat_NV12);
			nv12Type->SetUINT32(MF_MT_INTERLACE_MODE, MFVideoInterlace_Progressive);
			nv12Type->SetUINT32(MF_MT_ALL_SAMPLES_INDEPENDENT, TRUE);
			MFSetAttributeSize(nv12Type.get(), MF_MT_FRAME_SIZE, w, h);
			// NV12: stride do plano Y = w (1 byte/pixel), NAO w*3/2.
			nv12Type->SetUINT32(MF_MT_DEFAULT_STRIDE, w);
			MFSetAttributeRatio(nv12Type.get(), MF_MT_FRAME_RATE, kFps, 1);
			// NV12 = 12 bpp -> bitrate = w*h*12*fps/8 * 8 = w*h*12*fps bits/s.
			nv12Type->SetUINT32(MF_MT_AVG_BITRATE, (uint32_t)(w * h * 12 * kFps));
			MFSetAttributeRatio(nv12Type.get(), MF_MT_PIXEL_ASPECT_RATIO, 1, 1);
			types[ti++] = nv12Type.detach();
		}
	}

	HRESULT hr = hrInit;
	if (SUCCEEDED(hr))
		hr = MFCreateStreamDescriptor(_index, (DWORD)ti, types, &_descriptor);

	// O descriptor fez AddRef nos tipos; liberamos a nossa referencia.
	for (size_t k = 0; k < ti; k++)
		if (types[k]) types[k]->Release();

	RETURN_IF_FAILED_MSG(hr, "MFCreateStreamDescriptor failed");

	wil::com_ptr_nothrow<IMFMediaTypeHandler> handler;
	RETURN_IF_FAILED(_descriptor->GetMediaTypeHandler(&handler));
	TraceMFAttributes(handler.get(), L"MediaTypeHandler");
	// Tipo default = o primeiro (maior resolucao, RGB32). Buscamos pelo handler
	// para nao depender de um ponteiro que ja liberamos.
	wil::com_ptr_nothrow<IMFMediaType> firstType;
	RETURN_IF_FAILED(handler->GetMediaTypeByIndex(0, &firstType));
	RETURN_IF_FAILED(handler->SetCurrentMediaType(firstType.get()));

	return S_OK;
}

HRESULT MediaStream::Start(IMFMediaType* type)
{
	RETURN_HR_IF(MF_E_SHUTDOWN, !_queue || !_allocator);

	// Resolucao negociada com o app. Default = maior anunciada (1080p) caso o
	// type nao traga MF_MT_FRAME_SIZE por algum motivo.
	UINT negW = CAMFX_MAX_WIDTH, negH = CAMFX_MAX_HEIGHT;
	if (type)
	{
		RETURN_IF_FAILED(type->GetGUID(MF_MT_SUBTYPE, &_format));
		WINTRACE(L"MediaStream::Start format: %s", GUID_ToStringW(_format).c_str());
		UINT w = 0, h = 0;
		if (SUCCEEDED(MFGetAttributeSize(type, MF_MT_FRAME_SIZE, &w, &h)) && w && h)
		{
			negW = w;
			negH = h;
		}
	}
	_negWidth = negW;
	_negHeight = negH;

	// at this point, set D3D manager may have not been called
	// so we want to create a D2D1 renter target anyway. Usa a resolucao
	// NEGOCIADA (dinamica), nao mais a constante 720p.
	RETURN_IF_FAILED(_generator.EnsureRenderTarget(negW, negH));

	RETURN_IF_FAILED(_allocator->InitializeSampleAllocator(10, type));
	RETURN_IF_FAILED(_queue->QueueEventParamVar(MEStreamStarted, GUID_NULL, S_OK, nullptr));
	_state = MF_STREAM_STATE_RUNNING;
	return S_OK;
}

HRESULT MediaStream::Stop()
{
	RETURN_HR_IF(MF_E_SHUTDOWN, !_queue || !_allocator);

	RETURN_IF_FAILED(_allocator->UninitializeSampleAllocator());
	RETURN_IF_FAILED(_queue->QueueEventParamVar(MEStreamStopped, GUID_NULL, S_OK, nullptr));
	_state = MF_STREAM_STATE_STOPPED;
	return S_OK;
}

MFSampleAllocatorUsage MediaStream::GetAllocatorUsage()
{
	return MFSampleAllocatorUsage_UsesProvidedAllocator;
}

HRESULT MediaStream::SetAllocator(IUnknown* allocator)
{
	RETURN_HR_IF_NULL(E_POINTER, allocator);
	_allocator.reset();
	RETURN_HR(allocator->QueryInterface(&_allocator));
}

HRESULT MediaStream::SetD3DManager(IUnknown* manager)
{
	RETURN_HR_IF_NULL(E_POINTER, manager);

	// comment these 2 lines to force CPU usage. Usa a resolucao negociada
	// (definida em Start); se ainda nao negociou, cai no maximo (1080p).
	UINT w = _negWidth ? _negWidth : CAMFX_MAX_WIDTH;
	UINT h = _negHeight ? _negHeight : CAMFX_MAX_HEIGHT;
	RETURN_IF_FAILED(_allocator->SetDirectXManager(manager));
	RETURN_IF_FAILED(_generator.SetD3DManager(manager, w, h));
	return S_OK;
}

void MediaStream::Shutdown()
{
	if (_queue)
	{
		LOG_IF_FAILED_MSG(_queue->Shutdown(), "Queue shutdown failed");
		_queue.reset();
	}

	_descriptor.reset();
	_source.reset();
	_attributes.reset();
}

// IMFMediaEventGenerator
STDMETHODIMP MediaStream::BeginGetEvent(IMFAsyncCallback* pCallback, IUnknown* punkState)
{
	//WINTRACE(L"MediaSource::BeginGetEvent");
	winrt::slim_lock_guard lock(_lock);
	RETURN_HR_IF(MF_E_SHUTDOWN, !_queue);

	RETURN_IF_FAILED(_queue->BeginGetEvent(pCallback, punkState));
	return S_OK;
}

STDMETHODIMP MediaStream::EndGetEvent(IMFAsyncResult* pResult, IMFMediaEvent** ppEvent)
{
	//WINTRACE(L"MediaStream::EndGetEvent");
	RETURN_HR_IF_NULL(E_POINTER, ppEvent);
	*ppEvent = nullptr;
	winrt::slim_lock_guard lock(_lock);
	RETURN_HR_IF(MF_E_SHUTDOWN, !_queue);

	RETURN_IF_FAILED(_queue->EndGetEvent(pResult, ppEvent));
	return S_OK;
}

STDMETHODIMP MediaStream::GetEvent(DWORD dwFlags, IMFMediaEvent** ppEvent)
{
	WINTRACE(L"MediaStream::GetEvent");
	RETURN_HR_IF_NULL(E_POINTER, ppEvent);
	*ppEvent = nullptr;
	winrt::slim_lock_guard lock(_lock);
	RETURN_HR_IF(MF_E_SHUTDOWN, !_queue);

	RETURN_IF_FAILED(_queue->GetEvent(dwFlags, ppEvent));
	return S_OK;
}

STDMETHODIMP MediaStream::QueueEvent(MediaEventType met, REFGUID guidExtendedType, HRESULT hrStatus, const PROPVARIANT* pvValue)
{
	WINTRACE(L"MediaStream::QueueEvent");
	winrt::slim_lock_guard lock(_lock);
	RETURN_HR_IF(MF_E_SHUTDOWN, !_queue);

	RETURN_IF_FAILED(_queue->QueueEventParamVar(met, guidExtendedType, hrStatus, pvValue));
	return S_OK;
}

// IMFMediaStream
STDMETHODIMP MediaStream::GetMediaSource(IMFMediaSource** ppMediaSource)
{
	WINTRACE(L"MediaSource::GetMediaSource");
	RETURN_HR_IF_NULL(E_POINTER, ppMediaSource);
	*ppMediaSource = nullptr;
	RETURN_HR_IF(MF_E_SHUTDOWN, !_source);

	RETURN_IF_FAILED(_source.copy_to(ppMediaSource));
	return S_OK;
}

STDMETHODIMP MediaStream::GetStreamDescriptor(IMFStreamDescriptor** ppStreamDescriptor)
{
	WINTRACE(L"MediaStream::GetStreamDescriptor");
	RETURN_HR_IF_NULL(E_POINTER, ppStreamDescriptor);
	*ppStreamDescriptor = nullptr;
	winrt::slim_lock_guard lock(_lock);
	RETURN_HR_IF(MF_E_SHUTDOWN, !_descriptor);

	RETURN_IF_FAILED(_descriptor.copy_to(ppStreamDescriptor));
	return S_OK;
}

STDMETHODIMP MediaStream::RequestSample(IUnknown* pToken)
{
	//WINTRACE(L"MediaStream::RequestSample pToken:%p", pToken);
	winrt::slim_lock_guard lock(_lock);
	RETURN_HR_IF(MF_E_SHUTDOWN, !_allocator || !_queue);

	wil::com_ptr_nothrow<IMFSample> sample;
	RETURN_IF_FAILED(_allocator->AllocateSample(&sample));
	RETURN_IF_FAILED(sample->SetSampleTime(MFGetSystemTime()));
	RETURN_IF_FAILED(sample->SetSampleDuration(333333));

	// generate frame
	wil::com_ptr_nothrow<IMFSample> outSample;
	RETURN_IF_FAILED(_generator.Generate(sample.get(), _format, &outSample));

	if (pToken)
	{
		RETURN_IF_FAILED(outSample->SetUnknown(MFSampleExtension_Token, pToken));
	}
	RETURN_IF_FAILED(_queue->QueueEventParamUnk(MEMediaSample, GUID_NULL, S_OK, outSample.get()));
	return S_OK;
}

// IMFMediaStream2
STDMETHODIMP MediaStream::SetStreamState(MF_STREAM_STATE value)
{
	WINTRACE(L"MediaStream::SetStreamState current:%u value:%u", _state, value);
	if (_state = value)
		return S_OK;
	switch (value)
	{
	case MF_STREAM_STATE_PAUSED:
		if (_state != MF_STREAM_STATE_RUNNING)
			RETURN_HR(MF_E_INVALID_STATE_TRANSITION);

		_state = value;
		break;

	case MF_STREAM_STATE_RUNNING:
		RETURN_IF_FAILED(Start(nullptr));
		break;

	case MF_STREAM_STATE_STOPPED:
		RETURN_IF_FAILED(Stop());
		break;

	default:
		RETURN_HR(MF_E_INVALID_STATE_TRANSITION);
		break;
	}
	return S_OK;
}

STDMETHODIMP MediaStream::GetStreamState(MF_STREAM_STATE* value)
{
	WINTRACE(L"MediaStream::GetStreamState state:%u", _state);
	RETURN_HR_IF_NULL(E_POINTER, value);
	*value = _state;
	return S_OK;
}

// IKsControl
STDMETHODIMP_(NTSTATUS) MediaStream::KsProperty(PKSPROPERTY property, ULONG length, LPVOID data, ULONG dataLength, ULONG* bytesReturned)
{
	WINTRACE(L"MediaStream::KsProperty len:%u data:%p dataLength:%u", length, data, dataLength);
	RETURN_HR_IF_NULL(E_POINTER, property);
	RETURN_HR_IF_NULL(E_POINTER, bytesReturned);
	winrt::slim_lock_guard lock(_lock);

	WINTRACE(L"MediaStream::KsProperty prop:%s", PKSIDENTIFIER_ToString(property, length).c_str());

	return HRESULT_FROM_WIN32(ERROR_SET_NOT_FOUND);
}

STDMETHODIMP_(NTSTATUS) MediaStream::KsMethod(PKSMETHOD method, ULONG length, LPVOID data, ULONG dataLength, ULONG* bytesReturned)
{
	WINTRACE(L"MediaStream::KsMethod len:%u data:%p dataLength:%u", length, data, dataLength);
	RETURN_HR_IF_NULL(E_POINTER, method);
	RETURN_HR_IF_NULL(E_POINTER, bytesReturned);
	winrt::slim_lock_guard lock(_lock);

	WINTRACE(L"MediaStream::KsMethod method:%s", PKSIDENTIFIER_ToString(method, length).c_str());

	return HRESULT_FROM_WIN32(ERROR_SET_NOT_FOUND);
}

STDMETHODIMP_(NTSTATUS) MediaStream::KsEvent(PKSEVENT evt, ULONG length, LPVOID data, ULONG dataLength, ULONG* bytesReturned)
{
	WINTRACE(L"MediaStream::KsEvent evt:%p len:%u data:%p dataLength:%u", evt, length, data, dataLength);
	RETURN_HR_IF_NULL(E_POINTER, bytesReturned);
	winrt::slim_lock_guard lock(_lock);

	WINTRACE(L"MediaStream::KsEvent event:%s", PKSIDENTIFIER_ToString(evt, length).c_str());
	return HRESULT_FROM_WIN32(ERROR_SET_NOT_FOUND);
}
