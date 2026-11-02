"""Phone JPEG -> upright, letterboxed BGR24 canvas -> shared memory (read by the virtual camera)."""
from __future__ import annotations

import io
import threading
import time
from collections import deque
from typing import Deque, Optional, Tuple

import numpy as np
from PIL import Image

from .shm import VideoShm

try:
    _BILINEAR = Image.Resampling.BILINEAR
    _T = Image.Transpose
    _ROT = {0: None, 90: _T.ROTATE_270, 180: _T.ROTATE_180, 270: _T.ROTATE_90}   # clockwise degrees
    _MIRROR = _T.FLIP_LEFT_RIGHT
except AttributeError:      # very old Pillow
    _BILINEAR = Image.BILINEAR
    _ROT = {0: None, 90: Image.ROTATE_270, 180: Image.ROTATE_180, 270: Image.ROTATE_90}
    _MIRROR = Image.FLIP_LEFT_RIGHT


def compose(jpeg: bytes, rotation: int, mirror: bool, canvas: Tuple[int, int]) -> bytes:
    """Decode, rotate (clockwise degrees), mirror, scale to fit and letterbox. Returns BGR24 top-down."""
    cw, ch = canvas
    img = Image.open(io.BytesIO(jpeg))
    img.draft("RGB", (max(cw, ch), max(cw, ch)))          # cheap downscale-while-decoding for big frames
    img = img.convert("RGB")
    rot = _ROT.get(rotation % 360)
    if rot is not None:
        img = img.transpose(rot)
    if mirror:
        img = img.transpose(_MIRROR)
    iw, ih = img.size
    k = min(cw / iw, ch / ih)
    nw, nh = max(1, round(iw * k)), max(1, round(ih * k))
    if (nw, nh) != (iw, ih):
        img = img.resize((nw, nh), _BILINEAR)
    if (nw, nh) != (cw, ch):
        out = Image.new("RGB", (cw, ch), (0, 0, 0))
        out.paste(img, ((cw - nw) // 2, (ch - nh) // 2))
        img = out
    return np.asarray(img)[:, :, ::-1].tobytes()


class FrameProcessor:
    """Keeps only the newest phone frame (never builds a backlog => low latency)."""

    def __init__(self, shm: VideoShm, canvas: Tuple[int, int] = (1280, 720), mirror: bool = False):
        self.shm = shm
        self.canvas = canvas
        self.mirror = mirror
        self.shm.set_size(*canvas)
        self.frames = 0
        self.errors = 0
        self.last_error = ""
        self.latest_jpeg: Optional[bytes] = None      # for the UI preview (no decode needed)
        self.latest_seq = 0
        self._pending = None
        self._lock = threading.Lock()
        self._event = threading.Event()
        self._stop = False
        self._times: Deque[float] = deque(maxlen=90)
        self._thread = threading.Thread(target=self._run, daemon=True, name="frames")
        self._thread.start()

    @property
    def fps(self) -> float:
        now = time.time()
        recent = [t for t in self._times if now - t < 2.0]
        return round(len(recent) / 2.0, 1) if len(recent) > 1 else 0.0

    def set_canvas(self, w: int, h: int) -> None:
        with self._lock:
            self.shm.set_size(w, h)
            self.canvas = (w, h)

    def set_mirror(self, on: bool) -> None:
        self.mirror = bool(on)

    def submit(self, meta: dict, jpeg: bytes) -> None:
        with self._lock:
            self._pending = (meta, jpeg)
            self.latest_jpeg = jpeg
            self.latest_seq += 1
        self._event.set()

    def reset(self) -> None:
        """Phone gone: tell the camera so it can show its 'waiting' picture."""
        with self._lock:
            self._pending = None
            self.latest_jpeg = None
            self.latest_seq += 1
        self._times.clear()
        self.shm.set_connected(False)

    def stop(self) -> None:
        self._stop = True
        self._event.set()
        self._thread.join(timeout=2)

    def _run(self) -> None:
        while not self._stop:
            self._event.wait(0.5)
            with self._lock:
                item, self._pending = self._pending, None
                self._event.clear()
                canvas, mirror = self.canvas, self.mirror
            if item is None:
                continue
            meta, jpeg = item
            try:
                bgr = compose(jpeg, meta.get("rotation", 0), meta.get("mirror", False) ^ mirror, canvas)
                with self._lock:
                    if canvas == self.canvas:           # canvas may have changed while decoding
                        self.shm.write_frame(bgr, meta.get("ts", 0))
                        self.shm.set_connected(True)
                self.frames += 1
                self._times.append(time.time())
            except Exception as e:  # noqa: BLE001 - a bad frame must never kill the pipeline
                self.errors += 1
                self.last_error = f"{type(e).__name__}: {e}"
