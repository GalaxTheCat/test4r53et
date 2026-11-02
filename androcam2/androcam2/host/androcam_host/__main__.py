"""python -m androcam_host   (or AndroCam.exe)  [--background] [--no-ui]"""
import argparse
import json
import sys
import time
import urllib.request

from . import protocol as P
from . import winutil
from .app import HostApp
from .webui import make_download_server, make_ui_server, serve_in_thread


def _already_running(port: int) -> bool:
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/state")
        with urllib.request.urlopen(req, timeout=1.5) as r:
            return "pair_code" in json.loads(r.read())
    except Exception:  # noqa: BLE001
        return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="AndroCam")
    ap.add_argument("--background", action="store_true", help="start without opening the window")
    ap.add_argument("--no-ui", action="store_true")
    args = ap.parse_args(argv)
    url = f"http://127.0.0.1:{P.PORT_UI}"

    if _already_running(P.PORT_UI):               # second launch: just show the running instance's window
        if not args.background and not args.no_ui:
            winutil.open_ui(url)
        return 0                                   # (must happen BEFORE HostApp() resets shared memory)
    app = HostApp()
    try:
        ui = make_ui_server(app, P.PORT_UI)
    except OSError:
        app.stop()
        print("AndroCam: port %d is in use by another program." % P.PORT_UI, file=sys.stderr)
        return 1
    app.start()
    serve_in_thread(ui)
    try:
        serve_in_thread(make_download_server(app))
    except OSError:
        pass                                      # phone download page is optional
    if not args.background and not args.no_ui:
        winutil.open_ui(url)
    try:
        while not app.quit_event.wait(0.5):
            pass
    except KeyboardInterrupt:
        pass
    app.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
