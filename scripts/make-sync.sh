#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "$0")/env.sh"
# Matching full-frame flashes and quiet 1 kHz pulses at every integer second.
ffmpeg -hide_banner -loglevel error \
  -f lavfi -i "testsrc2=size=1280x720:rate=60,drawbox=color=white:t=fill:enable='lt(mod(t,1),0.05)'" \
  -f lavfi -i 'aevalsrc=if(lt(mod(t\,1)\,0.008)\,0.15*sin(2*PI*1000*t)\,0):s=48000' \
  -t 60 -c:v libx264 -preset fast -crf 24 -pix_fmt yuv420p \
  -c:a aac -b:a 192k -movflags +faststart -y "$VSR_ROOT/artifacts/sync.mp4"
