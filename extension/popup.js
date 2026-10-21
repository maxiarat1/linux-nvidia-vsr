/* Extension toolbar controls; also runs without Chrome APIs as a UI example. */
const el = (id) => document.getElementById(id);
const preview =
  !globalThis.chrome?.runtime?.id ||
  new URLSearchParams(location.search).has("preview");
let tabId,
  state,
  online = false,
  busy = false;
let sample = {
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
  status: "Sample preview · NVIDIA AI, GPU AV1 and synchronized audio.",
};
async function request(action, extra = {}) {
  if (preview) {
    if (action === "health") return { ready: true };
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
    return { ...sample };
  }
  const result = await chrome.runtime.sendMessage({
    type: "vsr-dashboard",
    action,
    tabId,
    ...extra,
  });
  if (!result)
    throw new Error(
      "Extension background unavailable. Reload the extension and video tab.",
    );
  if (result.error) throw new Error(result.error);
  return result;
}
function message(text, error = false) {
  el("message").textContent = text;
  el("message").classList.toggle("error", error);
}
function render(s) {
  state = s;
  for (const id of ["quality", "scale", "target"]) el(id).value = s[id];
  el("target").disabled = s.scale !== "auto" || busy;
  el("quality").disabled = el("scale").disabled = busy;
  el("panel").checked = s.panelVisible;
  el("panel").disabled = busy;
  el("toggle").disabled =
    busy || s.starting || !s.video || (!online && !s.active);
  el("toggle").textContent = s.starting
    ? "Starting NVIDIA VSR…"
    : s.active
      ? "Turn off enhancement"
      : "Enable enhancement ↗";
  el("toggle").classList.toggle("on", s.active);
  el("toggle").setAttribute("aria-pressed", String(s.active));
  el("state-tag").textContent = s.starting
    ? "STARTING"
    : s.active
      ? s.bypass
        ? "ORIGINAL"
        : "LIVE"
      : "STANDBY";
  el("state-title").textContent = s.active
    ? s.bypass
      ? "Source above target"
      : "A clearer view, in real time"
    : s.video
      ? "Ready for a clearer picture"
      : "Play a video to begin";
  el("summary").textContent = s.video
    ? `${s.sourceWidth} × ${s.sourceHeight} → ${s.outputWidth} × ${s.outputHeight}`
    : "Select a video tab, then open these controls.";
  el("factor").textContent = `${s.scale === "auto" ? "AUTO · " : ""}${Number(
    s.effectiveScale || 1,
  )
    .toFixed(2)
    .replace(/\.?0+$/, "")}×`;
  el("resolution-note").textContent = s.bypass
    ? "Source exceeds target. Original video is shown."
    : "Aspect ratio preserved · dimensions aligned to 8 pixels";
  const live = s.active && !s.bypass;
  el("fps").replaceChildren(
    document.createTextNode(live ? Number(s.fps || 0).toFixed(1) : "—"),
    unit(" fps"),
  );
  el("latency").replaceChildren(
    document.createTextNode(live ? Math.round(s.processingMs || 0) : "—"),
    unit(" ms"),
  );
  el("audio").replaceChildren(
    document.createTextNode(live ? Math.round(s.audio?.delay_ms || 0) : "—"),
    unit(" ms"),
  );
  el("latency").title =
    "Capture, processing and return decoding; excludes intentional wait for audio.";
  message(
    s.error ||
      (s.active
        ? s.status
        : "Enhancement stays off until you enable it for this video."),
    !!s.error,
  );
}
function unit(text) {
  const s = document.createElement("small");
  s.textContent = text;
  return s;
}
async function health() {
  try {
    const h = await request("health");
    online = !!h.ready;
  } catch {
    online = false;
  }
  el("engine").textContent = online
    ? "Local engine connected"
    : "Local engine offline";
  el("engine-dot").classList.toggle("online", online);
  el("help").hidden = online;
  if (state && !busy) render(state);
}
async function refresh() {
  if (busy) return;
  try {
    const s = await request("state");
    if (!busy) render(s);
  } catch (error) {
    el("toggle").disabled = true;
    message(error.message, true);
  }
}
async function act(action, extra = {}) {
  if (busy) return;
  busy = true;
  for (const id of ["toggle", "quality", "scale", "target", "panel"])
    el(id).disabled = true;
  try {
    const s = await request(action, extra);
    busy = false;
    render(s);
  } catch (error) {
    busy = false;
    if (state) render(state);
    message(error.message, true);
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
})().catch((error) => message(error.message, true));
