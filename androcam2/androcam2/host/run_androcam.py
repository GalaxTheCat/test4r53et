"""Entry point for the PyInstaller-built AndroCam.exe (windowed: no console, so log to a file)."""
import os
import sys
import traceback
from pathlib import Path


def _setup_logging() -> None:
    if sys.stderr is not None and sys.stdout is not None:
        return
    base = Path(os.environ.get("APPDATA") or Path.home()) / "AndroCam"
    base.mkdir(parents=True, exist_ok=True)
    log = open(base / "androcam.log", "a", buffering=1, encoding="utf-8")
    sys.stdout = sys.stderr = log


if __name__ == "__main__":
    _setup_logging()
    try:
        from androcam_host.__main__ import main
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        sys.exit(1)
