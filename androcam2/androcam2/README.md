# AndroCam 2 (alpha): your phone as a webcam + microphone, no OBS, no preview window

Phone app (Android / iPhone) -> PC over Wi-Fi or USB tethering -> **AndroCam Virtual Camera**, a real webcam
device that Zoom, Teams, Discord, Chrome, OBS... can pick. Everything is in this repo; nothing third-party to install.

## Get it (everything is built in the cloud; nothing to install locally)
1. Put this folder in a GitHub repository (Upload files, or `git push`).
2. **Actions -> build -> Run workflow.** It runs the tests, then builds three things:
   * `AndroCam-Windows-Setup`: the Windows installer (app + virtual camera + the Android app, bundled)
   * `AndroCam-Android`: `AndroCam.apk`
   * `AndroCam-iOS-unsigned`: the iPhone app (needs a Mac + Apple ID to install, see `docs/IOS.md`)
3. Run **AndroCam-Setup.exe** on the PC. The first-run guide walks you through the rest.

## What the guide does
Welcome -> install the phone app (the PC hosts the APK at `http://<pc-ip>:47802`) -> connect with a 6-digit code ->
camera check (preview, flash, switch lens) -> virtual webcam -> microphone check -> done.
Closing the window leaves AndroCam running quietly in the background; no window is needed for the webcam.

## Honest status
| Part | State |
|---|---|
| Protocol, pairing, frame pipeline, shared memory, local UI server | **Tested** here with a simulated phone over real sockets |
| Virtual-camera frame renderer (`vcam/shm_layout.h`) | **Compiled and verified** pixel-for-pixel against the Python writer |
| UI page logic | Tested in Node against a stub DOM; **never rendered in a real browser** |
| `vcam/vcam.cpp` (DirectShow camera) | **Never compiled** (no Windows toolchain where it was written); CI is its first build |
| Android app | **Never compiled or run**; static checks only |
| iPhone app | **Never compiled or run**; static checks only |
| Installer / CI workflow | **Never run** |
| **Microphone driver** | **Not written yet.** See `docs/DRIVER_PLAN.md`. Phone mic audio already reaches the PC and a level meter shows it |

Expect the first CI run to need a round or two of compile fixes; send me the failing log.

## Known limits
* Cameras seen by DirectShow apps (most desktop apps and browsers). Apps that only use Windows' newer Media Foundation camera stack may not list it.
* Phone and PC must be on the same network (or USB tethering). The installer opens the firewall for **Private** networks only.
* iPhone: manual PC address (iOS restricts network discovery); the app must stay in the foreground.
* The pairing code is the only protection and the video is not encrypted on your LAN: use trusted networks.

## Repo map
`host/` Windows app (Python) + tests · `vcam/` virtual camera (C++) · `android/` · `ios/` · `windows/` installer · `docs/`
Tests: `python host/tests/test_host.py && python host/tests/test_shm_c.py && python host/tests/test_mobile_wire.py && node host/tests/test_ui.js`
