/* AndroCam shared-memory video layout + renderer. Plain C99/C++ header, no OS dependencies,
   so it can be unit-tested on any machine. Python twin: host/androcam_host/shm.py. */
#ifndef ANDROCAM_SHM_LAYOUT_H
#define ANDROCAM_SHM_LAYOUT_H

#include <stddef.h>
#include <stdint.h>
#include <string.h>

#define ACV_MAGIC      0x31564341u          /* 'ACV1' */
#define ACV_HEADER     64
#define ACV_MAX_W      1920
#define ACV_MAX_H      1080
#define ACV_FMT_BGR24  1
#define ACV_BUF_SIZE   ((size_t)ACV_MAX_W * ACV_MAX_H * 3)
#define ACV_TOTAL      (ACV_HEADER + 2 * ACV_BUF_SIZE)
#define ACV_MAP_NAME   L"Local\\AndroCamVideo"

/* Virtual camera CLSID {9F5F8B64-6B4C-4C93-8B8C-0A7C2F6D3A11}; must equal winutil.VCAM_CLSID */
#define ACV_CLSID_DATA 0x9F5F8B64, 0x6B4C, 0x4C93, 0x8B, 0x8C, 0x0A, 0x7C, 0x2F, 0x6D, 0x3A, 0x11

typedef struct {
    uint32_t magic, version, width, height, format, stride;
    uint64_t frame_id;
    uint32_t active, reserved;
    uint64_t timestamp_ms;
    uint32_t connected, flags;
} ACV_Header;

#ifdef __cplusplus
static_assert(sizeof(ACV_Header) == 56, "ACV_Header layout");
static_assert(offsetof(ACV_Header, frame_id) == 24 && offsetof(ACV_Header, active) == 32 &&
              offsetof(ACV_Header, timestamp_ms) == 40 && offsetof(ACV_Header, connected) == 48, "ACV_Header offsets");
#else
_Static_assert(sizeof(ACV_Header) == 56, "ACV_Header layout");
_Static_assert(offsetof(ACV_Header, frame_id) == 24 && offsetof(ACV_Header, active) == 32 &&
               offsetof(ACV_Header, timestamp_ms) == 40 && offsetof(ACV_Header, connected) == 48, "ACV_Header offsets");
#endif

#define ACV_IDLE_B 30
#define ACV_IDLE_G 26
#define ACV_IDLE_R 26

static inline void acv_fill(uint8_t *dst, size_t pixels, uint8_t b, uint8_t g, uint8_t r) {
    for (size_t i = 0; i < pixels; i++) { dst[3 * i] = b; dst[3 * i + 1] = g; dst[3 * i + 2] = r; }
}

/* Draw the newest frame into dst (BGR24, dw x dh), scaled to fit with black bars (nearest neighbour).
   bottom_up != 0 writes rows bottom-first (a DirectShow RGB24 DIB with positive biHeight).
   Returns 1 when a live frame was drawn, 0 when there is none (dst is filled with the idle colour). */
static inline int acv_render(const uint8_t *base, size_t size, uint8_t *dst, int dw, int dh, int bottom_up) {
    if (dw <= 0 || dh <= 0) return 0;
    const volatile ACV_Header *h = (const volatile ACV_Header *)base;
    for (int attempt = 0; attempt < 2; attempt++) {
        if (!base || size < ACV_TOTAL || h->magic != ACV_MAGIC || h->format != ACV_FMT_BGR24 ||
            h->frame_id == 0 || h->connected == 0) break;
        uint32_t sw = h->width, sh = h->height, stride = h->stride, active = h->active & 1u;
        uint64_t id0 = h->frame_id;
        if (sw < 16 || sh < 16 || sw > ACV_MAX_W || sh > ACV_MAX_H || stride < sw * 3u) break;
        const uint8_t *src = base + ACV_HEADER + (size_t)active * ACV_BUF_SIZE;

        int tw, th;                                       /* fit inside dw x dh keeping the aspect ratio */
        if ((uint64_t)dw * sh <= (uint64_t)dh * sw) { tw = dw; th = (int)(((uint64_t)sh * dw) / sw); }
        else { th = dh; tw = (int)(((uint64_t)sw * dh) / sh); }
        if (tw < 1) tw = 1;
        if (th < 1) th = 1;
        int ox = (dw - tw) / 2, oy = (dh - th) / 2;

        for (int y = 0; y < dh; y++) {
            uint8_t *row = dst + (size_t)(bottom_up ? dh - 1 - y : y) * dw * 3;
            if (y < oy || y >= oy + th) { memset(row, 0, (size_t)dw * 3); continue; }
            const uint8_t *srow = src + (size_t)((uint64_t)(y - oy) * sh / th) * stride;
            if (ox) memset(row, 0, (size_t)ox * 3);
            if (tw == (int)sw) memcpy(row + (size_t)ox * 3, srow, (size_t)tw * 3);
            else for (int x = 0; x < tw; x++) memcpy(row + (size_t)(ox + x) * 3, srow + (size_t)((uint64_t)x * sw / tw) * 3, 3);
            if (ox + tw < dw) memset(row + (size_t)(ox + tw) * 3, 0, (size_t)(dw - ox - tw) * 3);
        }
        if (h->frame_id - id0 < 2) return 1;              /* writer lapped us: a buffer may have been overwritten; retry */
    }
    acv_fill(dst, (size_t)dw * dh, ACV_IDLE_B, ACV_IDLE_G, ACV_IDLE_R);
    return 0;
}

#endif
