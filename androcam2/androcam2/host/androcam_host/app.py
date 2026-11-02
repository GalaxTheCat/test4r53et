"""HostApp: owns settings, shared memory, the frame pipeline and the phone server."""
from __future__ import annotations

import json
import secrets
import socket
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from . import __version__, winutil
from . import protocol as P
from .frames import FrameProcessor
from .server import PhoneServer
from .shm import AUDIO_RATE, AudioShm, VideoShm

CANVASES = [(1920, 1080), (1280, 720), (854, 480), (640, 360)]


def local_ips() -> List[str]:
    ips: List[str] = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))             # no packet is sent; just picks the outgoing interface
        ips.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips:
                ips.append(ip)
    except OSError:
        pass
    return [i for i in ips if not i.startswith(("127.", "169.254."))]


def to_mono_48k(pcm: bytes, rate: int, channels: int) -> bytes:
    a = np.frombuffer(pcm[: len(pcm) // (2 * channels) * 2 * channels], dtype="<i2")
    if channels == 2:
        a = a.reshape(-1, 2).astype(np.int32).mean(axis=1).astype("<i2")
    if rate != AUDIO_RATE and len(a) > 1:
        n = int(len(a) * AUDIO_RATE / rate)
        a = np.interp(np.linspace(0, len(a) - 1, n), np.arange(len(a)), a.astype(np.float32)).astype("<i2")
    return a.tobytes()


def level_of(pcm: bytes) -> float:
    a = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2")
    if a.size == 0:
        return 0.0
    rms = float(np.sqrt(np.mean(np.square(a.astype(np.float32))))) / 32768.0
    db = 20 * np.log10(max(rms, 1e-5))
    return float(min(1.0, max(0.0, (db + 60.0) / 60.0)))


class HostApp:
    DEFAULTS = {"first_run": True, "wizard_step": 0, "pair_code": "", "canvas": [1280, 720],
                "mirror": False, "mic_muted": False}

    def __init__(self, settings_path: Optional[Path] = None, video_path: Optional[str] = None,
                 audio_path: Optional[str] = None, phone_port: int = P.PORT_PHONE,
                 discovery_port: int = P.PORT_DISCOVERY, pc_name: Optional[str] = None):
        self.settings_path = settings_path or winutil.settings_path()
        self.settings: Dict = dict(self.DEFAULTS)
        try:
            data = json.loads(self.settings_path.read_text())
            if isinstance(data, dict):
                self.settings.update(data)
        except (OSError, ValueError):
            pass
        if not (isinstance(self.settings["pair_code"], str) and len(self.settings["pair_code"]) == 6):
            self.settings["pair_code"] = f"{secrets.randbelow(10**6):06d}"
        self.save()
        self.pc_name = pc_name or socket.gethostname()
        self.video = VideoShm(video_path)
        self.audio = AudioShm(audio_path)
        cw, ch = self.settings["canvas"]
        self.frames = FrameProcessor(self.video, (cw, ch), bool(self.settings["mirror"]))
        self.level = 0.0
        self.quit_event = threading.Event()
        self.stopping = False
        self.server = PhoneServer(lambda: self.settings["pair_code"], self._on_video, self._on_audio,
                                  self._on_change, pc_name=self.pc_name, port=phone_port,
                                  discovery_port=discovery_port)

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self.server.start()

    def stop(self) -> None:
        self.stopping = True
        self.server.stop()
        self.frames.stop()
        self.audio.set_connected(False)
        self.video.set_connected(False)

    def save(self) -> None:
        try:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            self.settings_path.write_text(json.dumps(self.settings, indent=2))
        except OSError:
            pass

    # ------------------------------------------------------------ phone callbacks
    def _on_video(self, meta: dict, jpeg: bytes) -> None:
        self.frames.submit(meta, jpeg)

    def _on_audio(self, meta: dict, pcm: bytes) -> None:
        try:
            mono = to_mono_48k(pcm, meta["rate"], meta["channels"])
        except Exception:  # noqa: BLE001
            return
        if self.settings["mic_muted"]:
            mono = bytes(len(mono))
        self.audio.write(mono)
        self.audio.set_connected(True)
        lv = level_of(mono)
        self.level = lv if lv > self.level else self.level * 0.85 + lv * 0.15     # fast attack, slow decay

    def _on_change(self) -> None:
        if not self.server.connected:
            self.frames.reset()
            self.audio.set_connected(False)
            self.level = 0.0

    # ------------------------------------------------------------ state for the UI
    def state(self) -> dict:
        s = self.server.session
        d64, d32 = winutil.vcam_dlls()
        return {
            "version": __version__, "pc_name": self.pc_name, "ips": local_ips(),
            "pair_code": self.settings["pair_code"], "phone_port": self.server.port,
            "download_port": P.PORT_DOWNLOAD, "apk": winutil.resource("AndroCam.apk") is not None,
            "first_run": self.settings["first_run"], "wizard_step": self.settings["wizard_step"],
            "phone": {"connected": s is not None, "info": s.info if s else {}, "status": s.status if s else {},
                      "address": s.addr[0] if s else ""},
            "video": {"fps": self.frames.fps, "canvas": list(self.frames.canvas), "mirror": self.frames.mirror,
                      "frames": self.frames.frames, "errors": self.frames.errors,
                      "last_error": self.frames.last_error, "seq": self.frames.latest_seq},
            "audio": {"level": round(self.level, 3), "muted": self.settings["mic_muted"]},
            "vcam": {"installed": winutil.vcam_registered(), "files": bool(d64 or d32),
                     "supported": winutil.IS_WINDOWS},
            "mic_driver": {"installed": False, "available": False},
            "autostart": winutil.autostart_get(),
            "auth_failures": self.server.auth_failures,
        }

    # ------------------------------------------------------------ control from the UI
    def handle_control(self, req: dict) -> Tuple[bool, str]:
        cmd = req.get("cmd")
        if cmd == "torch":
            return self._phone({"cmd": "torch", "on": bool(req.get("on"))})
        if cmd == "camera":
            if req.get("facing") not in ("front", "back"):
                return False, "facing must be front or back"
            return self._phone({"cmd": "camera", "facing": req["facing"]})
        if cmd == "quality":
            try:
                w, h, j, f = int(req["w"]), int(req["h"]), int(req.get("jpeg", 80)), int(req.get("fps", 30))
            except (KeyError, ValueError, TypeError):
                return False, "bad quality settings"
            if not (160 <= w <= 4096 and 120 <= h <= 4096 and 30 <= j <= 100 and 5 <= f <= 60):
                return False, "quality settings out of range"
            return self._phone({"cmd": "quality", "w": w, "h": h, "jpeg": j, "fps": f})
        if cmd == "phone_mic":
            return self._phone({"cmd": "mic", "on": bool(req.get("on"))})
        if cmd == "canvas":
            size = (req.get("w"), req.get("h"))
            if size not in CANVASES:
                return False, "unsupported output size"
            self.frames.set_canvas(*size)
            self.settings["canvas"] = list(size)
        elif cmd == "mirror":
            self.frames.set_mirror(bool(req.get("on")))
            self.settings["mirror"] = bool(req.get("on"))
        elif cmd == "mic_mute":
            self.settings["mic_muted"] = bool(req.get("on"))
        elif cmd == "regen_code":
            self.settings["pair_code"] = f"{secrets.randbelow(10**6):06d}"
            s = self.server.session
            if s:
                s.close()                          # the phone must re-pair with the new code
        elif cmd == "autostart":
            if not winutil.autostart_set(bool(req.get("on"))) and winutil.IS_WINDOWS:
                return False, "could not change start-up setting"
        elif cmd == "setup":
            step = int(req.get("step", 0))
            self.settings["wizard_step"] = max(0, min(step, 10))
            if "done" in req:
                self.settings["first_run"] = not bool(req["done"])
        else:
            return False, "unknown command"
        self.save()
        return True, "ok"

    def _phone(self, msg: dict) -> Tuple[bool, str]:
        return (True, "sent") if self.server.send_control(msg) else (False, "No phone connected.")
