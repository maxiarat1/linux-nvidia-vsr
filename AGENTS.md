# Working on NVIDIA VSR for Linux browsers

This project uses NVIDIA `nvvfx.VideoSuperRes`. Do not substitute another upscaler, silently fall back to bicubic, or call the Windows browser driver toggle a Linux feature.

Read `docs/architecture.md`, `docs/performance.md`, `docs/api.md` and `CONTRIBUTING.md` before changing transport, timing, decoding or extension startup. These record investigated failures so they need not be rediscovered.

## Invariants

- Keep dependencies, SDK downloads, caches, credentials, generated media and test profiles inside this repository's ignored directories. Source `scripts/env.sh` using Bash for runtime commands.
- Never commit `.state/token`, `extension/config.js`, generated workers, browser profiles, cookies, proprietary wheels, models or downloaded SDK source. `manifest.template.json` is the editable source manifest. Hash changes must change both the generated worker URL and local four-part extension version; unchanged versions left a persistent profile with a disallowed stale worker. Runtime engine startup must not regenerate extension assets.
- Preserve browser-native on-demand startup and idle GPU cleanup. Do not turn popup health polling into a GPU start or idle keepalive. Native messages accept fixed status/ensure commands, never arbitrary executables or frame data.
- Bind the service to loopback. Preserve authentication, trusted-host/origin checks, per-tab expiring grants and the extension-owned bridge. Never pass the service token to page-world JavaScript or dashboard markup.
- The normal pipeline is NV12 host staging → CUDA VSR → zero-extra-delay NVENC AV1 → extension worker GPU VideoDecoder → OffscreenCanvas. Host staging is real; do not advertise zero-copy.
- Keep GPU work serialized, capture outstanding requests bounded, decoded-picture memory bounded, and source timestamps/seek epochs checked. Preserve original playback on errors.
- Do not use expectedDisplayTime as the audio-input clock. See the documented A/V compensation bug. Device latency initially reported as zero is unknown, not a measurement.
- Validate composited output, not only GPU/frame counters. The stable player sibling overlay must be above the native video stacking branch (YouTube uses z-index 10) and below player controls. Run tests/visible_output.py for overlay changes. LIVE requires fresh visible frames, not merely a connected engine.
- Keep decode/render in the extension worker and its own visible canvas. Returning GPU frames through the site's agent cluster caused expensive synchronization; hidden-frame rAF and cross-origin canvas ownership failed in tests.
- No arbitrary 1080p/4K caps. Preserve actual NVENC capability checks, memory checks and the 64 MiB transport guard. Auto resolution is not an fps controller.
- Only the project-owned isolated profile may be closed automatically. Never edit or restart a user's regular browser without their authorization. Do not delete cached workers that another profile may still use.

Run checks appropriate to the change. Unit/CI checks have no GPU coverage; runtime claims need actual hardware decoding/output evidence. Avoid shipping benchmark outputs with private data. No sub-agent delegation is required by this file.
