// A dedicated encoder keeps input capture independent of output JPEG decoding.
let canvas, ctx;
self.onmessage = async (event) => {
  const { frame, meta } = event.data;
  meta.inputHandoffMs =
    performance.timeOrigin + performance.now() - meta.capturedWall;
  try {
    if (
      !canvas ||
      canvas.width !== frame.width ||
      canvas.height !== frame.height
    ) {
      canvas = new OffscreenCanvas(frame.width, frame.height);
      ctx = canvas.getContext("2d", { alpha: false, willReadFrequently: true });
    }
    const t = performance.now();
    ctx.drawImage(frame, 0, 0);
    frame.close();
    const blob = await canvas.convertToBlob({
      type: "image/jpeg",
      quality: 0.95,
    });
    const bytes = await blob.arrayBuffer();
    self.postMessage(
      {
        type: "encoded",
        bytes,
        meta,
        encodeMs: performance.now() - t,
        encodedWall: performance.timeOrigin + performance.now(),
      },
      [bytes],
    );
  } catch (error) {
    frame.close();
    self.postMessage({ type: "error", message: error.message });
  }
};
