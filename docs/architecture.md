# Architecture and investigated failures

The backend uses `nvvfx.VideoSuperRes` from the NVIDIA VFX Python wheel. Quality levels are actual SDK AI modes. NVIDIA bicubic is used only as a probe reference. There is no model replacement or claimed implementation of the Windows driver switch.

## Components and data path

1. A content script picks the largest visible top-level video. `VideoFrame.copyTo` stages decoded NV12 pixels into a bounded host buffer. Non-NV12 sources explicitly use the diagnostic JPEG capture fallback.
2. An extension-owned iframe claims a 15-second, single-use grant bound to its tab. Only that bridge receives the local token. Transferable data crosses a MessageChannel; video payloads do not use chrome.runtime messaging.
3. The bridge's worker sends frames over an authenticated loopback WebSocket. The backend serializes GPU access with one thread and lock, rejecting busy work instead of growing an unbounded GPU queue.
4. CuPy converts NV12 to contiguous float RGB on CUDA. VideoSuperRes loads the requested dimensions/quality; its DLPack result is copied before the next SDK invocation reuses memory.
5. CUDA converts the output to BT.709 limited-range NV12. `native/nvenc.cpp` wraps the NVIDIA helper encoder with zero extra output delay, no B frames/lookahead, preset P1, QP 14, and independent AV1 keyframes with repeated sequence headers.
6. An extension worker decodes the actual AV1 codec/profile/level using WebCodecs with hardware preference, queues timestamped pictures, then draws a GPU VideoFrame to an OffscreenCanvas at its matching audible time. Tests inspect actual Media events for hardware decoder evidence.

The default path removes CPU JPEG encode/decode from normal NV12 video processing. It still stages raw input through CPU-accessible memory and copies compressed packets through WebSockets. UI, IPC and metadata use CPU resources. It is not a native zero-copy browser integration.

## Timing: findings worth preserving

### The roughly 50 ms encoder delay

The tested PyNvVideoCodec 2.2.3 helper wrapper hardcodes three additional queued frames. At 60 fps this contributes roughly 50 ms before output appears. Changing video quality or selecting an audio codec cannot remove that queue. The production C ABI adapter configures zero extra delay and requires exactly one packet returned for each submitted frame. `scripts/probe_nvenc.py` retains the investigation probe; `scripts/build_nvenc.py` pins and hashes the source archive.

### Average fps did not explain uneven motion

A pipeline can average 60 fps while displaying pictures in bunches. The earlier main-thread GPU-frame/ImageBitmap handoff coupled drawing to busy site JavaScript and expensive cross-agent synchronization. Decode and draw now remain in one extension worker, attached to the extension's own canvas.

A canvas transferred from the page into a worker in a different agent cluster did not receive the expected worker animation ticks in the tested browser. A hidden iframe also stopped animation ticks. The current overlay is a visible, pointer-transparent extension frame owning its OffscreenCanvas. The frame is anchored to a stable player ancestor, because moving an iframe reloads its document and invalidates its transferred port. A reload reconnects using a fresh authorization grant and channel.

### Which clock to use

`expectedDisplayTime` includes the native player's A/V compensation. Using it as the audio-input clock counted that compensation again and failed the flash/beep check. `presentation-clock.js` reconstructs the media-input timeline from `video.currentTime` sampled during frame callbacks. Median filtering removes timing-read noise; limited drift correction avoids moving frame deadlines abruptly.

Audio-device base/output latency is filtered separately. A reported zero output latency during startup means the estimate is unavailable; initializing the filter with that zero created a slowly corrected bias and unnecessary audio delay. The first valid estimate initializes immediately. Pictures wait for their matching audible timestamp; a smoothed DelayNode adds latency only if processing needs more time than the existing device path provides. No fixed 50 ms delay is imposed.

The queue is bounded to 32 decoded pictures / 1 GiB, independently of the four processing slots. Seek epochs and source timestamps discard old results. Pause, waiting, seeking, visibility and rate changes flush presentation state. Refreshing a page is required to fully remove its MediaElementAudioSourceNode; off mode only removes the extra DelayNode delay and overlay.

### Cold CUDA initialization

Querying NVENC limits before a current CUDA context existed failed on a cold process, even when later requests succeeded after another diagnostic warmed CUDA. `gpu_video.py` initializes the runtime device before querying the current driver context, with a one-time primary-context fallback. Preserve the cold-first-request check.

### Extension background cache failure

Helium retained an older unpacked extension service worker after a version update. The page content was new but the background lacked the expected runtime listener: `Could not establish connection. Receiving end does not exist.` A manifest version bump alone did not resolve it.

`./vsr configure` now generates a private standalone worker with an asset/content hash in its URL and inlines config. Its build identity covers manifest, JS, HTML and CSS. The source manifest is `manifest.template.json`; installed `manifest.json`, `config.js` and generated workers are ignored. Old workers are kept because another regular profile might still need them. Close all relevant browsers before pruning private generated files. Reload extension and page after updating a regular profile.

## Auto resolution and real limits

`resolution.js` computes a scale to fit the target's landscape or portrait bounds, preserving aspect ratio to the SDK's eight-pixel output alignment. A source above target bypasses enhancement; an equal-sized source uses 1×. The API accepts finite scales ≥1. Auto 360p → 4K (6×) worked in the tested runtime, although upstream documentation describes standard upscaling through 4×; do not promise every factor on every SDK/GPU.

No arbitrary 1080p input or 4K output cap is imposed. NVENC dimensions are queried on the active GPU (8192×8192 on the tested RTX 5090), input packets are bounded to 64 MiB, and minimum visible GPU-buffer memory is checked. Actual AI workspace/driver allocation can still fail. Large outputs reduce achievable fps.

## Security and UI

The API binds to 127.0.0.1, validates hosts, authenticates frame requests and checks browser WebSocket origins. The random local token is stored mode 0600. A malicious page cannot read the token from page-world JS; the extension bridge verifies a one-use tab grant. The worker and loopback service process local frames, with no remote upload or telemetry from project code.

The toolbar dashboard is an extension page. It requests sanitized per-tab state/settings through the background, and reads health without receiving the token. DOM values use textContent, not HTML interpolation. Injected content is opt-in on arbitrary pages using activeTab; YouTube and the local fixture have declared content scripts. The dashboard does not start external executables: start the local service with `./vsr start`.

Trust is local-user trust, not isolation against another process running as the same user. Do not expose the API to a LAN or deploy it as an unauthenticated server.

## References

- [NVIDIA VFX Python API](https://docs.nvidia.com/maxine/vfx-python/latest/api.html)
- [NVIDIA Python encoder API](https://docs.nvidia.com/video-technologies/pynvvideocodec/pynvc-api-reference/encoder.html)
- [WebCodecs](https://www.w3.org/TR/webcodecs/)
- [Web Audio](https://webaudio.github.io/web-audio-api/)
