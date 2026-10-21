/* Toolbar controller. The HTML/CSS and preview share the same UI contract. */
const el = (id) => document.getElementById(id);
const preview =
  !globalThis.chrome?.runtime?.id ||
  new URLSearchParams(location.search).has("preview");
let tabId,
  state,
  online = false,
  bridgeReady = false,
  busy = false,
  connectionError = null;
const sample = {
  video: true,
  active: false,
  starting: false,
  sourceWidth: 1920,
  sourceHeight: 1080,
  outputWidth: 3840,
  outputHeight: 2160,
  effectiveScale: 2,
  quality: "HIGH",
  scale: "auto",
  target: "2160",
  panelVisible: false,
  fps: 59.9,
  processingMs: 24,
  audio: { delay_ms: 0 },
  status: "",
  error: "",
};
const failure = (message, code) => Object.assign(new Error(message), { code });
async function request(action, extra = {}) {
  if (preview) {
    if (action === "health" || action === "engine-start")
      return { ready: true };
    if (action === "start" || action === "stop")
      sample.active = action === "start";
    if (action === "settings") {
      Object.assign(sample, extra.settings);
      const scale =
        sample.scale === "auto"
          ? Math.max(1, Number(sample.target) / sample.sourceHeight)
          : Number(sample.scale);
      Object.assign(sample, {
        effectiveScale: scale,
        outputWidth: Math.round(sample.sourceWidth * scale),
        outputHeight: Math.round(sample.sourceHeight * scale),
      });
    }
    if (action === "panel") sample.panelVisible = extra.visible;
    bridgeReady = true;
    return { ...sample };
  }
  let result;
  try {
    result = await chrome.runtime.sendMessage({
      type: "vsr-dashboard",
      action,
      tabId,
      ...extra,
    });
  } catch (error) {
    throw failure(
      "The browser extension could not wake its background connection. Reload the extension, then refresh the video tab.",
      "EXTENSION_UNAVAILABLE",
    );
  }
  if (!result)
    throw failure(
      "The browser extension did not respond. Reload it, then refresh the video tab.",
      "EXTENSION_UNAVAILABLE",
    );
  bridgeReady = true;
  if (result.error && !("video" in result))
    throw failure(result.error, result.code || "TAB_UNAVAILABLE");
  return result;
}
function message(text, error = false) {
  el("message").textContent = text;
  el("message").classList.toggle("error", error);
}
function connection() {
  const broken = connectionError?.code === "EXTENSION_UNAVAILABLE";
  const helperMissing =
    connectionError?.code === "HELPER_SETUP_REQUIRED" ||
    /browser.helper/i.test(state?.error || "");
  el("engine").textContent = broken
    ? online
      ? "Engine ready · browser connection unavailable"
      : "Browser connection unavailable"
    : helperMissing
      ? "Browser helper needs setup"
      : busy
        ? "Starting enhancement…"
        : online
          ? "Local engine connected"
          : "Engine starts when you enable VSR";
  el("engine-dot").classList.toggle("online", online && !broken);
  el("engine-action").hidden = !connectionError;
  el("engine-action").textContent = broken
    ? "Reload extension"
    : "Retry connection";
  el("help").hidden =
    connectionError?.code !== "HELPER_SETUP_REQUIRED" &&
    !/browser.helper/i.test(state?.error || "");
}
function render(s) {
  state = s;
  for (const id of ["quality", "scale", "target"]) el(id).value = s[id];
  el("target").disabled = s.scale !== "auto" || busy;
  el("quality").disabled = el("scale").disabled = busy;
  el("panel").checked = s.panelVisible;
  el("panel").disabled = busy;
  // An offline engine is normal: the native helper starts it on Enable.
  el("toggle").disabled = busy || s.starting || !s.video || !bridgeReady;
  el("toggle").textContent = s.starting
    ? "Starting enhancement…"
    : s.active
      ? "Turn off enhancement"
      : "Enable for this video";
  el("toggle").classList.toggle("on", s.active);
  el("toggle").setAttribute("aria-pressed", String(s.active));
  el("state-tag").textContent = s.starting
    ? "STARTING"
    : s.active
      ? s.bypass
        ? "ORIGINAL"
        : "LIVE"
      : "STANDBY";
  el("state-title").textContent = s.starting
    ? "Starting enhancement"
    : s.active
      ? s.bypass
        ? "Original playback"
        : "Enhancement on"
      : s.video
        ? "Enhancement off"
        : "No video selected";
  el("summary").textContent = s.video
    ? `${s.sourceWidth} × ${s.sourceHeight} → ${s.outputWidth} × ${s.outputHeight}`
    : "Play a video in this tab, then reopen these controls.";
  el("factor").textContent = `${s.scale === "auto" ? "AUTO · " : ""}${Number(
    s.effectiveScale || 1,
  )
    .toFixed(2)
    .replace(/\.?0+$/, "")}×`;
  el("resolution-note").textContent = s.bypass
    ? "Source exceeds the target. The original video is shown."
    : "Aspect ratio is preserved.";
  const live = s.active && !s.bypass;
  for (const [id, value, suffix] of [
    ["fps", live ? Number(s.fps || 0).toFixed(1) : "—", " fps"],
    ["latency", live ? Math.round(s.processingMs || 0) : "—", " ms"],
    ["audio", live ? Math.round(s.audio?.delay_ms || 0) : "—", " ms"],
  ]) {
    const unit = document.createElement("small");
    unit.textContent = suffix;
    el(id).replaceChildren(document.createTextNode(value), unit);
  }
  el("latency").title =
    "Capture through return decoding; excludes intentional waiting for audio.";
  message(
    connectionError?.message ||
      s.error ||
      (s.starting
        ? "Starting the local engine and connecting this video…"
        : s.active
          ? s.bypass
            ? "The source is already above your selected target."
            : "NVIDIA AI enhancement is active. Playback controls stay with the video."
          : s.video
            ? "Enable enhancement for this video. Processing stops when you turn it off."
            : "Open these controls from the video tab."),
    !!connectionError || !!s.error,
  );
  connection();
}
async function health() {
  try {
    if (preview) online = true;
    else {
      const r = await fetch("http://127.0.0.1:8730/health", {
        cache: "no-store",
        signal: AbortSignal.timeout(1500),
      });
      const h = r.ok && (await r.json());
      online = !!h?.ready && h.engine === "NVIDIA VideoSuperRes";
    }
  } catch {
    online = false;
  }
  connection();
}
async function refresh() {
  if (busy) return;
  try {
    const s = await request("state");
    if (!busy) {
      connectionError = null;
      render(s);
    }
  } catch (error) {
    connectionError = error;
    if (error.code === "EXTENSION_UNAVAILABLE") bridgeReady = false;
    el("toggle").disabled = true;
    message(error.message, true);
    connection();
  }
}
async function act(action, extra = {}) {
  if (busy) return;
  busy = true;
  connectionError = null;
  for (const id of ["toggle", "quality", "scale", "target", "panel"])
    el(id).disabled = true;
  el("toggle").textContent =
    action === "start" ? "Starting enhancement…" : "Applying…";
  if (action === "start")
    message("Starting the local engine and connecting this video…");
  connection();
  try {
    const s = await request(action, extra);
    busy = false;
    render(s);
    await health();
  } catch (error) {
    busy = false;
    connectionError = error;
    if (state) render(state);
    else {
      message(error.message, true);
      connection();
    }
  }
}
el("toggle").addEventListener("click", () =>
  act(state?.active ? "stop" : "start"),
);
for (const id of ["quality", "scale", "target"])
  el(id).addEventListener("change", () =>
    act("settings", {
      settings: Object.fromEntries(
        ["quality", "scale", "target"].map((k) => [k, el(k).value]),
      ),
    }),
  );
el("panel").addEventListener("change", () =>
  act("panel", { visible: el("panel").checked }),
);
el("engine-action").addEventListener("click", async () => {
  if (connectionError?.code === "EXTENSION_UNAVAILABLE" && !preview) {
    chrome.runtime.reload();
    return;
  }
  connectionError = null;
  await Promise.all([health(), refresh()]);
});
el("expand").addEventListener("click", () => {
  if (!preview)
    chrome.tabs.create({
      url: chrome.runtime.getURL(`popup.html?tab=${tabId}`),
    });
});
(async () => {
  if (preview) {
    el("preview-note").hidden = false;
    el("expand").hidden = true;
  } else {
    const fixed = new URLSearchParams(location.search).get("tab");
    tabId = fixed
      ? Number(fixed)
      : (await chrome.tabs.query({ active: true, currentWindow: true }))[0]?.id;
  }
  await Promise.all([health(), refresh()]);
  setInterval(refresh, 1000);
  setInterval(health, 5000);
})().catch((error) => {
  connectionError = error;
  message(error.message, true);
  connection();
});
