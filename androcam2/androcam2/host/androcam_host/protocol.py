"""AndroCam wire protocol v1 (phone <-> PC), plain TCP on the LAN / USB tethering.

Packet:  magic b'AC' | type u8 | flags u8 | length u32 (big endian) | payload
Flow:    phone connects -> PC sends CHALLENGE(16 random bytes)
         phone sends AUTH(HMAC-SHA256(key=pair code, msg=nonce)) -> PC replies AUTH_OK / AUTH_FAIL
         phone sends HELLO(json), then streams VIDEO (JPEG) and AUDIO (PCM16); PC may send CONTROL(json)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import struct
from typing import Optional, Tuple

PROTO_VERSION = 1
PORT_DISCOVERY = 47800      # UDP: phone asks "ANDROCAM?", PC answers with JSON
PORT_PHONE = 47801          # TCP: phone <-> PC session
PORT_DOWNLOAD = 47802       # HTTP: serves the Android app to phones on the LAN
PORT_UI = 47810             # HTTP: local UI (127.0.0.1 only)

MAGIC = b"AC"
HDR = struct.Struct(">2sBBI")
MAX_PAYLOAD = 16 * 1024 * 1024

(T_HELLO, T_CHALLENGE, T_AUTH, T_AUTH_OK, T_AUTH_FAIL, T_VIDEO, T_AUDIO,
 T_CONTROL, T_STATUS, T_PING, T_PONG) = range(1, 12)

VIDEO_HDR = struct.Struct(">HHHBBI")   # width, height, rotation(cw degrees), mirror, facing, ts_ms
AUDIO_HDR = struct.Struct(">IBBHI")    # sample_rate, channels, bits, reserved, ts_ms
FACING = {0: "back", 1: "front", 2: "external"}


class ProtocolError(Exception):
    pass


def pack(ptype: int, payload: bytes = b"", flags: int = 0) -> bytes:
    if len(payload) > MAX_PAYLOAD:
        raise ProtocolError("payload too large")
    return HDR.pack(MAGIC, ptype, flags, len(payload)) + payload


def pack_json(ptype: int, obj: dict) -> bytes:
    return pack(ptype, json.dumps(obj, separators=(",", ":")).encode())


def read_exact(sock, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("connection closed")
        buf += chunk
    return bytes(buf)


def read_packet(sock) -> Tuple[int, int, bytes]:
    magic, ptype, flags, length = HDR.unpack(read_exact(sock, HDR.size))
    if magic != MAGIC:
        raise ProtocolError("bad magic")
    if length > MAX_PAYLOAD:
        raise ProtocolError("payload too large")
    return ptype, flags, read_exact(sock, length) if length else b""


def make_auth(code: str, nonce: bytes) -> bytes:
    return hmac.new(code.encode(), nonce, hashlib.sha256).digest()


def verify_auth(code: str, nonce: bytes, digest: bytes) -> bool:
    return hmac.compare_digest(make_auth(code, nonce), digest)


def pack_video(w: int, h: int, rotation: int, mirror: bool, facing: int, ts_ms: int, jpeg: bytes) -> bytes:
    return pack(T_VIDEO, VIDEO_HDR.pack(w, h, rotation, 1 if mirror else 0, facing, ts_ms & 0xFFFFFFFF) + jpeg)


def unpack_video(payload: bytes) -> Tuple[dict, bytes]:
    if len(payload) <= VIDEO_HDR.size:
        raise ProtocolError("short video packet")
    w, h, rot, mirror, facing, ts = VIDEO_HDR.unpack_from(payload)
    return ({"w": w, "h": h, "rotation": rot % 360, "mirror": bool(mirror),
             "facing": FACING.get(facing, "back"), "ts": ts}, payload[VIDEO_HDR.size:])


def pack_audio(rate: int, channels: int, ts_ms: int, pcm: bytes) -> bytes:
    return pack(T_AUDIO, AUDIO_HDR.pack(rate, channels, 16, 0, ts_ms & 0xFFFFFFFF) + pcm)


def unpack_audio(payload: bytes) -> Tuple[dict, bytes]:
    if len(payload) < AUDIO_HDR.size:
        raise ProtocolError("short audio packet")
    rate, ch, bits, _, ts = AUDIO_HDR.unpack_from(payload)
    if bits != 16 or ch not in (1, 2) or not (8000 <= rate <= 192000):
        raise ProtocolError("unsupported audio format")
    return {"rate": rate, "channels": ch, "ts": ts}, payload[AUDIO_HDR.size:]


def parse_json(payload: bytes) -> Optional[dict]:
    try:
        v = json.loads(payload.decode())
        return v if isinstance(v, dict) else None
    except (ValueError, UnicodeDecodeError):
        return None
