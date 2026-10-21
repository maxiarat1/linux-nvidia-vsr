#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$0")/env.sh"
if [[ "${1:-}" == "--720p60" ]]; then
  ffmpeg -hide_banner -loglevel error -f lavfi -i 'testsrc2=size=1280x720:rate=60' -t 60 \
    -c:v libx264 -preset fast -crf 28 -pix_fmt yuv420p -movflags +faststart -y "$VSR_ROOT/artifacts/demo-720p60.mp4"
  exit 0
fi
ffmpeg -hide_banner -loglevel error -f lavfi -i 'testsrc2=size=640x360:rate=30' -t 20 \
  -c:v libx264 -preset fast -crf 26 -pix_fmt yuv420p -movflags +faststart -y "$VSR_ROOT/artifacts/demo.mp4"
