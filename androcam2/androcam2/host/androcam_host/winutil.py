"""Windows-specific helpers. Everything degrades to a harmless no-op on other systems (and in tests)."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import webbrowser
from pathlib import Path
from typing import List, Optional, Tuple

IS_WINDOWS = sys.platform == "win32"
VCAM_CLSID = "{9F5F8B64-6B4C-4C93-8B8C-0A7C2F6D3A11}"     # must match vcam/shm_layout.h + vcam.cpp
VCAM_NAME = "AndroCam Virtual Camera"
AUTOSTART_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def resource(name: str) -> Optional[Path]:
    for base in (app_root() / "resources", app_root()):
        p = base / name
        if p.is_file():
            return p
    return None


def settings_path() -> Path:
    base = os.environ.get("APPDATA") if IS_WINDOWS else os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / (".config" if not IS_WINDOWS else "AppData/Roaming")
    return root / "AndroCam" / "settings.json"


# ------------------------------------------------------------------ virtual camera registration
def vcam_dlls() -> Tuple[Optional[Path], Optional[Path]]:
    return resource("androcam_vcam_x64.dll"), resource("androcam_vcam_x86.dll")


def vcam_registered() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        import winreg
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, f"CLSID\\{VCAM_CLSID}\\InprocServer32", 0,
                                    winreg.KEY_READ | view):
                    return True
            except OSError:
                continue
    except Exception:  # noqa: BLE001
        pass
    return False


def vcam_script(action: str, dll64: Optional[Path], dll32: Optional[Path]) -> str:
    """Batch file that (un)registers both bitness builds. Run elevated: one UAC prompt, no 3rd-party tool."""
    flag = "" if action == "install" else "/u "
    lines = ["@echo off", "setlocal", "set R64=%SystemRoot%\\System32\\regsvr32.exe",
             "set R32=%SystemRoot%\\SysWOW64\\regsvr32.exe", "set RC=0"]
    if dll64:
        lines.append(f'"%R64%" /s {flag}"{dll64}" || set RC=1')
    if dll32:
        lines.append(f'if exist "%R32%" "%R32%" /s {flag}"{dll32}" || set RC=1')
    lines.append("exit /b %RC%")
    return "\r\n".join(lines) + "\r\n"


def vcam_change(action: str) -> Tuple[bool, str]:
    """action: 'install' | 'uninstall'. Triggers the Windows UAC prompt."""
    d64, d32 = vcam_dlls()
    if not d64 and not d32:
        return False, "The virtual camera files are missing from this build."
    script = vcam_script(action, d64, d32)
    if not IS_WINDOWS:
        return False, "Virtual cameras are only supported on Windows."
    path = Path(tempfile.gettempdir()) / "androcam_vcam.cmd"
    path.write_text(script)
    import ctypes
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", str(path), None, None, 0)   # type: ignore[attr-defined]
    if rc <= 32:
        return False, "Administrator permission was not granted."
    return True, "Approve the Windows prompt, then wait a few seconds."


# ------------------------------------------------------------------ autostart
def autostart_get() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY) as k:
            winreg.QueryValueEx(k, "AndroCam")
            return True
    except OSError:
        return False


def autostart_set(on: bool) -> bool:
    if not IS_WINDOWS:
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if on:
                cmd = (f'"{sys.executable}" --background' if getattr(sys, "frozen", False)
                       else f'"{sys.executable}" -m androcam_host --background')
                winreg.SetValueEx(k, "AndroCam", 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(k, "AndroCam")
                except OSError:
                    pass
        return True
    except OSError:
        return False


# ------------------------------------------------------------------ UI window
def find_edge() -> Optional[str]:
    cands: List[str] = []
    for var in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        base = os.environ.get(var)
        if base:
            cands.append(str(Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"))
    return next((c for c in cands if os.path.isfile(c)), None)


def open_ui(url: str) -> None:
    """Edge (built into Windows) in app mode = a clean native-looking window; else the default browser."""
    edge = find_edge()
    if edge:
        try:
            subprocess.Popen([edge, f"--app={url}", "--window-size=1040,760"])
            return
        except OSError:
            pass
    webbrowser.open(url)
