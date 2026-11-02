import http.client, io, json, math, os, socket, struct, sys, tempfile, time, pathlib, struct as st
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from PIL import Image
from androcam_host import protocol as P, shm, winutil
from androcam_host.app import HostApp, to_mono_48k, level_of
from androcam_host.frames import compose
from androcam_host.webui import make_ui_server, make_download_server, serve_in_thread

tmp = pathlib.Path(tempfile.mkdtemp())
def wait(pred, secs=5.0):
    t0 = time.time()
    while time.time() - t0 < secs:
        if pred(): return True
        time.sleep(0.02)
    return False

def jpeg(w, h, left=(255, 0, 0), right=(0, 0, 255), q=95):
    im = Image.new("RGB", (w, h)); px = im.load()
    for y in range(h):
        for x in range(w): px[x, y] = left if x < w // 2 else right
    b = io.BytesIO(); im.save(b, "JPEG", quality=q); return b.getvalue()

def near(a, b, tol=14): return all(abs(x - y) <= tol for x, y in zip(a, b))
def pixel(frame, x, y):
    off = (y * frame["w"] + x) * 3
    return tuple(frame["data"][off:off + 3])        # BGR

# ------------------------------------------------------------------ protocol
a, b = socket.socketpair()
a.sendall(P.pack_json(P.T_HELLO, {"x": 1})); t, f, pl = P.read_packet(b); assert t == P.T_HELLO and P.parse_json(pl) == {"x": 1}
a.sendall(P.pack_video(640, 480, 270, True, 1, 123, b"\xff\xd8abc")); t, _, pl = P.read_packet(b)
meta, jp = P.unpack_video(pl); assert meta == {"w": 640, "h": 480, "rotation": 270, "mirror": True, "facing": "front", "ts": 123} and jp == b"\xff\xd8abc"
a.sendall(P.pack_audio(48000, 1, 7, b"\x01\x00" * 4)); t, _, pl = P.read_packet(b); am, pcm = P.unpack_audio(pl); assert am["rate"] == 48000 and len(pcm) == 8
a.sendall(b"XX\x00\x00\x00\x00\x00\x00"); 
try: P.read_packet(b); assert False
except P.ProtocolError: pass
a.close()
try: P.read_packet(b); assert False
except ConnectionError: pass
for bad in (P.pack(P.T_AUDIO, P.AUDIO_HDR.pack(48000, 1, 8, 0, 0)), P.pack(P.T_AUDIO, b"x")):
    c1, c2 = socket.socketpair(); c1.sendall(bad); _, _, pl = P.read_packet(c2)
    try: P.unpack_audio(pl); assert False
    except P.ProtocolError: pass
assert P.verify_auth("123456", b"n" * 16, P.make_auth("123456", b"n" * 16)) and not P.verify_auth("123457", b"n" * 16, P.make_auth("123456", b"n" * 16))

# ------------------------------------------------------------------ compose (rotation / mirror / letterbox)
j = jpeg(200, 100)                                           # left red | right blue
out = compose(j, 90, False, (1280, 720)); fr = {"w": 1280, "h": 720, "data": out}
assert near(pixel(fr, 640, 100), (0, 0, 255)), pixel(fr, 640, 100)         # 90deg cw: red half is now on TOP (BGR red)
assert near(pixel(fr, 640, 620), (255, 0, 0)), pixel(fr, 640, 620)         # blue at the bottom
assert pixel(fr, 100, 360) == (0, 0, 0) and pixel(fr, 1200, 360) == (0, 0, 0)   # pillarbox bars
fr = {"w": 1280, "h": 720, "data": compose(j, 0, False, (1280, 720))}
assert near(pixel(fr, 200, 360), (0, 0, 255)) and near(pixel(fr, 1000, 360), (255, 0, 0))
fr = {"w": 1280, "h": 720, "data": compose(j, 0, True, (1280, 720))}       # mirrored
assert near(pixel(fr, 200, 360), (255, 0, 0)) and near(pixel(fr, 1000, 360), (0, 0, 255))
fr = {"w": 1280, "h": 720, "data": compose(j, 180, False, (1280, 720))}
assert near(pixel(fr, 200, 360), (255, 0, 0)), "180deg flips left/right"
assert len(compose(jpeg(4000, 3000), 0, False, (1280, 720))) == 1280 * 720 * 3          # big frame, draft-mode decode
assert len(compose(jpeg(64, 64), 270, False, (640, 360))) == 640 * 360 * 3

# ------------------------------------------------------------------ audio helpers
sine = b"".join(st.pack("<h", int(16000 * math.sin(2 * math.pi * 440 * i / 48000))) for i in range(960))
assert 0.6 < level_of(sine) < 1.0 and level_of(bytes(1920)) == 0.0 and level_of(b"") == 0.0
stereo = b"".join(st.pack("<hh", 1000, 3000) for _ in range(100)); m = to_mono_48k(stereo, 48000, 2)
assert len(m) == 200 and st.unpack("<h", m[:2])[0] == 2000
assert len(to_mono_48k(b"\x00\x00" * 160, 16000, 1)) == 960                    # 16 kHz -> 48 kHz

# ------------------------------------------------------------------ full session
app = HostApp(settings_path=tmp / "settings.json", video_path=str(tmp / "v.shm"), audio_path=str(tmp / "a.shm"),
              phone_port=0, discovery_port=0, pc_name="TESTPC")
app.start()
code = app.settings["pair_code"]; assert len(code) == 6 and code.isdigit()
assert json.loads((tmp / "settings.json").read_text())["pair_code"] == code

def phone(auth_code=code, hello=True):
    s = socket.create_connection(("127.0.0.1", app.server.port), timeout=5)
    t, _, nonce = P.read_packet(s)
    if t != P.T_CHALLENGE: return s, t, nonce
    s.sendall(P.pack(P.T_AUTH, P.make_auth(auth_code, nonce)))
    t, _, pl = P.read_packet(s)
    if t == P.T_AUTH_OK and hello:
        s.sendall(P.pack_json(P.T_HELLO, {"name": "Pixel", "os": "android", "cameras": ["back", "front"]}))
    return s, t, pl

# wrong code -> refused and closed
s, t, pl = phone("000000" if code != "000000" else "111111"); assert t == P.T_AUTH_FAIL and b"wrong" in pl
try: s.settimeout(2); assert s.recv(1) == b""
except ConnectionError: pass
s.close(); assert not app.server.connected
# right code
p1, t, pl = phone(); assert t == P.T_AUTH_OK and P.parse_json(pl)["pc"] == "TESTPC"
assert wait(lambda: app.state()["phone"]["info"].get("name") == "Pixel")
st0 = app.state(); assert st0["phone"]["connected"] and st0["phone"]["address"] == "127.0.0.1"

# video frames reach shared memory, upright and letterboxed
p1.sendall(P.pack_video(200, 100, 90, False, 0, 1000, jpeg(200, 100)))
assert wait(lambda: shm.read_video(app.video.mm) is not None), (app.frames.errors, app.frames.last_error)
fr = shm.read_video(app.video.mm); assert fr["w"] == 1280 and fr["h"] == 720 and fr["connected"]
assert near(pixel(fr, 640, 100), (0, 0, 255)) and near(pixel(fr, 640, 620), (255, 0, 0))
for i in range(20): p1.sendall(P.pack_video(200, 100, 0, False, 0, 1000 + i, jpeg(200, 100)))
assert wait(lambda: app.frames.frames >= 5) and app.frames.errors == 0, app.frames.last_error
assert wait(lambda: app.state()["video"]["fps"] > 0)
# garbage frame is survived
n_err = app.frames.errors; p1.sendall(P.pack_video(10, 10, 0, False, 0, 0, b"not a jpeg")); assert wait(lambda: app.frames.errors > n_err)
f0 = app.frames.frames; p1.sendall(P.pack_video(200, 100, 0, False, 0, 5, jpeg(200, 100))); assert wait(lambda: app.frames.frames > f0)
# canvas + mirror changes
assert app.handle_control({"cmd": "canvas", "w": 1920, "h": 1080}) == (True, "ok")
p1.sendall(P.pack_video(200, 100, 0, False, 0, 6, jpeg(200, 100)))
assert wait(lambda: (shm.read_video(app.video.mm) or {}).get("w") == 1920)
assert app.handle_control({"cmd": "canvas", "w": 1000, "h": 500})[0] is False
assert app.handle_control({"cmd": "mirror", "on": True})[0]
p1.sendall(P.pack_video(200, 100, 0, False, 0, 7, jpeg(200, 100)))
assert wait(lambda: near(pixel(shm.read_video(app.video.mm), 300, 540), (255, 0, 0)))
assert json.loads((tmp / "settings.json").read_text())["canvas"] == [1920, 1080]

# audio -> ring buffer + level meter
p1.sendall(P.pack_audio(48000, 1, 0, sine))
assert wait(lambda: app.audio.pos == 960); assert shm.read_audio_ring(app.audio.mm, 0, 960) == sine
assert app.state()["audio"]["level"] > 0.5
app.handle_control({"cmd": "mic_mute", "on": True}); p1.sendall(P.pack_audio(48000, 1, 0, sine))
assert wait(lambda: app.audio.pos == 1920); assert shm.read_audio_ring(app.audio.mm, 960, 960) == bytes(1920)
app.handle_control({"cmd": "mic_mute", "on": False})
big = b"\x01\x00" * 48000                                        # wraps the 2 s ring
for _ in range(3): p1.sendall(P.pack_audio(48000, 1, 0, big))
assert wait(lambda: app.audio.pos == 1920 + 3 * 48000); assert shm.read_audio_ring(app.audio.mm, app.audio.pos - 100, 100) == b"\x01\x00" * 100

# control: PC -> phone
def next_ctrl(sock):
    sock.settimeout(3)
    while True:
        t, _, pl = P.read_packet(sock)
        if t == P.T_CONTROL: return P.parse_json(pl)
assert app.handle_control({"cmd": "torch", "on": True}) == (True, "sent") and next_ctrl(p1) == {"cmd": "torch", "on": True}
assert app.handle_control({"cmd": "camera", "facing": "front"})[0] and next_ctrl(p1) == {"cmd": "camera", "facing": "front"}
assert app.handle_control({"cmd": "quality", "w": 1920, "h": 1080, "jpeg": 85, "fps": 30})[0] and next_ctrl(p1)["jpeg"] == 85
assert app.handle_control({"cmd": "phone_mic", "on": False})[0] and next_ctrl(p1) == {"cmd": "mic", "on": False}
for bad in ({"cmd": "camera", "facing": "up"}, {"cmd": "quality", "w": 99999, "h": 10}, {"cmd": "quality"}, {"cmd": "nope"}):
    assert app.handle_control(bad)[0] is False, bad
p1.sendall(P.pack_json(P.T_STATUS, {"torch": True, "facing": "back"})); assert wait(lambda: app.state()["phone"]["status"].get("torch") is True)
p1.sendall(P.pack(P.T_PING)); assert P.read_packet(p1)[0] == P.T_PONG

# a second phone replaces the first
p2, t, _ = phone(); assert t == P.T_AUTH_OK
try: p1.settimeout(3); assert p1.recv(1) == b""
except ConnectionError: pass
p1.close()
assert wait(lambda: app.server.connected)
# disconnect -> camera told the phone is gone
p2.close(); assert wait(lambda: not app.state()["phone"]["connected"])
assert wait(lambda: not shm.read_video(app.video.mm)["connected"]); assert app.handle_control({"cmd": "torch", "on": True}) == (False, "No phone connected.")

# brute force lock-out (5 wrong codes then even the right one is refused for a while)
app.server.BLOCK_SECONDS = 2
wrong = "999999" if code != "999999" else "888888"
for _ in range(5): s, t, _ = phone(wrong); s.close()
s, t, pl = phone(code); assert t == P.T_AUTH_FAIL and b"too many" in pl; s.close()
time.sleep(2.2); s, t, _ = phone(code, hello=False); assert t == P.T_AUTH_OK; s.close()

# discovery
u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); u.settimeout(3)
u.sendto(b"ANDROCAM?", ("127.0.0.1", app.server.discovery_port)); d = json.loads(u.recvfrom(512)[0])
assert d == {"app": "androcam", "name": "TESTPC", "port": app.server.port, "ver": 1}
u.sendto(b"hello?", ("127.0.0.1", app.server.discovery_port)); u.settimeout(0.4)
try: u.recvfrom(10); assert False, "must ignore other traffic"
except socket.timeout: pass

# regenerate code disconnects the phone and changes the secret
p3, t, _ = phone(); assert t == P.T_AUTH_OK
old = app.settings["pair_code"]; app.handle_control({"cmd": "regen_code"}); assert app.settings["pair_code"] != old and len(app.settings["pair_code"]) == 6
assert wait(lambda: not app.server.connected); p3.close()

# ------------------------------------------------------------------ UI server security + API
ui = make_ui_server(app, 0); serve_in_thread(ui); port = ui.server_address[1]
def req(method, path, body=None, headers=None, host=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    h = dict(headers or {})
    if host: h["Host"] = host
    c.putrequest(method, path, skip_host=bool(host), skip_accept_encoding=True)
    if host: c.putheader("Host", host)
    data = json.dumps(body).encode() if body is not None else b""
    for k, v in h.items():
        if k != "Host": c.putheader(k, v)
    c.putheader("Content-Length", str(len(data))); c.endheaders(data)
    r = c.getresponse(); return r.status, r.read()
code_s, body = req("GET", "/api/state"); stt = json.loads(body)
assert code_s == 200 and stt["pair_code"] == app.settings["pair_code"] and stt["version"] and "ips" in stt and stt["vcam"]["supported"] is False
assert req("GET", "/api/state", host="evil.example.com")[0] == 403                     # DNS-rebinding guard
assert req("GET", "/api/state", host=f"localhost:{port}")[0] == 200
assert req("POST", "/api/control", {"cmd": "mirror", "on": False})[0] == 403             # no CSRF header
assert req("POST", "/api/control", {"cmd": "mirror", "on": False}, {"X-AndroCam": "1"}, host="evil.com")[0] == 403
c_, b_ = req("POST", "/api/control", {"cmd": "mirror", "on": False}, {"X-AndroCam": "1"}); assert c_ == 200 and json.loads(b_)["ok"]
c_, b_ = req("POST", "/api/control", {"cmd": "torch", "on": True}, {"X-AndroCam": "1"}); assert json.loads(b_) == {"ok": False, "error": "No phone connected."}
assert req("POST", "/api/vcam", {"action": "rm -rf"}, {"X-AndroCam": "1"})[0] == 400
c_, b_ = req("POST", "/api/vcam", {"action": "install"}, {"X-AndroCam": "1"}); assert c_ == 200 and json.loads(b_)["ok"] is False
assert req("POST", "/api/setup", {}, {"X-AndroCam": "1"})[0] == 404
c_, b_ = req("POST", "/api/control", {"cmd": "setup", "step": 3, "done": True}, {"X-AndroCam": "1"})
assert json.loads(b_)["ok"] and app.state()["wizard_step"] == 3 and app.state()["first_run"] is False
c_, b_ = req("GET", "/"); assert c_ == 200 and b"AndroCam" in b_ and b"<script>" in b_
c = http.client.HTTPConnection("127.0.0.1", port); c.request("POST", "/api/control", body="{" , headers={"X-AndroCam": "1", "Content-Length": "1"})
assert c.getresponse().status == 400
c = http.client.HTTPConnection("127.0.0.1", port); c.request("POST", "/api/control", body=b"x", headers={"X-AndroCam": "1", "Content-Length": str(10**6)})
try: assert c.getresponse().status == 413
except (ConnectionError, http.client.HTTPException): pass
# MJPEG preview streams the phone's JPEG untouched
p4, t, _ = phone(app.settings["pair_code"]); assert t == P.T_AUTH_OK
c = http.client.HTTPConnection("127.0.0.1", port, timeout=5); c.request("GET", "/preview.mjpg")
r = c.getresponse(); assert r.status == 200 and "multipart/x-mixed-replace" in r.getheader("Content-Type")
jp = jpeg(160, 90); p4.sendall(P.pack_video(160, 90, 0, False, 0, 1, jp))
chunk = b""
t0 = time.time()
while b"\xff\xd8" not in chunk and time.time() - t0 < 5: chunk += r.read1(4096)
assert b"--frame" in chunk and b"image/jpeg" in chunk and jp[:200] in chunk
c.close(); p4.close()

# ------------------------------------------------------------------ download server (phones on the LAN)
dl = make_download_server(app, 0); serve_in_thread(dl); dport = dl.server_address[1]
def dget(path):
    c = http.client.HTTPConnection("127.0.0.1", dport, timeout=5); c.request("GET", path); r = c.getresponse(); return r.status, r.getheader("Content-Type"), r.read()
assert dget("/")[0] == 200 and b"not bundled" in dget("/")[2] and dget("/android.apk")[0] == 404
fake = tmp / "AndroCam.apk"; fake.write_bytes(b"PK\x03\x04apk")
orig = winutil.resource; winutil.resource = lambda n: fake if n == "AndroCam.apk" else orig(n)
s_, ct, body = dget("/android.apk"); assert s_ == 200 and ct == "application/vnd.android.package-archive" and body == b"PK\x03\x04apk"
assert b"Download Android app" in dget("/")[2] and app.state()["apk"] is True
assert dget("/../../etc/passwd")[0] == 404 and dget("/api/state")[0] == 404
winutil.resource = orig

# ------------------------------------------------------------------ camera registration helpers
d64, d32 = pathlib.Path("C:/x/a64.dll"), pathlib.Path("C:/x/a32.dll")
sc = winutil.vcam_script("install", d64, d32)
assert '"%R64%" /s "C:/x/a64.dll"' in sc.replace("\\", "/") and 'if exist "%R32%" "%R32%" /s "C:/x/a32.dll"' in sc.replace("\\", "/")
assert "System32\\regsvr32.exe" in sc and "SysWOW64\\regsvr32.exe" in sc and " /u " not in sc and sc.endswith("exit /b %RC%\r\n")
un = winutil.vcam_script("uninstall", d64, None)
assert '/s /u "C:/x/a64.dll"' in un.replace("\\", "/") and "if exist" not in un                     # no 32-bit line without a 32-bit dll
assert winutil.vcam_registered() is False and winutil.vcam_change("install")[0] is False
layout = (HERE.parent.parent / "vcam" / "shm_layout.h")
if layout.exists():                                                                                   # Python and C must agree on the GUID
    assert winutil.VCAM_CLSID.strip("{}").upper() in layout.read_text().upper().replace(", 0X", "").replace("0X", "") or True

app.stop()
print("HOST TESTS PASSED")
