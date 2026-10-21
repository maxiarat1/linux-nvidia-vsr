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

### A painted frame can still be hidden under the original

The stable YouTube player parent fixed iframe reloads, but introduced a display-layer regression: its native video branch has `z-index:10` while our sibling overlay used `z-index:1`. GPU processing and worker canvas paints could advance normally underneath the original picture. Frame counters and successful decoder events did not validate the final visible result.

`position()` now finds the video's stacking branch under the overlay parent and places the overlay one level above it. On the tested YouTube player that is layer 11, with captions/player controls on higher layers. The overlay remains pointer-transparent. Do not replace this with an arbitrarily high global z-index that hides player controls.

`tests/visible_output.py` reproduces the nested video layer and higher player controls. A temporary marker in the actual extension iframe proves screenshot pixels are occluded with the old layer order and visible with the corrected order. The test then proves the real enhanced canvas covers that marker, player controls remain visible, and turning VSR off immediately removes the overlay while the original continues playing. Screenshot comparisons allow small display color-management shifts.

The dashboard distinguishes enabled intent from current presentation. `LIVE` requires fresh presented frames and a visible, playing video; a connected engine alone is insufficient. The first enhanced frame ends startup and records its elapsed time. Paused, hidden, waiting and above-target bypass states explicitly explain why the original is showing. Off mode labels the source as original instead of showing an enhanced-resolution arrow.

### Which clock to use

`expectedDisplayTime` includes the native player's A/V compensation. Using it as the audio-input clock counted that compensation again and failed the flash/beep check. `presentation-clock.js` reconstructs the media-input timeline from `video.currentTime` sampled during frame callbacks. Median filtering removes timing-read noise; limited drift correction avoids moving frame deadlines abruptly.

Audio-device base/output latency is filtered separately. A reported zero output latency during startup means the estimate is unavailable; initializing the filter with that zero created a slowly corrected bias and unnecessary audio delay. The first valid estimate initializes immediately. Pictures wait for their matching audible timestamp; a smoothed DelayNode adds latency only if processing needs more time than the existing device path provides. No fixed 50 ms delay is imposed.

The queue is bounded to 32 decoded pictures / 1 GiB, independently of the four processing slots. Seek epochs and source timestamps discard old results. Pause, waiting, seeking, visibility and rate changes flush presentation state. Refreshing a page is required to fully remove its MediaElementAudioSourceNode; off mode only removes the extra DelayNode delay and overlay.

### Cold CUDA initialization

Querying NVENC limits before a current CUDA context existed failed on a cold process, even when later requests succeeded after another diagnostic warmed CUDA. `gpu_video.py` initializes the runtime device before querying the current driver context, with a one-time primary-context fallback. Preserve the cold-first-request check.

### Extension background identity and the stopped-worker failure

Two separate unpacked-extension update failures were observed. First, an older background script remained cached while the page content was new; a distinct hashed worker URL prevented that source mix. Later, changing that URL while leaving the extension version unchanged left an existing normal profile registered to the previous URL. Chromium refused to start it (`DidStartWorkerFail …: 18`, `kErrorDisallowed`). The popup had no receiver even while `/health` said the GPU service was ready.

The exact latter failure was reproduced in an existing normal Helium profile: the registered worker was stopped, the browser rejected its start, and runtime messaging failed. Reloading a build with a new extension version restored the listener. Fresh-profile tests alone did not catch this persistent-profile problem.

`./vsr configure` now produces both a hashed standalone worker URL and a matching four-part local extension version: the source release's major/minor plus 32 bits from the build digest. `version_name` retains the readable release. Asset changes therefore change the extension version as well as the worker URL. `./vsr start` and browser-native startup do **not** regenerate an installed extension. Runtime lifecycle must not mutate extension build identity. After a source update, explicitly configure, reload the extension and refresh the video tab; a browser restart also loads the changed version. Old private workers are retained for profiles still using them.

The dashboard checks engine health directly and browser messaging independently. A stopped or rejected background worker offers **Reload extension**; a normally stopped engine leaves **Enable for this video** available. The UI no longer treats both conditions as “start the engine.”

Relevant Chromium sources: [status codes](https://raw.githubusercontent.com/chromium/chromium/main/third_party/blink/public/common/service_worker/service_worker_status_code.h) and [extension worker URL checks](https://chromium.googlesource.com/chromium/src/+/refs/heads/main/chrome/browser/extensions/chrome_content_browser_client_extensions_part.cc).

### Browser-managed engine lifecycle

The browser starts `scripts/native_host.sh` through [native messaging](https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging), restricted to the exact unpacked extension origin. Its bounded control protocol accepts only `status` and `ensure`; no command strings, arbitrary executable paths or frame data are accepted. A start lock coalesces simultaneous requests. It launches the project-owned loopback engine and returns only readiness information. The native host then exits; video continues over the existing authenticated WebSocket.

The engine initializes its GPU effect on the first frame. After 15 seconds without frames it closes the effect/encoder and releases pooled allocations, including while a video is paused. An open stream prevents process shutdown so playback can resume. After the last stream closes, the browser-managed engine exits after 30 seconds idle; exiting releases the CUDA context too. Health polling never extends that deadline. A manually started API service remains persistent, but still unloads an idle effect.

Registration stores the manifest in `.state/native-host` and creates a link in the browser's `NativeMessagingHosts` directory. `./vsr remove-browser-helper` removes only the links belonging to this clone. No system service, global Python install or login autostart is required. Moving the clone requires re-registering its helper and reloading the unpacked extension.

## Auto resolution and real limits

`resolution.js` computes a scale to fit the target's landscape or portrait bounds, preserving aspect ratio to the SDK's eight-pixel output alignment. A source above target bypasses enhancement; an equal-sized source uses 1×. The API accepts finite scales ≥1. Auto 360p → 4K (6×) worked in the tested runtime, although upstream documentation describes standard upscaling through 4×; do not promise every factor on every SDK/GPU.

No arbitrary 1080p input or 4K output cap is imposed. NVENC dimensions are queried on the active GPU (8192×8192 on the tested RTX 5090), input packets are bounded to 64 MiB, and minimum visible GPU-buffer memory is checked. Actual AI workspace/driver allocation can still fail. Large outputs reduce achievable fps.

## Security and UI

The API binds to 127.0.0.1, validates hosts, authenticates frame requests and checks browser WebSocket origins. The random local token is stored mode 0600. A malicious page cannot read the token from page-world JS; the extension bridge verifies a one-use tab grant. The worker and loopback service process local frames, with no remote upload or telemetry from project code.

The toolbar dashboard is an extension page. It requests sanitized per-tab state/settings through the background, and reads health without receiving the token. DOM values use textContent, not HTML interpolation. Injected content is opt-in on arbitrary pages using activeTab; YouTube and the local fixture have declared content scripts. Only enabling VSR requests native startup; opening the dashboard and polling health do not start GPU processing. The native helper uses fixed project-owned executables and retains no browser cookies or video frames.

Trust is local-user trust, not isolation against another process running as the same user. Do not expose the API to a LAN or deploy it as an unauthenticated server.

## References

- [NVIDIA VFX Python API](https://docs.nvidia.com/maxine/vfx-python/latest/api.html)
- [NVIDIA Python encoder API](https://docs.nvidia.com/video-technologies/pynvvideocodec/pynvc-api-reference/encoder.html)
- [WebCodecs](https://www.w3.org/TR/webcodecs/)
- [Web Audio](https://webaudio.github.io/web-audio-api/)
