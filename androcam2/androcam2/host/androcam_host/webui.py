"""Local UI server (127.0.0.1) and the tiny LAN download server for the Android app."""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

from . import protocol as P
from . import winutil

WEB_DIR = Path(__file__).resolve().parent / "web"
MAX_BODY = 64 * 1024


class _Quiet(BaseHTTPRequestHandler):
    def log_message(self, *a):          # no console noise
        pass

    def _send(self, code: int, body: bytes, ctype: str, extra: Optional[dict] = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json")


def make_ui_server(app, port: int = P.PORT_UI) -> ThreadingHTTPServer:
    class Handler(_Quiet):
        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").lower()
            return host in (f"127.0.0.1:{srv.server_address[1]}", f"localhost:{srv.server_address[1]}")

        def do_GET(self):
            if not self._host_ok():                       # blocks DNS-rebinding from web pages
                return self._send(403, b"forbidden", "text/plain")
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                return self._send(200, (WEB_DIR / "index.html").read_bytes(), "text/html; charset=utf-8")
            if path == "/api/state":
                return self._json(app.state())
            if path == "/preview.mjpg":
                return self._preview()
            self._send(404, b"not found", "text/plain")

        def do_POST(self):
            # Browsers cannot add a custom header cross-site without a CORS preflight (which we refuse),
            # so this stops any web page from driving the app.
            if not self._host_ok() or self.headers.get("X-AndroCam") != "1":
                return self._send(403, b"forbidden", "text/plain")
            try:
                n = int(self.headers.get("Content-Length") or 0)
                if n > MAX_BODY:
                    return self._send(413, b"too large", "text/plain")
                req = json.loads(self.rfile.read(n) or b"{}")
                if not isinstance(req, dict):
                    raise ValueError
            except (ValueError, OSError):
                return self._json({"ok": False, "error": "bad request"}, 400)
            path = self.path.split("?")[0]
            if path == "/api/control":
                ok, msg = app.handle_control(req)
                return self._json({"ok": ok, "error": None if ok else msg})
            if path == "/api/vcam":
                if req.get("action") not in ("install", "uninstall"):
                    return self._json({"ok": False, "error": "bad action"}, 400)
                ok, msg = winutil.vcam_change(req["action"])
                return self._json({"ok": ok, "message": msg})
            if path == "/api/quit":
                self._json({"ok": True})
                app.quit_event.set()
                return
            self._send(404, b"not found", "text/plain")

        def _preview(self):
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            last = -1
            try:
                while not app.stopping:
                    seq, jpeg = app.frames.latest_seq, app.frames.latest_jpeg
                    if jpeg is not None and seq != last:
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                         + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n")
                        self.wfile.flush()
                        last = seq
                        time.sleep(0.06)                  # ~16 fps is plenty for a preview
                    else:
                        time.sleep(0.03)
            except (OSError, ValueError):
                pass

    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    srv.daemon_threads = True
    return srv


LANDING = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>AndroCam</title><body style="font-family:system-ui;background:#0f1115;color:#eee;text-align:center;padding:40px 20px">
<h1>AndroCam</h1><p>Install the phone app, open it, and it will find this PC.</p>
{button}<p style="opacity:.6;margin-top:40px">After downloading, tap the file and allow "Install unknown apps" for your browser if asked.</p>"""


def make_download_server(app, port: int = P.PORT_DOWNLOAD) -> ThreadingHTTPServer:
    class Handler(_Quiet):
        def do_GET(self):
            path = self.path.split("?")[0]
            apk = winutil.resource("AndroCam.apk")
            if path == "/android.apk" and apk:
                return self._send(200, apk.read_bytes(), "application/vnd.android.package-archive",
                                  {"Content-Disposition": 'attachment; filename="AndroCam.apk"'})
            if path in ("/", "/index.html"):
                btn = ('<a href="/android.apk" style="display:inline-block;background:#3ddc84;color:#062;'
                       'padding:16px 28px;border-radius:12px;font-weight:700;text-decoration:none">'
                       'Download Android app</a>' if apk else "<p>The Android app is not bundled in this build.</p>")
                return self._send(200, LANDING.format(button=btn).encode(), "text/html; charset=utf-8")
            self._send(404, b"not found", "text/plain")

    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    srv.daemon_threads = True
    return srv


def serve_in_thread(srv: ThreadingHTTPServer) -> threading.Thread:
    t = threading.Thread(target=srv.serve_forever, daemon=True, name="http")
    t.start()
    return t
