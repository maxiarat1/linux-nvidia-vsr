# Measured performance

These are short development measurements from an RTX 5090, driver 615.78.08, CachyOS/KDE Wayland, and Helium 0.19.4.1 / Chromium 155. They are evidence for this configuration, not expected results for every GPU/video. Full reports remain local because they contain machine/browser details. Reproduction commands are in [CONTRIBUTING.md](../CONTRIBUTING.md).

| Test                                                                        | Observed result                                                                                       |
| --------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| Auto 1440p60 → 4K High                                                      | 59.7 enhanced fps, 0 source drops, about 34 ms capture-through-return-decode processing               |
| Same scene 1080p → 4K, 8 ms site main-thread work every 100 ms, 10 s window | Earlier main-thread rendering: 59.7 fps, 19 gaps >25 ms; worker rendering: 59.9 fps, 2 gaps >25 ms    |
| That UI-load test, p95 absolute cadence error                               | 10.4 ms → 1.6 ms                                                                                      |
| Quiet same-window comparison                                                | Earlier path had 0 long gaps; worker path had 5. Quiet-scene improvement was not universal.           |
| Moving flash/beep fixture, 4K output                                        | About 60 fps; largest settled software A/V discrepancy 12.1 ms                                        |
| Same fixture with +100 ms controller-age stress                             | About 60 fps; about 76 ms actual additional audio delay; largest settled software discrepancy 12.3 ms |

An earlier 4K → 8K High experiment achieved about 24.8 enhanced fps and dropped source frames. The current renderer has changed since then; that number is a warning about processing cost, not a current benchmark or an artificial resolution cap.

## Visible-output validation added in 0.8.1

The earlier fps figures measure processing and worker canvas presentation throughput. A later layer-order bug was found: YouTube's native video could cover those successful canvas paints. Those earlier counters alone must not be used as evidence that enhanced pixels were visible. Version 0.8.1 adds composited screenshot tests and puts the overlay above the native video while retaining higher player controls.

A local 360p → 4K fixture measured 332 ms from enable to the first enhanced canvas frame with the service already running, about 50 ms steady capture-to-presentation age, and 0.5 ms to remove the overlay inside the stop handler. Browser messaging/physical display latency are not included in that stop-handler figure. A separate browser-native startup from a fully stopped engine measured 956 ms to the first frame. These are individual software timing checks, not universal startup guarantees. The helper's 30-second idle exit delay does not delay switching the picture off.

## What the numbers mean

Processing time includes capture, transfers, AI, encoding and return decoding. It excludes intentional picture waiting for the audio output clock. Service inference time is only one part of that processing path. Do not label capture-to-canvas age as AI inference time or interpret a two-frame presentation wait as an encoder stall without the component timings.

The sync fixture uses moving video with matched flashes/beeps, graph AnalyserNodes and AudioContext output timestamps. It compares submitted canvas timing to estimated audible time. It does **not** record a microphone, speaker output or physical display scanout. Device buffering, Bluetooth and incorrect browser latency estimates can alter real results.

Averages hide jitter. The cadence harness records actual presented source timestamps, presentation intervals, skipped source timestamps, long gaps and source drops. Decode verification observes Media agents in the page, extension iframe and worker so that a software fallback cannot pass merely because `hardwareAcceleration: prefer-hardware` was requested.

Two displays were present: 2560×1440 at approximately 240 Hz and 144 Hz. A 60 fps video fits the former refresh cadence evenly and the latter unevenly. Keep the benchmark window on the same display, compare the same source scene after warm-up, and record background load. This project performs upscaling, not motion interpolation.
