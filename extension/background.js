// Only small authorization/control messages cross chrome.runtime. Video frames
// use transferable objects through the extension frame's MessageChannel.
importScripts("config.js");
const grants = new Map();
let engineStarting;
async function engineHealth() {
  try {
    const response = await fetch("http://127.0.0.1:8730/health", {
      signal: AbortSignal.timeout(1500),
      cache: "no-store",
    });
    const info = response.ok && (await response.json());
    return info?.engine === "NVIDIA VideoSuperRes" ? info : { ready: false };
  } catch {
    return { ready: false };
  }
}
async function ensureEngine() {
  if ((await engineHealth()).ready) return;
  if (!engineStarting)
    engineStarting = (async () => {
      let result;
      try {
        result = await chrome.runtime.sendNativeMessage(
          "com.local_nvidia_vsr.engine",
          { action: "ensure" },
        );
      } catch (error) {
        throw Object.assign(
          new Error(
            "Browser helper unavailable. Complete the browser-helper setup, then retry.",
          ),
          { code: "HELPER_SETUP_REQUIRED" },
        );
      }
      if (result?.error || !result?.ready)
        throw Object.assign(
          new Error(result?.error || "Local engine could not start."),
          { code: result?.code || "ENGINE_START_FAILED" },
        );
    })().finally(() => {
      engineStarting = null;
    });
  await engineStarting;
}
chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  const tab = sender.tab?.id;
  if (msg.type === "vsr-grant" && tab !== undefined && sender.frameId === 0) {
    ensureEngine().then(
      () => {
        const now = Date.now();
        for (const [key, value] of grants)
          if (value.expires < now) grants.delete(key);
        const grant = crypto.randomUUID();
        grants.set(grant, { tab, expires: now + 15000 });
        reply({ grant, protocol: 2 });
      },
      (error) => reply({ error: error.message, code: error.code }),
    );
    return true;
  } else if (
    msg.type === "vsr-claim" &&
    sender.url === chrome.runtime.getURL("bridge.html")
  ) {
    const value = grants.get(msg.grant);
    grants.delete(msg.grant);
    reply(
      value?.tab === tab && value.expires > Date.now()
        ? { token: VSR_CONFIG.token }
        : { error: "VSR bridge authorization expired" },
    );
  }
});
// Only extension-owned controls can request tab operations. The local token
// stays in this worker and the authorized bridge, never in a page or popup.
chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  if (
    msg.type !== "vsr-dashboard" ||
    sender.url?.split("?")[0] !== chrome.runtime.getURL("popup.html")
  )
    return;
  dashboard(msg).then(reply, (error) =>
    reply({ error: error.message, code: error.code }),
  );
  return true;
});

async function dashboard(msg) {
  if (msg.action === "health") {
    return engineHealth();
  }
  if (msg.action === "engine-start") {
    await ensureEngine();
    return engineHealth();
  }
  if (!Number.isInteger(msg.tabId))
    throw new Error("Select a video tab first.");
  try {
    return await chrome.tabs.sendMessage(
      msg.tabId,
      {
        ...msg,
        type: "vsr-control",
      },
      { frameId: 0 },
    );
  } catch (error) {
    if (
      !/Receiving end does not exist|Could not establish connection/.test(
        error.message,
      )
    )
      throw error;
  }
  await chrome.scripting.executeScript({
    target: { tabId: msg.tabId },
    files: [
      "audio-sync.js",
      "presentation-clock.js",
      "resolution.js",
      "content.js",
    ],
  });
  return chrome.tabs.sendMessage(
    msg.tabId,
    { ...msg, type: "vsr-control" },
    { frameId: 0 },
  );
}
