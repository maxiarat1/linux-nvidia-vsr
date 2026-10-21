// Only small authorization/control messages cross chrome.runtime. Video frames
// use transferable objects through the extension frame's MessageChannel.
importScripts("config.js");
const grants = new Map();
chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  const tab = sender.tab?.id;
  if (msg.type === "vsr-grant" && tab !== undefined && sender.frameId === 0) {
    const now = Date.now();
    for (const [key, value] of grants)
      if (value.expires < now) grants.delete(key);
    const grant = crypto.randomUUID();
    grants.set(grant, { tab, expires: now + 15000 });
    reply({ grant, protocol: 2 });
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
  dashboard(msg).then(reply, (error) => reply({ error: error.message }));
  return true;
});

async function dashboard(msg) {
  if (msg.action === "health") {
    const response = await fetch("http://127.0.0.1:8730/health", {
      signal: AbortSignal.timeout(2000),
      cache: "no-store",
    });
    if (!response.ok) throw new Error("Engine health check failed");
    return response.json();
  }
  if (!Number.isInteger(msg.tabId))
    throw new Error("Select a video tab first.");
  try {
    return await chrome.tabs.sendMessage(msg.tabId, {
      ...msg,
      type: "vsr-control",
    });
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
  return chrome.tabs.sendMessage(msg.tabId, { ...msg, type: "vsr-control" });
}
