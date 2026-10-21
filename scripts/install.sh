#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$0")/env.sh"
cd "$VSR_ROOT"
for command in uv g++ ffmpeg; do
  command -v "$command" >/dev/null || { printf 'Missing host prerequisite: %s\n' "$command" >&2; exit 1; }
done
printf 'Installing NVIDIA runtime dependencies locally. See THIRD_PARTY.md for upstream licenses.\n'
uv python install 3.13
if [[ ! -x .venv/bin/python ]]; then uv venv --python 3.13 .venv; fi
uv pip install --python .venv/bin/python --extra-index-url https://pypi.nvidia.com --index-strategy unsafe-best-match -r requirements.lock
.venv/bin/python scripts/build_nvenc.py
./vsr configure
./scripts/make-demo.sh
printf 'Installed locally. Run ./vsr probe, then ./vsr browser\n'
