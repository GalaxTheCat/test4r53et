"""Compiles vcam/shm_layout.h with gcc and checks its pixels against the Python writer + a reference renderer."""
import os, pathlib, re, subprocess, sys, tempfile
import numpy as np
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from androcam_host import shm, winutil
VC = HERE.parent.parent / "vcam"
tmp = pathlib.Path(tempfile.mkdtemp())

# strict compiles: as C (harness) and as C++ (what vcam.cpp does)
subprocess.run(["gcc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-O2", "-o", str(tmp / "acv_test"), str(VC / "acv_test.c")], check=True)
subprocess.run(["g++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-fsyntax-only", "-x", "c++", str(VC / "shm_layout.h")], check=True)

def ref(frame, sw, sh, dw, dh, bottom_up):
    """Independent re-implementation with the same integer rules."""
    if dw * sh <= dh * sw: tw, th = dw, sh * dw // sw
    else: th, tw = dh, sw * dh // sh
    tw, th = max(tw, 1), max(th, 1); ox, oy = (dw - tw) // 2, (dh - th) // 2
    out = np.zeros((dh, dw, 3), np.uint8)
    ys = (np.arange(th) * sh // th); xs = (np.arange(tw) * sw // tw)
    out[oy:oy + th, ox:ox + tw] = frame[ys][:, xs]
    return (out[::-1] if bottom_up else out).tobytes()

def run(path, dw, dh, bu):
    o = tmp / "out.bin"
    r = subprocess.run([str(tmp / "acv_test"), str(path), str(dw), str(dh), str(bu), str(o)], capture_output=True, text=True)
    assert r.returncode == 0, r
    return r.stdout.strip(), o.read_bytes()

path = tmp / "v.shm"; v = shm.VideoShm(str(path))
IDLE = bytes([30, 26, 26])

# no frame yet -> idle colour
st, out = run(path, 64, 36, 0); assert st == "idle" and out == IDLE * (64 * 36)
rng = np.random.default_rng(7)
for (cw, ch) in [(1280, 720), (1920, 1080), (854, 480)]:
    v.set_size(cw, ch)
    frame = rng.integers(0, 256, (ch, cw, 3), dtype=np.uint8)
    v.write_frame(frame.tobytes(), 1); v.set_connected(True)
    for (dw, dh) in [(cw, ch), (640, 480), (1920, 1080), (320, 240), (1000, 1000), (1280, 720), (1920, 800), (800, 300), (640, 100)]:   # last three: wider than 16:9 -> side bars
        for bu in (0, 1):
            st, out = run(path, dw, dh, bu)
            assert st == "live" and out == ref(frame, cw, ch, dw, dh, bu), (cw, ch, dw, dh, bu)
    # identity case is literally the source (top-down) / row-reversed (bottom-up)
    assert run(path, cw, ch, 0)[1] == frame.tobytes() and run(path, cw, ch, 1)[1] == frame[::-1].tobytes()
# double buffering: second frame replaces the first
f2 = rng.integers(0, 256, (480, 854, 3), dtype=np.uint8)
v.set_size(854, 480); v.write_frame(f2.tobytes(), 2)
assert run(path, 854, 480, 0)[1] == f2.tobytes()
# phone disconnected -> idle again even though old pixels are still in memory
v.set_connected(False); st, out = run(path, 64, 36, 1); assert st == "idle" and out == IDLE * (64 * 36)
# corrupt header -> idle, never a crash
import struct
v.set_connected(True); struct.pack_into("<I", v.mm, 8, 99999); st, _ = run(path, 64, 36, 0); assert st == "idle"
struct.pack_into("<I", v.mm, 8, 854); struct.pack_into("<I", v.mm, 0, 0xDEADBEEF); st, _ = run(path, 64, 36, 0); assert st == "idle"
# truncated mapping -> idle
(tmp / "short.shm").write_bytes(b"\0" * 100); st, _ = run(tmp / "short.shm", 64, 36, 0); assert st == "idle"

# GUID in C header == GUID Python registers/looks up
m = re.search(r"ACV_CLSID_DATA\s+(0x\w+),\s*(0x\w+),\s*(0x\w+),\s*((?:0x\w+,?\s*){8})", (VC / "shm_layout.h").read_text())
d1, d2, d3 = (int(m.group(i), 16) for i in (1, 2, 3)); tail = [int(x, 16) for x in re.findall(r"0x\w+", m.group(4))]
guid = "{%08X-%04X-%04X-%02X%02X-%s}" % (d1, d2, d3, tail[0], tail[1], "".join("%02X" % b for b in tail[2:]))
assert guid == winutil.VCAM_CLSID.upper(), (guid, winutil.VCAM_CLSID)
cpp = (VC / "vcam.cpp").read_text()
m2 = re.search(r"DEFINE_GUID\(CLSID_AndroCamVCam,\s*(0x\w+),\s*(0x\w+),\s*(0x\w+),\s*((?:0x\w+,?\s*){8})\)", cpp)
t2 = [int(x, 16) for x in re.findall(r"0x\w+", m2.group(4))]
guid2 = "{%08X-%04X-%04X-%02X%02X-%s}" % (int(m2.group(1), 16), int(m2.group(2), 16), int(m2.group(3), 16), t2[0], t2[1], "".join("%02X" % b for b in t2[2:]))
assert guid2 == guid, (guid2, guid)
# .def exports everything COM needs
d = (VC / "vcam.def").read_text()
for sym in ("DllGetClassObject", "DllCanUnloadNow", "DllRegisterServer", "DllUnregisterServer"): assert sym in d, sym   # exported
for sym in ("DllRegisterServer", "DllUnregisterServer"): assert "STDAPI %s()" % sym in cpp, sym                   # defined by us (the other two come from strmbase)
# cheap structural sanity on the C++ we cannot compile here: balanced braces/parens, and every pin method we declare is defined
code = re.sub(r'//[^\n]*|"(?:\\.|[^"\\])*"', "", cpp)
assert code.count("{") == code.count("}") and code.count("(") == code.count(")"), "unbalanced brackets in vcam.cpp"
for meth in ("FillBuffer", "DecideBufferSize", "CheckMediaType", "GetMediaType", "SetMediaType", "OnThreadCreate", "SetFormat", "GetFormat",
             "GetNumberOfCapabilities", "GetStreamCaps", "QuerySupported"):
    assert re.search(r"CAndroCamPin::%s\(" % meth, cpp), "declared but not defined: " + meth
print("SHM/C TESTS PASSED")
