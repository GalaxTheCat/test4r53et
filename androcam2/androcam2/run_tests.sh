#!/bin/sh
set -e
cd "$(dirname "$0")"
python3 host/tests/test_host.py
python3 host/tests/test_shm_c.py
python3 host/tests/test_mobile_wire.py
node host/tests/test_ui.js
