# Architecture

```
phone app --TCP 47801--> PhoneServer --> FrameProcessor --> shared memory "Local\AndroCamVideo" --> androcam_vcam_*.dll --> apps
   |  (UDP 47800 discovery, HTTP 47802 APK download)      \-> AudioShm ring "Local\AndroCamAudio" --> (future mic driver)
   '--- JPEG frames + PCM16 audio; PC -> phone: flash, camera, quality, mic         UI: http://127.0.0.1:47810 (Edge app window)
```
* **Protocol** (`host/androcam_host/protocol.py`): `AC | type | flags | u32 length | payload`. The PC sends a random challenge; the phone answers
  HMAC-SHA256(pairing code, nonce). Five wrong codes block an address for a minute.
* **Frames**: phone sends JPEG (cheap on the phone, no codec on the PC). The PC rotates, optionally mirrors, letterboxes to the output size
  (360p-1080p) and writes BGR24 into one of two buffers, then flips `active` (no tearing).
* **Virtual camera** (`vcam/`): a DirectShow source filter registered in the "video input" category. `shm_layout.h::acv_render` scales/letterboxes to
  whatever size the app negotiated. When no phone is connected it shows a dark idle frame.
* **UI**: the host serves a single HTML page on localhost only. POSTs need an `X-AndroCam: 1` header and a localhost `Host`, so web pages can't drive it.
