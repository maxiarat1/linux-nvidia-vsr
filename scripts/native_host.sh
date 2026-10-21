#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$0")/env.sh"
cd "$VSR_ROOT"
exec .venv/bin/python -m scripts.native_host "$@"
