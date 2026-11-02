// AndroCam Virtual Camera - a DirectShow video-capture source filter.
// Reads frames the AndroCam app places in shared memory ("Local\AndroCamVideo", see shm_layout.h)
// and presents them as a normal webcam called "AndroCam Virtual Camera". No window, no OBS.
//
// Built with Microsoft's DirectShow base classes (strmbase, from Windows-classic-samples).
// NOTE: compiled only by CI (MSVC). All pixel logic lives in shm_layout.h, which is unit-tested.
#include <streams.h>
#include <initguid.h>
#include <olectl.h>
#include <dvdmedia.h>
#include "shm_layout.h"

#ifndef KSPROPERTY_SUPPORT_GET
#define KSPROPERTY_SUPPORT_GET 1
#endif

// {9F5F8B64-6B4C-4C93-8B8C-0A7C2F6D3A11}  (== ACV_CLSID_DATA, checked by the tests)
DEFINE_GUID(CLSID_AndroCamVCam, 0x9F5F8B64, 0x6B4C, 0x4C93, 0x8B, 0x8C, 0x0A, 0x7C, 0x2F, 0x6D, 0x3A, 0x11);

static const wchar_t kFilterName[] = L"AndroCam Virtual Camera";
static const REFERENCE_TIME kFrameTime = 333333;       // 30 fps in 100 ns units

struct SizeEntry { int w, h; };
static const SizeEntry kSizes[] = { {1280, 720}, {1920, 1080}, {854, 480}, {640, 480}, {640, 360}, {320, 240} };
static const int kNumSizes = (int)(sizeof(kSizes) / sizeof(kSizes[0]));

class CAndroCamPin;

// ---------------------------------------------------------------------------------------- filter
class CAndroCam : public CSource {
public:
    static CUnknown *WINAPI CreateInstance(LPUNKNOWN lpunk, HRESULT *phr);
    STDMETHODIMP NonDelegatingQueryInterface(REFIID riid, void **ppv);
private:
    CAndroCam(LPUNKNOWN lpunk, HRESULT *phr);
};

// ------------------------------------------------------------------------------------------- pin
class CAndroCamPin : public CSourceStream, public IAMStreamConfig, public IKsPropertySet {
public:
    CAndroCamPin(HRESULT *phr, CSource *pParent, LPCWSTR pPinName);
    ~CAndroCamPin();

    STDMETHODIMP QueryInterface(REFIID riid, void **ppv);
    STDMETHODIMP_(ULONG) AddRef() { return GetOwner()->AddRef(); }
    STDMETHODIMP_(ULONG) Release() { return GetOwner()->Release(); }
    STDMETHODIMP Notify(IBaseFilter *pSender, Quality q) { return E_FAIL; }

    HRESULT FillBuffer(IMediaSample *pms);
    HRESULT DecideBufferSize(IMemAllocator *pAlloc, ALLOCATOR_PROPERTIES *pProperties);
    HRESULT CheckMediaType(const CMediaType *pMediaType);
    HRESULT GetMediaType(int iPosition, CMediaType *pmt);
    HRESULT SetMediaType(const CMediaType *pmt);
    HRESULT OnThreadCreate(void);

    // IAMStreamConfig
    HRESULT STDMETHODCALLTYPE SetFormat(AM_MEDIA_TYPE *pmt);
    HRESULT STDMETHODCALLTYPE GetFormat(AM_MEDIA_TYPE **ppmt);
    HRESULT STDMETHODCALLTYPE GetNumberOfCapabilities(int *piCount, int *piSize);
    HRESULT STDMETHODCALLTYPE GetStreamCaps(int iIndex, AM_MEDIA_TYPE **ppmt, BYTE *pSCC);

    // IKsPropertySet (lets apps see this is a "capture" pin)
    HRESULT STDMETHODCALLTYPE Set(REFGUID, DWORD, void *, DWORD, void *, DWORD) { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE Get(REFGUID guidPropSet, DWORD dwPropID, void *pInstanceData, DWORD cbInstanceData,
                                  void *pPropData, DWORD cbPropData, DWORD *pcbReturned);
    HRESULT STDMETHODCALLTYPE QuerySupported(REFGUID guidPropSet, DWORD dwPropID, DWORD *pTypeSupport);

private:
    void FillMediaType(CMediaType *pmt, int w, int h);
    bool EnsureMapped();

    CSource *m_pParent;
    REFERENCE_TIME m_rtLastTime;
    LARGE_INTEGER m_qpcFreq, m_qpcStart;
    LONGLONG m_frameIndex;
    HANDLE m_hMap;
    const uint8_t *m_view;
    DWORD m_lastTry;
};

// ---------------------------------------------------------------------------------------- filter
CUnknown *WINAPI CAndroCam::CreateInstance(LPUNKNOWN lpunk, HRESULT *phr) {
    ASSERT(phr);
    CUnknown *punk = new CAndroCam(lpunk, phr);
    return punk;
}

CAndroCam::CAndroCam(LPUNKNOWN lpunk, HRESULT *phr) : CSource(NAME("AndroCam Virtual Camera"), lpunk, CLSID_AndroCamVCam) {
    ASSERT(phr);
    CAutoLock cAutoLock(&m_cStateLock);
    m_paStreams = (CSourceStream **) new CAndroCamPin *[1];
    m_paStreams[0] = new CAndroCamPin(phr, this, L"Output");
}

STDMETHODIMP CAndroCam::NonDelegatingQueryInterface(REFIID riid, void **ppv) {
    // IAMStreamConfig and IKsPropertySet live on the pin; apps ask the filter for them.
    if (riid == IID_IAMStreamConfig || riid == IID_IKsPropertySet) return m_paStreams[0]->QueryInterface(riid, ppv);
    return CSource::NonDelegatingQueryInterface(riid, ppv);
}

// ------------------------------------------------------------------------------------------- pin
CAndroCamPin::CAndroCamPin(HRESULT *phr, CSource *pParent, LPCWSTR pPinName)
    : CSourceStream(NAME("AndroCam Virtual Camera"), phr, pParent, pPinName), m_pParent(pParent),
      m_rtLastTime(0), m_frameIndex(0), m_hMap(NULL), m_view(NULL), m_lastTry(0) {
    QueryPerformanceFrequency(&m_qpcFreq);
    m_qpcStart.QuadPart = 0;
    FillMediaType(&m_mt, kSizes[0].w, kSizes[0].h);     // default format: 1280x720 RGB24 @ 30
}

CAndroCamPin::~CAndroCamPin() {
    if (m_view) UnmapViewOfFile(m_view);
    if (m_hMap) CloseHandle(m_hMap);
}

STDMETHODIMP CAndroCamPin::QueryInterface(REFIID riid, void **ppv) {
    if (riid == IID_IAMStreamConfig) *ppv = static_cast<IAMStreamConfig *>(this);
    else if (riid == IID_IKsPropertySet) *ppv = static_cast<IKsPropertySet *>(this);
    else return CSourceStream::QueryInterface(riid, ppv);
    AddRef();
    return S_OK;
}

void CAndroCamPin::FillMediaType(CMediaType *pmt, int w, int h) {
    VIDEOINFOHEADER *pvi = (VIDEOINFOHEADER *)pmt->AllocFormatBuffer(sizeof(VIDEOINFOHEADER));
    ZeroMemory(pvi, sizeof(VIDEOINFOHEADER));
    pvi->bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
    pvi->bmiHeader.biWidth = w;
    pvi->bmiHeader.biHeight = h;                        // positive = bottom-up DIB
    pvi->bmiHeader.biPlanes = 1;
    pvi->bmiHeader.biBitCount = 24;
    pvi->bmiHeader.biCompression = BI_RGB;
    pvi->bmiHeader.biSizeImage = (DWORD)(((w * 3 + 3) & ~3) * h);
    pvi->AvgTimePerFrame = kFrameTime;
    pvi->dwBitRate = (DWORD)(pvi->bmiHeader.biSizeImage * 8 * 30);
    SetRectEmpty(&pvi->rcSource);
    SetRectEmpty(&pvi->rcTarget);
    pmt->SetType(&MEDIATYPE_Video);
    pmt->SetFormatType(&FORMAT_VideoInfo);
    pmt->SetTemporalCompression(FALSE);
    pmt->SetSubtype(&MEDIASUBTYPE_RGB24);
    pmt->SetSampleSize(pvi->bmiHeader.biSizeImage);
}

HRESULT CAndroCamPin::GetMediaType(int iPosition, CMediaType *pmt) {
    if (iPosition < 0) return E_INVALIDARG;
    if (iPosition > kNumSizes) return VFW_S_NO_MORE_ITEMS;
    if (iPosition == 0) { *pmt = m_mt; return S_OK; }   // the currently preferred format first
    FillMediaType(pmt, kSizes[iPosition - 1].w, kSizes[iPosition - 1].h);
    return S_OK;
}

HRESULT CAndroCamPin::CheckMediaType(const CMediaType *pmt) {
    if (!pmt || *pmt->Type() != MEDIATYPE_Video || *pmt->Subtype() != MEDIASUBTYPE_RGB24 ||
        *pmt->FormatType() != FORMAT_VideoInfo || pmt->cbFormat < sizeof(VIDEOINFOHEADER) || !pmt->pbFormat)
        return E_INVALIDARG;
    const VIDEOINFOHEADER *pvi = (const VIDEOINFOHEADER *)pmt->pbFormat;
    LONG w = pvi->bmiHeader.biWidth, h = pvi->bmiHeader.biHeight;
    if (h < 0) h = -h;
    if (w < 16 || h < 16 || w > ACV_MAX_W || h > ACV_MAX_H) return E_INVALIDARG;   // any size is rendered by scaling
    return S_OK;
}

HRESULT CAndroCamPin::SetMediaType(const CMediaType *pmt) {
    return CSourceStream::SetMediaType(pmt);
}

HRESULT CAndroCamPin::DecideBufferSize(IMemAllocator *pAlloc, ALLOCATOR_PROPERTIES *pProperties) {
    CAutoLock cAutoLock(m_pFilter->pStateLock());
    VIDEOINFOHEADER *pvi = (VIDEOINFOHEADER *)m_mt.Format();
    pProperties->cBuffers = 1;
    pProperties->cbBuffer = pvi->bmiHeader.biSizeImage;
    ALLOCATOR_PROPERTIES actual;
    HRESULT hr = pAlloc->SetProperties(pProperties, &actual);
    if (FAILED(hr)) return hr;
    if (actual.cbBuffer < pProperties->cbBuffer) return E_FAIL;
    return NOERROR;
}

HRESULT CAndroCamPin::OnThreadCreate(void) {
    m_rtLastTime = 0;
    m_frameIndex = 0;
    QueryPerformanceCounter(&m_qpcStart);
    timeBeginPeriod(1);                                 // accurate Sleep() for frame pacing
    return NOERROR;
}

bool CAndroCamPin::EnsureMapped() {
    if (m_view) return true;
    DWORD now = GetTickCount();
    if (m_lastTry && now - m_lastTry < 500) return false;   // the app may not be running yet: retry twice a second
    m_lastTry = now ? now : 1;
    HANDLE h = OpenFileMappingW(FILE_MAP_READ, FALSE, ACV_MAP_NAME);
    if (!h) return false;
    const void *v = MapViewOfFile(h, FILE_MAP_READ, 0, 0, 0);
    if (!v) { CloseHandle(h); return false; }
    m_hMap = h;
    m_view = (const uint8_t *)v;
    return true;
}

HRESULT CAndroCamPin::FillBuffer(IMediaSample *pms) {
    VIDEOINFOHEADER *pvi = (VIDEOINFOHEADER *)m_mt.pbFormat;
    REFERENCE_TIME avg = pvi->AvgTimePerFrame ? pvi->AvgTimePerFrame : kFrameTime;

    // pace the stream at the negotiated frame rate
    m_frameIndex++;
    LARGE_INTEGER nowq;
    QueryPerformanceCounter(&nowq);
    LONGLONG due = m_qpcStart.QuadPart + (m_frameIndex * avg * m_qpcFreq.QuadPart) / 10000000LL;
    LONGLONG waitTicks = due - nowq.QuadPart;
    if (waitTicks > 0) {
        DWORD ms = (DWORD)((waitTicks * 1000) / m_qpcFreq.QuadPart);
        if (ms) Sleep(ms);
    }

    REFERENCE_TIME rtNow = m_rtLastTime;
    m_rtLastTime += avg;
    pms->SetTime(&rtNow, &m_rtLastTime);
    pms->SetSyncPoint(TRUE);

    BYTE *pData = NULL;
    pms->GetPointer(&pData);
    long len = pms->GetSize();
    int w = pvi->bmiHeader.biWidth, h = pvi->bmiHeader.biHeight < 0 ? -pvi->bmiHeader.biHeight : pvi->bmiHeader.biHeight;
    bool bottomUp = pvi->bmiHeader.biHeight > 0;
    int stride = (w * 3 + 3) & ~3;
    if (!pData || len < (long)stride * h) return NOERROR;

    // acv_render writes tightly packed rows; DIB rows are 4-byte aligned, so use a scratch buffer when they differ.
    if (stride == w * 3) {
        if (EnsureMapped()) acv_render(m_view, ACV_TOTAL, pData, w, h, bottomUp ? 1 : 0);
        else acv_fill(pData, (size_t)w * h, ACV_IDLE_B, ACV_IDLE_G, ACV_IDLE_R);
    } else {
        BYTE *tmp = new BYTE[(size_t)w * h * 3];
        if (EnsureMapped()) acv_render(m_view, ACV_TOTAL, tmp, w, h, bottomUp ? 1 : 0);
        else acv_fill(tmp, (size_t)w * h, ACV_IDLE_B, ACV_IDLE_G, ACV_IDLE_R);
        for (int y = 0; y < h; y++) {
            memcpy(pData + (size_t)y * stride, tmp + (size_t)y * w * 3, (size_t)w * 3);
            memset(pData + (size_t)y * stride + (size_t)w * 3, 0, stride - w * 3);
        }
        delete[] tmp;
    }
    return NOERROR;
}

// ---------------------------------------------------------------------------------- IAMStreamConfig
HRESULT STDMETHODCALLTYPE CAndroCamPin::SetFormat(AM_MEDIA_TYPE *pmt) {
    if (!pmt) return E_POINTER;
    CMediaType mt(*pmt);
    if (FAILED(CheckMediaType(&mt))) return VFW_E_INVALIDMEDIATYPE;
    IPin *pin = NULL;
    ConnectedTo(&pin);
    m_mt = mt;
    if (pin) {                                          // already connected: renegotiate with the new size
        pin->Release();
        IFilterGraph *graph = m_pParent->GetFilterGraph();
        if (graph) graph->Reconnect(this);
    }
    return S_OK;
}

HRESULT STDMETHODCALLTYPE CAndroCamPin::GetFormat(AM_MEDIA_TYPE **ppmt) {
    if (!ppmt) return E_POINTER;
    *ppmt = CreateMediaType(&m_mt);
    return *ppmt ? S_OK : E_OUTOFMEMORY;
}

HRESULT STDMETHODCALLTYPE CAndroCamPin::GetNumberOfCapabilities(int *piCount, int *piSize) {
    if (!piCount || !piSize) return E_POINTER;
    *piCount = kNumSizes;
    *piSize = sizeof(VIDEO_STREAM_CONFIG_CAPS);
    return S_OK;
}

HRESULT STDMETHODCALLTYPE CAndroCamPin::GetStreamCaps(int iIndex, AM_MEDIA_TYPE **ppmt, BYTE *pSCC) {
    if (!ppmt || !pSCC) return E_POINTER;
    if (iIndex < 0 || iIndex >= kNumSizes) return S_FALSE;
    CMediaType mt;
    int w = kSizes[iIndex].w, h = kSizes[iIndex].h;
    FillMediaType(&mt, w, h);
    *ppmt = CreateMediaType(&mt);
    if (!*ppmt) return E_OUTOFMEMORY;

    VIDEO_STREAM_CONFIG_CAPS *c = (VIDEO_STREAM_CONFIG_CAPS *)pSCC;
    ZeroMemory(c, sizeof(*c));
    c->guid = FORMAT_VideoInfo;
    c->VideoStandard = AnalogVideo_None;
    c->InputSize.cx = w;  c->InputSize.cy = h;
    c->MinCroppingSize = c->MaxCroppingSize = c->InputSize;
    c->CropGranularityX = w;  c->CropGranularityY = h;
    c->CropAlignX = c->CropAlignY = 1;
    c->MinOutputSize = c->MaxOutputSize = c->InputSize;
    c->OutputGranularityX = c->OutputGranularityY = 0;
    c->MinFrameInterval = c->MaxFrameInterval = kFrameTime;
    c->MinBitsPerSecond = c->MaxBitsPerSecond = (LONG)(((w * 3 + 3) & ~3) * h * 8 * 30);
    return S_OK;
}

// ------------------------------------------------------------------------------------ IKsPropertySet
HRESULT STDMETHODCALLTYPE CAndroCamPin::Get(REFGUID guidPropSet, DWORD dwPropID, void *, DWORD, void *pPropData,
                                             DWORD cbPropData, DWORD *pcbReturned) {
    if (guidPropSet != AMPROPSETID_Pin) return E_PROP_SET_UNSUPPORTED;
    if (dwPropID != AMPROPERTY_PIN_CATEGORY) return E_PROP_ID_UNSUPPORTED;
    if (!pPropData && !pcbReturned) return E_POINTER;
    if (pcbReturned) *pcbReturned = sizeof(GUID);
    if (!pPropData) return S_OK;
    if (cbPropData < sizeof(GUID)) return E_UNEXPECTED;
    *(GUID *)pPropData = PIN_CATEGORY_CAPTURE;
    return S_OK;
}

HRESULT STDMETHODCALLTYPE CAndroCamPin::QuerySupported(REFGUID guidPropSet, DWORD dwPropID, DWORD *pTypeSupport) {
    if (guidPropSet != AMPROPSETID_Pin) return E_PROP_SET_UNSUPPORTED;
    if (dwPropID != AMPROPERTY_PIN_CATEGORY) return E_PROP_ID_UNSUPPORTED;
    if (pTypeSupport) *pTypeSupport = KSPROPERTY_SUPPORT_GET;
    return S_OK;
}

// -------------------------------------------------------------------------- COM registration (DLL)
static const AMOVIESETUP_MEDIATYPE sudMediaTypes = { &MEDIATYPE_Video, &MEDIASUBTYPE_NULL };
static const AMOVIESETUP_PIN sudOutputPin = { const_cast<LPWSTR>(L"Output"), FALSE, TRUE, FALSE, FALSE, &CLSID_NULL, NULL, 1, &sudMediaTypes };
static const AMOVIESETUP_FILTER sudFilter = { &CLSID_AndroCamVCam, kFilterName, MERIT_DO_NOT_USE, 1, &sudOutputPin };

CFactoryTemplate g_Templates[1] = {
    { kFilterName, &CLSID_AndroCamVCam, CAndroCam::CreateInstance, NULL, &sudFilter }
};
int g_cTemplates = sizeof(g_Templates) / sizeof(g_Templates[0]);

static STDAPI RegisterFilters(BOOL bRegister) {
    WCHAR achFileName[MAX_PATH];
    if (!GetModuleFileNameW(g_hInst, achFileName, MAX_PATH)) return HRESULT_FROM_WIN32(GetLastError());
    HRESULT hr = CoInitialize(NULL);
    if (FAILED(hr)) return hr;
    if (bRegister) hr = AMovieSetupRegisterServer(CLSID_AndroCamVCam, kFilterName, achFileName, L"Both", L"InprocServer32");
    if (SUCCEEDED(hr)) {
        IFilterMapper2 *fm = NULL;
        hr = CoCreateInstance(CLSID_FilterMapper2, NULL, CLSCTX_INPROC_SERVER, IID_IFilterMapper2, (void **)&fm);
        if (SUCCEEDED(hr)) {
            if (bRegister) {
                IMoniker *pMoniker = NULL;
                REGFILTER2 rf2;
                ZeroMemory(&rf2, sizeof(rf2));
                rf2.dwVersion = 1;
                rf2.dwMerit = MERIT_DO_NOT_USE;
                rf2.cPins = 1;
                rf2.rgPins = reinterpret_cast<const REGFILTERPINS *>(&sudOutputPin);   // same layout by design
                hr = fm->RegisterFilter(CLSID_AndroCamVCam, kFilterName, &pMoniker, &CLSID_VideoInputDeviceCategory, NULL, &rf2);
                if (pMoniker) pMoniker->Release();
            } else {
                hr = fm->UnregisterFilter(&CLSID_VideoInputDeviceCategory, 0, CLSID_AndroCamVCam);
            }
            fm->Release();
        }
    }
    if (SUCCEEDED(hr) && !bRegister) hr = AMovieSetupUnregisterServer(CLSID_AndroCamVCam);
    CoFreeUnusedLibraries();
    CoUninitialize();
    return hr;
}

STDAPI DllRegisterServer() { return RegisterFilters(TRUE); }
STDAPI DllUnregisterServer() { return RegisterFilters(FALSE); }

extern "C" BOOL WINAPI DllEntryPoint(HINSTANCE, ULONG, LPVOID);
BOOL APIENTRY DllMain(HANDLE hModule, DWORD dwReason, LPVOID lpReserved) {
    return DllEntryPoint((HINSTANCE)hModule, dwReason, lpReserved);
}
