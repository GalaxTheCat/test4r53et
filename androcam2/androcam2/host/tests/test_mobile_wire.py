"""Transcribes the byte-building code of the Kotlin (android/.../Protocol.kt) and Swift (ios/.../Protocol.swift)
apps field-for-field, and checks the PC parser accepts it. Catches layout/endianness mistakes in code we can't compile here."""
import hashlib, hmac, pathlib, re, struct, sys
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from androcam_host import protocol as P

# ---- Kotlin: ByteBuffer (big-endian by default) calls, in source order
def kt_packet(t, payload=b""):                 # put('A').put('C').put(type).put(0).putInt(len).put(payload)
    return b"A" + b"C" + bytes([t]) + bytes([0]) + struct.pack(">i", len(payload)) + payload
def kt_video(w, h, rot, mirror, facing, ts, jpeg):   # header, then putShort x3, put mirror, put facing, putInt ts, put jpeg
    return (b"AC" + bytes([P.T_VIDEO, 0]) + struct.pack(">i", 12 + len(jpeg)) + struct.pack(">hhh", w, h, rot)
            + bytes([1 if mirror else 0]) + bytes([facing]) + struct.pack(">i", ts) + jpeg)
def kt_audio(rate, ch, ts, pcm):               # putInt rate, put ch, put 16, putShort 0, putInt ts
    return b"AC" + bytes([P.T_AUDIO, 0]) + struct.pack(">i", 12 + len(pcm)) + struct.pack(">i", rate) + bytes([ch]) + bytes([16]) + struct.pack(">h", 0) + struct.pack(">i", ts) + pcm
def kt_hmac(code, nonce): return hmac.new(code.encode(), nonce, hashlib.sha256).digest()      # Mac "HmacSHA256", key = UTF-8 code

# ---- Swift: Data appends with be16/be32 helpers
def be16(v): return bytes([v >> 8, v & 0xFF])
def be32(v): return bytes([v >> 24, (v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF])
def sw_packet(t, payload=b""): return bytes([0x41, 0x43, t, 0]) + be32(len(payload)) + payload
def sw_video(w, h, rot, mirror, facing, ts, jpeg): return sw_packet(P.T_VIDEO, be16(w) + be16(h) + be16(rot) + bytes([1 if mirror else 0]) + bytes([facing]) + be32(ts) + jpeg)
def sw_audio(rate, ch, ts, pcm): return sw_packet(P.T_AUDIO, be32(rate) + bytes([ch]) + bytes([16]) + be16(0) + be32(ts) + pcm)

import socket
def parse(raw):
    a, b = socket.socketpair(); a.sendall(raw); a.close()
    t, f, pl = P.read_packet(b); b.close(); return t, f, pl

jpeg = b"\xff\xd8\xff\xe0" + bytes(range(200)) + b"\xff\xd9"
for name, vid, aud, pkt in (("kotlin", kt_video, kt_audio, kt_packet), ("swift", sw_video, sw_audio, sw_packet)):
    for (w, h, rot, mir, fac, ts) in [(1280, 720, 90, False, 0, 123456), (1920, 1080, 270, True, 1, 0xFFFFFF), (4000, 3000, 0, False, 2, 1)]:
        t, f, pl = parse(vid(w, h, rot, mir, fac, ts, jpeg)); assert t == P.T_VIDEO and f == 0, name
        meta, j = P.unpack_video(pl)
        assert meta == {"w": w, "h": h, "rotation": rot, "mirror": mir, "facing": P.FACING[fac], "ts": ts} and j == jpeg, (name, meta)
    pcm = b"".join(struct.pack("<h", i * 100) for i in range(-5, 5))
    t, _, pl = parse(aud(48000, 1, 4242, pcm)); meta, p = P.unpack_audio(pl)
    assert t == P.T_AUDIO and meta == {"rate": 48000, "channels": 1, "ts": 4242} and p == pcm, name
    t, _, pl = parse(pkt(P.T_PONG)); assert t == P.T_PONG and pl == b""
    t, _, pl = parse(pkt(P.T_AUTH, b"x" * 32)); assert t == P.T_AUTH and len(pl) == 32
# both phones authenticate with the same HMAC the PC verifies
nonce = bytes(range(16)); assert P.verify_auth("042042", nonce, kt_hmac("042042", nonce))
# the PC's packets decode the way both phones read them: 'A','C', type, flags, big-endian length
raw = P.pack_json(P.T_CONTROL, {"cmd": "torch", "on": True})
assert raw[:2] == b"AC" and raw[2] == P.T_CONTROL and raw[3] == 0 and struct.unpack(">I", raw[4:8])[0] == len(raw) - 8
# the Kotlin source really contains the layout we transcribed (guards against the two drifting apart)
k = (HERE.parent.parent / "android/app/src/main/java/app/androcam/Protocol.kt").read_text()
assert "putShort(w.toShort()).putShort(h.toShort()).putShort(rotation.toShort())" in k and "putInt(12 + jpeg.size)" in k
assert "putInt(rate).put(channels.toByte()).put(16.toByte()).putShort(0.toShort()).putInt(tsMs)" in k
s = (HERE.parent.parent / "ios/AndroCam/Protocol.swift").read_text()
assert "be16(UInt16(truncatingIfNeeded: w))" in s and "p.append(be32(ts))" in s and "p.append(be32(rate))" in s
print("MOBILE WIRE TESTS PASSED")
