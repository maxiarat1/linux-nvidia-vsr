(() => {
  if (globalThis.__localVsrAttached) return;
  globalThis.__localVsrAttached = true;

  const host = document.createElement("div");
  host.id = "local-nvidia-vsr";
  host.style.cssText =
    "display:none;position:fixed;right:16px;bottom:16px;z-index:2147483647;font:12px system-ui;color:#eee";
  const shadow = host.attachShadow({ mode: "open" });
  shadow.innerHTML = `<style>
    .panel{background:#171d23ed;border:1px solid #56713b;border-radius:10px;padding:10px;box-shadow:0 4px 24px #0007;max-width:360px}
    button,select{font:inherit;background:#26313b;color:#fff;border:1px solid #52616c;border-radius:5px;padding:5px;cursor:pointer}
    button[aria-pressed=true]{background:#416c18} .row{display:flex;flex-wrap:wrap;gap:6px;align-items:center} .status{margin-top:7px;max-width:340px;line-height:1.4}
    .close{border:0;background:none;padding:0 3px;color:#aaa} label{display:flex;gap:4px;align-items:center}
  </style><div class="panel"><div class="row">
    <button id="toggle" aria-pressed="false">VSR OFF</button>
    <select id="quality" aria-label="NVIDIA VSR quality"><option>LOW</option><option>MEDIUM</option><option selected>HIGH</option><option>ULTRA</option></select>
    <select id="scale" aria-label="Upscale factor"><option value="auto" selected>Auto</option><option value="1">1×</option><option value="1.5">1.5×</option><option value="2">2×</option><option value="3">3×</option><option value="4">4×</option></select>
    <button id="ab" title="Hold to compare original video">Original</button>
    <button id="close" class="close" title="Hide panel (extension button restores it)">×</button>
    </div><div class="row" style="margin-top:7px"><label>Target <select id="target" aria-label="Target enhanced resolution"><option value="720">720p</option><option value="1080">1080p</option><option value="1440">1440p</option><option value="2160" selected>4K</option><option value="4320">8K</option></select></label><span id="sizing"></span></div><div class="status" id="status">NVIDIA VSR · select a video, then switch on.</div></div>`;
  document.documentElement.append(host);
  const ui = (id) => shadow.getElementById(id);
  // The toolbar is the default control surface. Page controls are opt-in.
  chrome.storage.local.get("vsrPanelVisible").then(({ vsrPanelVisible }) => {
    host.style.display = vsrPanelVisible === true ? "block" : "none";
  });
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === "local" && changes.vsrPanelVisible)
      host.style.display =
        changes.vsrPanelVisible.newValue === true ? "block" : "none";
  });
  let overlay = null,
    outputCtx = null,
    renderer = null;
  function setupRenderer() {
    const kind =
      host.dataset.renderer === "legacy"
        ? "bitmaprenderer"
        : host.dataset.renderer === "direct"
          ? "2d"
          : "worker";
    if (renderer === kind) return;
    overlay?.remove();
    overlay = document.createElement(kind === "worker" ? "iframe" : "canvas");
    overlay.dataset.nvidiaVsrOverlay = "true";
    overlay.style.cssText =
      "position:absolute;pointer-events:none;z-index:1;object-fit:contain;display:none;background:transparent;border:0";
    outputCtx =
      kind === "worker" ? {} : overlay.getContext(kind, { alpha: false });
    renderer = kind;
  }
  setupRenderer();
  let sizing = null,
    settingsTouched = false;
  function updateSizing(source = video) {
    ui("target").disabled = ui("scale").value !== "auto";
    const resolved =
      source &&
      VsrResolution.resolve(
        source.videoWidth,
        source.videoHeight,
        ui("scale").value,
        ui("target").value,
      );
    ui("scale").querySelector('[value="auto"]').textContent =
      resolved && ui("scale").value === "auto"
        ? `Auto (${VsrResolution.factor(resolved.scale)})`
        : "Auto";
    ui("sizing").textContent = resolved
      ? resolved.bypass
        ? "Source above target · original"
        : `${resolved.width}×${resolved.height}`
      : "";
    return resolved;
  }
  function saveSettings() {
    settingsTouched = true;
    chrome.storage.local
      .set({
        vsrSettings: {
          quality: ui("quality").value,
          scale: ui("scale").value,
          target: ui("target").value,
        },
      })
      .catch((error) =>
        console.warn(
          "[Local NVIDIA VSR] Cannot save preferences:",
          error.message,
        ),
      );
  }
  chrome.storage.local
    .get("vsrSettings")
    .then(({ vsrSettings }) => {
      if (!vsrSettings || settingsTouched || active || starting) return;
      for (const id of ["quality", "scale", "target"])
        if ([...ui(id).options].some((o) => o.value === vsrSettings[id]))
          ui(id).value = vsrSettings[id];
      updateSizing(findVideo());
    })
    .catch((error) =>
      console.warn(
        "[Local NVIDIA VSR] Cannot load preferences:",
        error.message,
      ),
    );
  let active = false,
    starting = false,
    ready = false,
    inflight = 0,
    video = null,
    port = null,
    bridge = null,
    callback = null;
  let generation = 0,
    seekEpoch = 0,
    latestMediaTime = -1,
    sentMediaTime = -2,
    frames = 0;
  let sentAt = 0,
    lastResult = 0,
    lastUi = 0,
    comparison = false,
    completedAt = [],
    roundTrips = [];
  let captureMs = 0,
    startupTimer = null;
  let lastPresentedTime = -1;
  let enabledAt = 0,
    firstFrameMs = null;
  function markPresented() {
    if (firstFrameMs === null) {
      firstFrameMs = Math.round(performance.now() - enabledAt);
      host.dataset.firstFrameMs = String(firstFrameMs);
    }
    if (starting) {
      starting = false;
      clearTimeout(startupTimer);
      ui("toggle").textContent = "VSR ON";
      ui("toggle").removeAttribute("aria-busy");
    }
  }
  const pictureWidth = (image) => image.displayWidth || image.width;
  const pictureHeight = (image) => image.displayHeight || image.height;
  const wall = () => performance.timeOrigin + performance.now();
  const audioSync = new VsrAudioSync();
  const presentationClock = new VsrPresentationClock();
  const pendingPictures = [];
  let presentationCallback = null,
    syncWarmup = 0;
  function clearPictures() {
    for (const msg of pendingPictures) msg.bitmap.close();
    pendingPictures.length = 0;
  }
  function presentPicture(rafTime) {
    if (!active || renderer === "worker") return;
    presentationCallback = requestAnimationFrame(presentPicture);
    if (video.paused || video.seeking || document.hidden) return;
    const sourceTime = presentationClock.mediaTime(rafTime);
    if (sourceTime === null) return;
    const mediaTime = audioSync.audibleTime(video, sourceTime);
    let selected;
    while (
      pendingPictures.length &&
      pendingPictures[0].mediaTime <= mediaTime
    ) {
      selected?.bitmap.close();
      selected = pendingPictures.shift();
    }
    if (!selected) return;
    if (
      selected.epoch !== seekEpoch ||
      selected.mediaTime <= lastPresentedTime
    ) {
      selected.bitmap.close();
      return;
    }
    const msg = selected,
      now = performance.now();
    if (
      overlay.width !== pictureWidth(msg.bitmap) ||
      overlay.height !== pictureHeight(msg.bitmap)
    ) {
      overlay.width = pictureWidth(msg.bitmap);
      overlay.height = pictureHeight(msg.bitmap);
    }
    try {
      const begin = performance.now();
      if (renderer === "bitmaprenderer")
        outputCtx.transferFromImageBitmap(msg.bitmap);
      else {
        outputCtx.drawImage(msg.bitmap, 0, 0);
        msg.bitmap.close();
      }
      msg.timings.canvas_submit_ms = performance.now() - begin;
      msg.timings.raf_lateness_ms = now - rafTime;
    } catch (error) {
      msg.bitmap.close();
      stop(`Cannot present enhanced frame: ${error.message}`);
      return;
    }
    overlay.style.display = comparison ? "none" : "block";
    lastPresentedTime = msg.mediaTime;
    lastResult = now;
    markPresented();
    frames++;
    host.dataset.presentedMediaTime = String(msg.mediaTime);
    host.dataset.presentedWall = String(wall());
    host.dataset.frames = String(frames);
    completedAt.push(now);
    while (completedAt.length > 2 && completedAt[0] < now - 2000)
      completedAt.shift();
    msg.timings.presentation_wait_ms = +(wall() - msg.readyWall).toFixed(3);
    msg.timings.presentation_age_ms = +(wall() - msg.captureStart).toFixed(3);
    msg.timings.estimated_av_error_ms = +(
      ((mediaTime - msg.mediaTime) * 1000) /
      video.playbackRate
    ).toFixed(3);
    host.dataset.timings = JSON.stringify(msg.timings);
  }
  let videoDecoder = null,
    videoDecoderKey = "",
    decodeSequence = 0;
  const videoDecodes = new Map();
  function closeVideoDecoder() {
    if (videoDecoder && videoDecoder.state !== "closed") videoDecoder.close();
    videoDecoder = null;
    videoDecoderKey = "";
    for (const p of videoDecodes.values()) {
      clearTimeout(p.timer);
      p.reject(new DOMException("Video decoder reset", "AbortError"));
    }
    videoDecodes.clear();
  }
  function decodeAv1(bytes, stats) {
    const key = `${stats.codec}/${stats.width}/${stats.height}`;
    if (key !== videoDecoderKey) {
      closeVideoDecoder();
      const g = generation;
      videoDecoder = new VideoDecoder({
        output: async (frame) => {
          const p = videoDecodes.get(frame.timestamp);
          videoDecodes.delete(frame.timestamp);
          if (!p) {
            frame.close();
            return;
          }
          clearTimeout(p.timer);
          if (renderer !== "bitmaprenderer") {
            p.resolve(frame);
            return;
          }
          try {
            p.resolve(await createImageBitmap(frame));
          } catch (error) {
            p.reject(error);
          } finally {
            frame.close();
          }
        },
        error: (error) => {
          if (g === generation)
            stop(`Hardware video decoding failed: ${error.message}`);
        },
      });
      videoDecoder.configure({
        codec: stats.codec,
        codedWidth: stats.width,
        codedHeight: stats.height,
        hardwareAcceleration: "prefer-hardware",
        optimizeForLatency: true,
      });
      videoDecoderKey = key;
    }
    return new Promise((resolve, reject) => {
      const timestamp = ++decodeSequence;
      const timer = setTimeout(() => {
        videoDecodes.delete(timestamp);
        reject(new Error("Hardware video decode timed out"));
      }, 5000);
      videoDecodes.set(timestamp, { resolve, reject, timer });
      try {
        videoDecoder.decode(
          new EncodedVideoChunk({ type: "key", timestamp, data: bytes }),
        );
      } catch (error) {
        clearTimeout(timer);
        videoDecodes.delete(timestamp);
        reject(error);
      }
    });
  }
  const status = (text) => {
    ui("status").textContent = text;
    host.dataset.status = text;
  };
  const fail = (message) => {
    if (
      /Receiving end does not exist|Extension context invalidated/.test(message)
    )
      message =
        "VSR extension background is unavailable. Reopen the isolated browser with ./vsr browser, then try again.";
    stop(message);
    host.dataset.lastError = message;
    ui("status").setAttribute("role", "alert");
    console.warn("[Local NVIDIA VSR]", message);
  };
  const invalidate = () => {
    presentationClock.reset();
    seekEpoch++;
    if (renderer === "worker")
      port?.postMessage({ type: "render-reset", epoch: seekEpoch });
    lastPresentedTime = -1;
    overlay.style.display = "none";
    clearPictures();
    audioSync.bypass();
    syncWarmup = 0;
  };
  const pictureInPicture = () =>
    stop("Picture-in-picture shows the original video; VSR stopped.");

  function findVideo() {
    return [...document.querySelectorAll("video")]
      .filter((v) => v.videoWidth && v.getBoundingClientRect().width > 100)
      .sort(
        (a, b) =>
          b.getBoundingClientRect().width * b.getBoundingClientRect().height -
          a.getBoundingClientRect().width * a.getBoundingClientRect().height,
      )[0];
  }
  function position() {
    if (!video || !active) return;
    const v = video.getBoundingClientRect(),
      parent =
        renderer === "worker"
          ? overlay.parentElement || video.parentElement
          : video.parentElement,
      p = parent.getBoundingClientRect();
    // The stable player parent survives YouTube fullscreen moves, but its
    // native video lives in a separate z-index:10 stacking context. A fixed
    // z-index:1 overlay can decode/paint successfully underneath that video.
    // Place it just above the video's branch, below separate player controls.
    let videoLayer = video;
    while (videoLayer.parentElement && videoLayer.parentElement !== parent)
      videoLayer = videoLayer.parentElement;
    const nativeLayer =
      Number.parseInt(getComputedStyle(videoLayer).zIndex) || 0;
    Object.assign(overlay.style, {
      left: `${v.left - p.left + parent.scrollLeft}px`,
      top: `${v.top - p.top + parent.scrollTop}px`,
      width: `${v.width}px`,
      height: `${v.height}px`,
      objectFit: getComputedStyle(video).objectFit || "contain",
      zIndex: String(Math.max(1, nativeLayer + 1)),
    });
    const fullscreen = document.fullscreenElement;
    if (fullscreen === video) {
      stop(
        "Native video fullscreen cannot show VSR. Use the website's player fullscreen.",
      );
      return;
    }
    const destination = fullscreen || document.documentElement;
    if (host.parentElement !== destination) destination.append(host);
  }
  function stop(message) {
    presentationClock.reset();
    audioSync.bypass();
    clearPictures();
    cancelAnimationFrame(presentationCallback);
    generation++;
    active = ready = starting = false;
    closeVideoDecoder();
    inflight = 0;
    clearTimeout(startupTimer);
    video?.removeEventListener("seeking", invalidate);
    video?.removeEventListener("pause", invalidate);
    video?.removeEventListener("ratechange", invalidate);
    video?.removeEventListener("waiting", invalidate);
    video?.removeEventListener("playing", invalidate);
    video?.removeEventListener("enterpictureinpicture", pictureInPicture);
    video?.removeEventListener("resize", sourceResize);
    if (callback !== null && video?.cancelVideoFrameCallback)
      video.cancelVideoFrameCallback(callback);
    callback = null;
    if (port) {
      try {
        port.postMessage({ type: "stop" });
        port.close();
      } catch (_) {}
      port = null;
    }
    bridge?.remove();
    bridge = null;
    overlay.remove();
    overlay.style.display = "none";
    ui("toggle").textContent = "VSR OFF";
    ui("toggle").setAttribute("aria-pressed", "false");
    ui("toggle").removeAttribute("aria-busy");
    host.dataset.active = "false";
    host.dataset.stage = "off";
    host.dataset.audio = JSON.stringify(audioSync.stats());
    if (message) status(message);
  }
  function schedule() {
    if (active && video) callback = video.requestVideoFrameCallback(tick);
  }
  function tick(now, metadata) {
    schedule();
    presentationClock.observe(
      metadata,
      video.playbackRate,
      video.currentTime,
      performance.now(),
    );
    if (renderer === "worker" && port) {
      if (!comparison && !video.paused && !video.seeking && !document.hidden)
        overlay.style.display = "block";
      sendRenderClock();
    }
    latestMediaTime = metadata.mediaTime;
    captureFrame();
  }
  function sendRenderClock() {
    if (renderer !== "worker" || !port || presentationClock.phase === null)
      return;
    const audio = audioSync.stats();
    port.postMessage({
      type: "render-clock",
      epoch: seekEpoch,
      phaseWall: performance.timeOrigin + presentationClock.phase,
      rate: video.playbackRate,
      latencyMs: audioSync.outputLatency() * 1000 + audio.delay_ms,
      reportedLatencyMs: audio.base_latency_ms + audio.output_latency_ms,
      visible:
        !comparison && !video.paused && !video.seeking && !document.hidden,
    });
  }
  function sourceResize() {
    if (!video?.videoWidth || !video?.videoHeight) return;
    if (active || starting) start();
    else updateSizing();
  }
  async function captureFrame() {
    if (
      !active ||
      !ready ||
      inflight >= 4 ||
      (renderer === "worker" && comparison) ||
      document.hidden ||
      video.paused ||
      video.seeking ||
      latestMediaTime < 0 ||
      latestMediaTime === sentMediaTime
    )
      return;
    if (
      sizing &&
      (video.videoWidth !== sizing.sourceWidth ||
        video.videoHeight !== sizing.sourceHeight)
    ) {
      sourceResize();
      return;
    }
    try {
      const g = generation,
        epoch = seekEpoch,
        mediaTime = latestMediaTime;
      inflight++;
      sentMediaTime = mediaTime;
      sentAt = performance.now();
      const t = performance.now();
      if ((host.dataset.inputFormat || "nv12") === "nv12") {
        const source = new VideoFrame(video, { timestamp: 0 });
        try {
          if (
            source.format === "NV12" &&
            source.visibleRect.width % 2 === 0 &&
            source.visibleRect.height % 2 === 0
          ) {
            const w = source.visibleRect.width,
              h = source.visibleRect.height;
            const layout = [
              { offset: 0, stride: w },
              { offset: w * h, stride: w },
            ];
            const metadata = {
              width: w,
              height: h,
              layout,
              color: source.colorSpace.toJSON(),
            };
            const json = new TextEncoder().encode(JSON.stringify(metadata));
            const size = 8 + json.length + source.allocationSize({ layout });
            if (size > 64 * 1024 * 1024)
              throw new Error("Frame exceeds the 64 MiB transport limit.");
            const bytes = new Uint8Array(size);
            bytes.set([86, 83, 82, 49]);
            new DataView(bytes.buffer).setUint32(4, json.length, true);
            bytes.set(json, 8);
            await source.copyTo(bytes.subarray(8 + json.length), { layout });
            if (g !== generation || !active) return;
            host.dataset.inputPath = "raw NV12";
            port.postMessage(
              {
                type: "raw-frame",
                bytes: bytes.buffer,
                epoch,
                mediaTime,
                capturedWall: wall(),
                captureMs: performance.now() - t,
                captureStart: performance.timeOrigin + t,
              },
              [bytes.buffer],
            );
            return;
          }
          host.dataset.inputPath = `JPEG fallback (${source.format})`;
        } finally {
          source.close();
        }
      } else host.dataset.inputPath = "JPEG";
      // ImageBitmap is transferable across extension origins. VideoFrame is
      // deliberately restricted to the same agent cluster by WebCodecs.
      const frame = await createImageBitmap(video);
      if (g !== generation || !active) {
        frame.close();
        return;
      }
      captureMs = performance.now() - t;
      port.postMessage(
        {
          type: "frame",
          frame,
          epoch,
          mediaTime,
          capturedWall: wall(),
          captureMs,
          captureStart: performance.timeOrigin + t,
        },
        [frame],
      );
    } catch (error) {
      stop(
        error.name === "SecurityError"
          ? "This video's cross-origin/protected frames cannot be captured."
          : error.message,
      );
    }
  }
  async function start() {
    stop();
    enabledAt = performance.now();
    firstFrameMs = null;
    delete host.dataset.firstFrameMs;
    delete host.dataset.lastError;
    ui("status").removeAttribute("role");
    video = findVideo();
    if (!video) {
      status("No playable video found. Start a video first.");
      return;
    }
    setupRenderer();
    sizing = updateSizing();
    sizing.sourceWidth = video.videoWidth;
    sizing.sourceHeight = video.videoHeight;
    host.dataset.effectiveScale = String(sizing.scale);
    host.dataset.target =
      ui("scale").value === "auto" ? ui("target").value : "manual";
    saveSettings();
    video.addEventListener("resize", sourceResize);
    if (sizing.bypass) {
      active = true;
      host.dataset.active = "true";
      host.dataset.stage = "original";
      host.dataset.frames = "0";
      delete host.dataset.processedFps;
      delete host.dataset.roundtripMs;
      ui("toggle").textContent = "VSR ON";
      ui("toggle").setAttribute("aria-pressed", "true");
      status(
        "Auto · source is above the selected target. Showing original video.",
      );
      return;
    }
    if (video.mediaKeys) {
      status("Protected video is not supported.");
      return;
    }
    if (
      !globalThis.createImageBitmap ||
      !video.requestVideoFrameCallback ||
      !outputCtx
    ) {
      status("This browser lacks the required video-frame APIs.");
      return;
    }
    const g = generation;
    starting = true;
    ui("toggle").textContent = "VSR STARTING…";
    ui("toggle").setAttribute("aria-busy", "true");
    host.dataset.stage = "audio";
    status("Starting synchronized audio…");
    startupTimer = setTimeout(() => {
      if (g === generation && !frames && !video.paused && !document.hidden)
        fail(
          `VSR startup timed out during ${host.dataset.stage}. Play the video, then retry.`,
        );
    }, 35000);
    try {
      await audioSync.attach(video);
    } catch (error) {
      if (g === generation) fail(`Cannot synchronize audio: ${error.message}`);
      return;
    }
    if (g !== generation) return;
    active = true;
    frames = 0;
    syncWarmup = 0;
    completedAt = [];
    roundTrips = [];
    lastResult = lastUi = 0;
    latestMediaTime = -1;
    sentMediaTime = -2;
    lastPresentedTime = -1;
    video.addEventListener("seeking", invalidate);
    video.addEventListener("pause", invalidate);
    video.addEventListener("ratechange", invalidate);
    video.addEventListener("waiting", invalidate);
    video.addEventListener("playing", invalidate);
    video.addEventListener("enterpictureinpicture", pictureInPicture);
    ui("toggle").textContent = "VSR STARTING…";
    ui("toggle").setAttribute("aria-pressed", "true");
    host.dataset.active = "true";
    host.dataset.frames = "0";
    host.dataset.transport = "transferable-worker";
    if (renderer !== "worker") video.parentElement.append(overlay);
    status("Connecting to NVIDIA engine…");
    position();
    schedule();
    presentationCallback = requestAnimationFrame(presentPicture);
    try {
      host.dataset.stage = "authorization";
      const auth = await chrome.runtime.sendMessage({ type: "vsr-grant" });
      if (g !== generation || !active) return;
      if (!auth?.grant)
        throw new Error(
          auth?.error || "Extension changed. Reload it and refresh this page.",
        );
      if (auth.protocol !== 2)
        throw new Error(
          "VSR extension components do not match. Reopen the isolated browser with ./vsr browser.",
        );
      host.dataset.stage = "bridge";
      const channel = new MessageChannel();
      port = channel.port1;
      port.onmessageerror = () =>
        stop("Browser blocked a transferred image from the extension worker");
      port.onmessage = async (event) => {
        let msg = event.data;
        if (msg.type === "encoded-result") {
          const t = wall(),
            { meta, stats } = msg;
          try {
            let bitmap;
            if (stats.format === "av1")
              bitmap = await decodeAv1(msg.bytes, stats);
            else if (globalThis.ImageDecoder) {
              const decoder = new ImageDecoder({
                data: msg.bytes,
                type: "image/jpeg",
              });
              try {
                const { image } = await decoder.decode();
                try {
                  bitmap = await createImageBitmap(image);
                } finally {
                  image.close();
                }
              } finally {
                decoder.close();
              }
            } else
              bitmap = await createImageBitmap(
                new Blob([msg.bytes], { type: "image/jpeg" }),
              );
            msg = {
              type: "result",
              bitmap,
              stats,
              ...meta,
              decodedWall: wall(),
              timings: {
                capture_ms: meta.captureMs,
                input_handoff_ms: meta.inputHandoffMs,
                encoder_handoff_ms: meta.encoderHandoffMs,
                encode_ms: meta.encodeMs,
                socket_ms: msg.socketMs,
                decoder_handoff_ms: t - msg.receivedWall,
                decode_ms: wall() - t,
                service_ms: stats.service_ms,
              },
            };
          } catch (error) {
            if (error.name === "AbortError") {
              if (g === generation) {
                inflight = Math.max(0, inflight - 1);
                captureFrame();
              }
              return;
            }
            if (g === generation)
              stop(`Cannot decode enhanced image: ${error.message}`);
            return;
          }
        }
        if (msg.type === "stage") {
          host.dataset.stage = msg.stage;
          return;
        }
        if (g !== generation || !active) {
          msg.bitmap?.close();
          return;
        }
        if (msg.type === "decoded") {
          inflight = Math.max(0, inflight - 1);
          if (msg.meta.epoch === seekEpoch && !video.paused && !video.seeking) {
            const atDecode = presentationClock.mediaTime(
              msg.decodedWall - performance.timeOrigin,
            );
            const age =
              ((atDecode - msg.meta.mediaTime) * 1000) / video.playbackRate;
            if (++syncWarmup > 12) audioSync.update(age);
            host.dataset.audio = JSON.stringify(audioSync.stats());
            sendRenderClock();
          }
          roundTrips.push(msg.decodedWall - msg.meta.captureStart);
          if (roundTrips.length > 60) roundTrips.shift();
          captureFrame();
          return;
        }
        if (msg.type === "presented") {
          if (msg.epoch !== seekEpoch || video.paused || video.seeking) return;
          const now = performance.now(),
            stamp = msg.presentedWall - performance.timeOrigin;
          overlay.dataset.outputWidth = String(msg.stats.width);
          overlay.dataset.outputHeight = String(msg.stats.height);
          overlay.style.display = comparison ? "none" : "block";
          lastResult = now;
          markPresented();
          lastPresentedTime = msg.mediaTime;
          frames++;
          host.dataset.stage = "displaying";
          host.dataset.presentedMediaTime = String(msg.mediaTime);
          host.dataset.presentedWall = String(msg.presentedWall);
          host.dataset.frames = String(frames);
          host.dataset.timings = JSON.stringify(msg.timings);
          completedAt.push(stamp);
          while (completedAt.length > 2 && completedAt[0] < stamp - 2000)
            completedAt.shift();
          const fps =
            completedAt.length > 1
              ? (1000 * (completedAt.length - 1)) / (stamp - completedAt[0])
              : 0;
          host.dataset.processedFps = fps.toFixed(1);
          const avg = roundTrips.length
            ? roundTrips.reduce((a, b) => a + b, 0) / roundTrips.length
            : 0;
          host.dataset.roundtripMs = String(Math.round(avg));
          host.dataset.quality = msg.stats.quality;
          if (now - lastUi > 250) {
            lastUi = now;
            status(
              `${msg.stats.quality} · ${msg.stats.input_width}×${msg.stats.input_height} → ${msg.stats.width}×${msg.stats.height} · ${Math.round(fps)} enhanced fps · ${Math.round(avg)} ms processing · GPU AV1 · audio sync · AI ${msg.stats.inference_ms.toFixed(1)} ms`,
            );
          }
          return;
        }
        if (msg.type === "ready") {
          ready = true;
          status("Engine connected. Waiting for the first enhanced frame…");
          captureFrame();
        } else if (msg.type === "result") {
          host.dataset.stage = "displaying";
          const now = performance.now();
          const present =
            !video.seeking &&
            !video.paused &&
            msg.epoch === seekEpoch &&
            !document.hidden &&
            wall() - msg.captureStart < 200 &&
            msg.mediaTime > lastPresentedTime;
          if (present) {
            const age =
              ((video.currentTime - msg.mediaTime) * 1000) / video.playbackRate;
            // Cold model/codec initialization is not steady-state latency.
            // Present fresh frames first, then let the controller adapt.
            if (++syncWarmup > 12) audioSync.update(age);
            host.dataset.audio = JSON.stringify(audioSync.stats());
            msg.readyWall = wall();
            Object.assign(msg.timings, {
              page_handoff_ms: +(wall() - msg.decodedWall).toFixed(3),
              frame_age_ms: +(wall() - msg.captureStart).toFixed(3),
              media_age_ms: +age.toFixed(3),
              inference_ms: msg.stats.inference_ms,
              input_decode_ms: msg.stats.input_decode_ms,
              upload_ms: msg.stats.upload_ms,
              output_encode_ms: msg.stats.output_encode_ms,
            });
            pendingPictures.push(msg);
            pendingPictures.sort((a, b) => a.mediaTime - b.mediaTime);
            // Bounded independently of the four processing slots.
            const bytes = pendingPictures.reduce(
              (n, p) =>
                n + pictureWidth(p.bitmap) * pictureHeight(p.bitmap) * 4,
              0,
            );
            if (pendingPictures.length > 32 || bytes > 1024 * 1024 * 1024) {
              stop("Audio output buffering exceeds the picture memory budget.");
              return;
            }
          } else msg.bitmap.close();
          const fps =
            completedAt.length > 1
              ? (1000 * (completedAt.length - 1)) / (now - completedAt[0])
              : 0;
          roundTrips.push(wall() - msg.captureStart);
          if (roundTrips.length > 60) roundTrips.shift();
          const avg = roundTrips.reduce((a, b) => a + b, 0) / roundTrips.length;
          host.dataset.processedFps = fps.toFixed(1);
          host.dataset.roundtripMs = String(Math.round(avg));
          host.dataset.quality = msg.stats.quality;
          // Status/layout updates need not run at the video frame rate.
          if (now - lastUi > 250) {
            lastUi = now;
            status(
              `${msg.stats.quality} · ${msg.stats.input_width}×${msg.stats.input_height} → ${msg.stats.width}×${msg.stats.height} · ${Math.round(fps)} enhanced fps · ${Math.round(avg)} ms processing · ${msg.stats.input_format === "NV12 CUDA" ? "NV12" : "JPEG in"} → ${msg.stats.format === "av1" ? "GPU AV1" : "JPEG"} · audio sync · AI ${msg.stats.inference_ms.toFixed(1)} ms`,
            );
          }
          inflight = Math.max(0, inflight - 1);
          captureFrame();
        } else if (msg.type === "error") fail(msg.message);
        else if (msg.type === "closed")
          stop("Engine disconnected. Original video restored.");
      };
      port.start();
      bridge =
        renderer === "worker" ? overlay : document.createElement("iframe");
      if (renderer !== "worker")
        bridge.style.cssText =
          "position:fixed;width:1px;height:1px;left:-10px;top:0;opacity:0;pointer-events:none;border:0";
      bridge.setAttribute("aria-hidden", "true");
      bridge.tabIndex = -1;
      let initialized = false;
      bridge.onload = () => {
        if (g !== generation || !active) return;
        if (bridge.contentDocument?.URL === "about:blank") return;
        // A site can move a video's ancestor during fullscreen transitions.
        // Moving an iframe reloads its document and destroys its worker/port.
        if (initialized) {
          start();
          return;
        }
        initialized = true;
        bridge.contentWindow.postMessage(
          {
            type: "vsr-init",
            grant: auth.grant,
            quality: ui("quality").value,
            scale: sizing.scale,
            format: host.dataset.outputFormat || "av1",
            workerRendering: renderer === "worker",
            objectFit: getComputedStyle(video).objectFit || "contain",
          },
          new URL(chrome.runtime.getURL("bridge.html")).origin,
          [channel.port2],
        );
      };
      bridge.src = chrome.runtime.getURL("bridge.html");
      if (renderer !== "worker") document.documentElement.append(bridge);
      else {
        (video.closest(".html5-video-player") || video.parentElement).append(
          bridge,
        );
        position();
      }
    } catch (error) {
      if (g === generation) fail(error.message);
    }
  }
  ui("toggle").onclick = () =>
    active || starting ? stop("Original video. NVIDIA VSR is off.") : start();
  for (const id of ["quality", "scale", "target"])
    ui(id).onchange = () => {
      saveSettings();
      updateSizing();
      if (active || starting) start();
    };
  ui("close").onclick = () => {
    stop("NVIDIA VSR off.");
    host.style.display = "none";
    chrome.storage.local.set({ vsrPanelVisible: false });
  };
  ui("ab").onpointerdown = (e) => {
    comparison = true;
    if (renderer === "worker")
      port?.postMessage({ type: "render-reset", epoch: seekEpoch });
    overlay.style.display = "none";
    e.target.setPointerCapture(e.pointerId);
  };
  ui("ab").onpointerup = ui("ab").onpointercancel = () => {
    comparison = false;
    if (
      active &&
      (renderer === "worker" ||
        (lastResult && performance.now() - lastResult < 500))
    )
      overlay.style.display = "block";
    sendRenderClock();
  };
  document.addEventListener("fullscreenchange", position);
  document.addEventListener("visibilitychange", () => {
    if (active) invalidate();
  });
  document.addEventListener("yt-navigate-start", () => {
    if (active) stop("Video changed. Enable VSR for the new video.");
  });
  window.addEventListener("resize", position);
  setInterval(() => {
    if (!active) {
      if (!starting) updateSizing(findVideo());
      return;
    }
    if (!video?.isConnected) {
      stop("Video changed. Reattach VSR.");
      return;
    }
    if (
      video.paused ||
      video.seeking ||
      document.hidden ||
      (renderer !== "worker" && performance.now() - lastResult > 500)
    )
      overlay.style.display = "none";
    if (inflight && performance.now() - Math.max(sentAt, lastResult) > 30000)
      stop("Processing timed out. Original video restored.");
    position();
  }, 200);
  function dashboardState() {
    const source = findVideo(),
      resolved = updateSizing(source);
    const presenting =
      active &&
      !starting &&
      !resolved?.bypass &&
      !comparison &&
      !video?.paused &&
      !video?.seeking &&
      !document.hidden &&
      overlay.isConnected &&
      overlay.style.display !== "none" &&
      frames > 0 &&
      performance.now() - lastResult < 500;
    return {
      active,
      starting,
      presenting: !!presenting,
      tabVisible: !document.hidden,
      comparing: comparison,
      seeking: !!source?.seeking,
      firstFrameMs,
      video: !!source,
      paused: source?.paused,
      sourceWidth: source?.videoWidth || 0,
      sourceHeight: source?.videoHeight || 0,
      outputWidth: resolved?.width || 0,
      outputHeight: resolved?.height || 0,
      bypass: !!resolved?.bypass,
      effectiveScale: resolved?.scale || 1,
      quality: ui("quality").value,
      scale: ui("scale").value,
      target: ui("target").value,
      panelVisible: host.style.display !== "none",
      fps: active ? Number(host.dataset.processedFps || 0) : 0,
      processingMs: active ? Number(host.dataset.roundtripMs || 0) : 0,
      frames: active ? frames : 0,
      audio: audioSync.stats(),
      status: ui("status").textContent,
      error: host.dataset.lastError || "",
    };
  }
  chrome.runtime.onMessage.addListener((msg, sender, reply) => {
    if (msg.type === "vsr-toggle") {
      host.style.display = "block";
      active || starting ? stop("Original video. VSR off.") : start();
    } else if (msg.type === "vsr-control") {
      const run = async () => {
        if (msg.action === "start" && !active && !starting) await start();
        else if (msg.action === "stop")
          stop("Original video. NVIDIA VSR is off.");
        else if (msg.action === "settings") {
          for (const id of ["quality", "scale", "target"])
            if ([...ui(id).options].some((o) => o.value === msg.settings?.[id]))
              ui(id).value = msg.settings[id];
          saveSettings();
          updateSizing(findVideo());
          if (active || starting) await start();
        } else if (msg.action === "panel") {
          host.style.display = msg.visible ? "block" : "none";
          await chrome.storage.local.set({ vsrPanelVisible: !!msg.visible });
        }
        return dashboardState();
      };
      run().then(reply, (error) => reply({ error: error.message }));
      return true;
    }
  });
})();
