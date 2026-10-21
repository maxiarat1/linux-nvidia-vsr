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
let manualScale = "2";
const dashboard = new URLSearchParams(location.search).has("tab");
document.body.classList.toggle("dashboard", dashboard);
const sample = {
  video: true,
  active: false,
  starting: false,
  presenting: false,
  firstFrameMs: 350,
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
      sample.presenting = sample.active = action === "start";
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
  el("feedback").hidden =
    !error &&
    !busy &&
    !dashboard &&
    ![
      "empty",
      "paused",
      "hidden",
      "starting",
      "waiting",
      "original",
      "compare",
    ].includes(VsrPopupState(state || {}).kind);
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
  const view = VsrPopupState(s);
  for (const id of ["quality", "scale", "target"]) el(id).value = s[id];
  el("target").disabled = s.scale !== "auto" || busy;
  el("quality").disabled = el("scale").disabled = busy;
  const auto = s.scale === "auto";
  if (!auto) manualScale = String(s.scale);
  el("target-row").hidden = !auto;
  el("scale-row").hidden = auto;
  for (const mode of ["auto", "manual"]) {
    el(`mode-${mode}`).disabled = busy;
    el(`mode-${mode}`).setAttribute(
      "aria-pressed",
      String((mode === "auto") === auto),
    );
  }
  el("panel").checked = s.panelVisible;
  el("panel").disabled = busy;
  // An offline engine is normal: the native helper starts it on Enable.
  el("toggle").disabled = busy || !s.video || !bridgeReady;
  const toggleLabel = s.starting
    ? "Cancel start"
    : s.active
      ? "Turn off enhancement"
      : "Enable for this video";
  el("toggle").setAttribute("aria-label", toggleLabel);
  el("toggle").title = toggleLabel;
  el("toggle").classList.toggle("on", s.active);
  el("toggle").setAttribute("aria-checked", String(s.active));
  el("state-tag").textContent = view.tag;
  el("state-tag").classList.toggle("live", view.kind === "live");
  el("state-title").textContent = view.title;
  el("summary").textContent = s.video
    ? view.kind === "live"
      ? `${s.sourceWidth} × ${s.sourceHeight} → ${s.outputWidth} × ${s.outputHeight}`
      : `${s.sourceWidth} × ${s.sourceHeight} · Original video`
    : "Play a video in this tab, then reopen these controls.";
  el("factor").textContent = `${s.scale === "auto" ? "AUTO · " : ""}${Number(
    s.effectiveScale || 1,
  )
    .toFixed(2)
    .replace(/\.?0+$/, "")}×`;
  el("resolution-note").textContent = s.bypass
    ? "Source exceeds the target. The original video is shown."
    : "Aspect ratio is preserved.";
  const live = view.kind === "live";
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
      (view.kind === "paused"
        ? "Resume the video to see enhanced frames. The original is shown while paused."
        : view.kind === "hidden"
          ? "Enhancement waits while the video tab is hidden. Return to that tab."
          : s.starting
            ? "Starting the local engine and connecting this video…"
            : s.active
              ? s.bypass
                ? "The source is already above your selected target."
                : live
                  ? `Enhanced frames are visible.${s.firstFrameMs != null ? ` First frame after ${(s.firstFrameMs / 1000).toFixed(2)} s.` : ""}`
                  : "Connected, but no fresh enhanced frame is visible yet."
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
  for (const id of [
    "toggle",
    "quality",
    "scale",
    "target",
    "panel",
    "mode-auto",
    "mode-manual",
  ])
    el(id).disabled = true;
  el("toggle").setAttribute(
    "aria-label",
    action === "start" ? "Starting enhancement…" : "Applying…",
  );
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
for (const mode of ["auto", "manual"])
  el(`mode-${mode}`).addEventListener("click", () => {
    if (busy || !state) return;
    const scale = mode === "auto" ? "auto" : manualScale;
    if (scale === state.scale) return;
    act("settings", {
      settings: { quality: state.quality, target: state.target, scale },
    });
  });
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
    el("expand").disabled = true;
  } else {
    const fixed = new URLSearchParams(location.search).get("tab");
    tabId = fixed
      ? Number(fixed)
      : (await chrome.tabs.query({ active: true, currentWindow: true }))[0]?.id;
  }
  await Promise.all([health(), refresh()]);
  setInterval(refresh, 250);
  setInterval(health, 5000);
})().catch((error) => {
  connectionError = error;
  message(error.message, true);
  connection();
});
