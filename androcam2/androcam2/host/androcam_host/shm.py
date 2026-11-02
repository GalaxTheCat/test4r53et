"""Shared-memory bridge to the native components.

Video  (read by the DirectShow virtual camera, vcam/shm_layout.h is the C twin of this file):
  header 64 bytes little-endian:
    0 magic u32 'ACV1' | 4 version u32 | 8 width u32 | 12 height u32 | 16 format u32 (1 = BGR24 top-down)
    20 stride u32 | 24 frame_id u64 | 32 active u32 (buffer holding the newest frame) | 36 reserved
    40 timestamp_ms u64 | 48 connected u32 | 52 flags u32
  then two frame buffers of MAX_W*MAX_H*3 bytes (double buffered).
Audio  (for the microphone driver, written now so the driver only has to read it):
  header 64 bytes: 0 magic 'ACA1' | 4 version | 8 sample_rate | 12 channels | 16 bits | 20 capacity_frames
    24 write_pos u64 (total frames written) | 32 read_pos u64 (driver) | 40 connected u32
  then capacity_frames * channels * 2 bytes of PCM16 ring.
"""
from __future__ import annotations

import mmap
import os
import struct
import sys
from typing import Optional

VIDEO_NAME = "Local\\AndroCamVideo"
AUDIO_NAME = "Local\\AndroCamAudio"
VIDEO_MAGIC = 0x31564341      # 'ACV1' little endian
AUDIO_MAGIC = 0x31414341      # 'ACA1'
HEADER = 64
MAX_W, MAX_H = 1920, 1080
FMT_BGR24 = 1
AUDIO_RATE, AUDIO_CH, AUDIO_SECONDS = 48000, 1, 2


def _open_map(size: int, name: str, path: Optional[str]) -> mmap.mmap:
    if sys.platform == "win32" and not path:
        return mmap.mmap(-1, size, tagname=name)
    path = path or os.path.join(os.environ.get("TMPDIR", "/tmp"), name.replace("\\", "_"))
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o666)
    try:
        os.ftruncate(fd, size)
        return mmap.mmap(fd, size)
    finally:
        os.close(fd)


class VideoShm:
    def __init__(self, path: Optional[str] = None, name: str = VIDEO_NAME):
        self.buf_size = MAX_W * MAX_H * 3
        self.mm = _open_map(HEADER + 2 * self.buf_size, name, path)
        self.frame_id = 0
        self.width, self.height = 1280, 720
        struct.pack_into("<IIIIIIQIIQII", self.mm, 0, VIDEO_MAGIC, 1, self.width, self.height, FMT_BGR24,
                         self.width * 3, 0, 0, 0, 0, 0, 0)

    def set_size(self, w: int, h: int) -> None:
        if not (16 <= w <= MAX_W and 16 <= h <= MAX_H):
            raise ValueError("canvas size out of range")
        self.width, self.height = w, h
        struct.pack_into("<II", self.mm, 8, w, h)
        struct.pack_into("<I", self.mm, 20, w * 3)

    def write_frame(self, bgr: bytes, ts_ms: int = 0) -> None:
        if len(bgr) != self.width * self.height * 3:
            raise ValueError("frame size does not match the canvas")
        active = struct.unpack_from("<I", self.mm, 32)[0]
        nxt = 1 - active
        off = HEADER + nxt * self.buf_size
        self.mm[off:off + len(bgr)] = bgr
        self.frame_id += 1
        struct.pack_into("<Q", self.mm, 24, self.frame_id)       # writer is the only mutator
        struct.pack_into("<I", self.mm, 32, nxt)
        struct.pack_into("<Q", self.mm, 40, ts_ms)

    def set_connected(self, on: bool) -> None:
        struct.pack_into("<I", self.mm, 48, 1 if on else 0)

    def close(self) -> None:
        self.mm.close()


def read_video(mm) -> Optional[dict]:
    """Reference reader (what the camera DLL does). Returns None until a frame exists."""
    magic, ver, w, h, fmt, stride, fid, active, _r, ts, conn, _f = struct.unpack_from("<IIIIIIQIIQII", mm, 0)
    if magic != VIDEO_MAGIC or fid == 0 or fmt != FMT_BGR24:
        return None
    off = HEADER + active * MAX_W * MAX_H * 3
    return {"w": w, "h": h, "frame_id": fid, "connected": bool(conn), "data": bytes(mm[off:off + stride * h])}


class AudioShm:
    def __init__(self, path: Optional[str] = None, name: str = AUDIO_NAME):
        self.capacity = AUDIO_RATE * AUDIO_SECONDS
        self.mm = _open_map(HEADER + self.capacity * AUDIO_CH * 2, name, path)
        self.pos = 0
        struct.pack_into("<IIIIIIQQI", self.mm, 0, AUDIO_MAGIC, 1, AUDIO_RATE, AUDIO_CH, 16, self.capacity, 0, 0, 0)

    def write(self, pcm: bytes) -> None:
        """pcm: mono s16le at AUDIO_RATE."""
        n = len(pcm) // 2
        if n == 0:
            return
        base = HEADER
        start = (self.pos % self.capacity) * 2
        first = min(len(pcm), self.capacity * 2 - start)
        self.mm[base + start:base + start + first] = pcm[:first]
        if first < len(pcm):
            self.mm[base:base + len(pcm) - first] = pcm[first:]
        self.pos += n
        struct.pack_into("<Q", self.mm, 24, self.pos)

    def set_connected(self, on: bool) -> None:
        struct.pack_into("<I", self.mm, 40, 1 if on else 0)

    def close(self) -> None:
        self.mm.close()


def read_audio_ring(mm, from_pos: int, count: int) -> bytes:
    """Reference reader (what the driver does): count frames starting at absolute frame from_pos."""
    cap = struct.unpack_from("<I", mm, 20)[0]
    out = bytearray()
    for i in range(count):
        p = ((from_pos + i) % cap) * 2
        out += mm[HEADER + p:HEADER + p + 2]
    return bytes(out)
