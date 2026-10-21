#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$0")/env.sh"
cd "$VSR_ROOT"
exec .venv/bin/python -m uvicorn vsr_service.app:app --host 127.0.0.1 --port 8730 --ws-max-size 67108864 --ws-per-message-deflate false --no-access-log
