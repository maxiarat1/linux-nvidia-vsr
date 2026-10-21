let claimed = false;
let worker;
window.addEventListener("message", async (event) => {
  if (
    claimed ||
    event.source !== parent ||
    event.data?.type !== "vsr-init" ||
    event.ports.length !== 1
  )
    return;
  claimed = true;
  const port = event.ports[0];
  try {
    const auth = await chrome.runtime.sendMessage({
      type: "vsr-claim",
      grant: event.data.grant,
    });
    if (!auth?.token)
      throw new Error(auth?.error || "VSR authorization failed");
    worker = new Worker(chrome.runtime.getURL("frame-worker.js"));
    let canvas;
    if (event.data.workerRendering) {
      document.documentElement.style.cssText =
        "height:100%;background:transparent";
      document.body.style.cssText =
        "margin:0;height:100%;overflow:hidden;background:transparent";
      const element = document.createElement("canvas");
      element.style.cssText =
        "display:block;width:100%;height:100%;object-fit:contain";
      element.style.objectFit = event.data.objectFit || "contain";
      document.body.append(element);
      canvas = element.transferControlToOffscreen();
    }
    worker.onerror = (event) =>
      port.postMessage({ type: "error", message: event.message });
    worker.postMessage(
      {
        type: "init",
        port,
        token: auth.token,
        quality: event.data.quality,
        scale: event.data.scale,
        format: event.data.format,
        canvas,
      },
      canvas ? [port, canvas] : [port],
    );
  } catch (error) {
    port.postMessage({ type: "error", message: error.message });
  }
});
window.addEventListener("pagehide", () => worker?.terminate());
