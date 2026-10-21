// The coordinator only moves transferable objects and handles the socket.
// Input encoding has its own thread. The page bounds the processing pipeline to
// four outstanding frames, so a slow stage cannot grow an unbounded queue.
importScripts("renderer.js");
let port,
  ws,
  stats,
  stopped = false,
  keepalive,
  encoder,
  encoding = false,
  completed = 0,
  renderer = null;
const captures = [],
  requests = [];
const wall = () => performance.timeOrigin + performance.now();
const send = (msg, transfers = []) => {
  if (!stopped) port.postMessage(msg, transfers);
  else msg.bitmap?.close();
};
const stage = (value) => {
  if (!completed) send({ type: "stage", stage: value });
};
function stop() {
  stopped = true;
  renderer?.close();
  clearInterval(keepalive);
  for (const msg of captures) msg.frame.close();
  captures.length = 0;
  encoder?.terminate();
  ws?.close();
  port?.close();
  self.close();
}
function encodeNext() {
  if (encoding || stopped || !captures.length) return;
  encoding = true;
  const msg = captures.shift();
  stage("encoding");
  encoder.postMessage(
    {
      frame: msg.frame,
      meta: {
        epoch: msg.epoch,
        mediaTime: msg.mediaTime,
        capturedWall: msg.capturedWall,
        captureStart: msg.captureStart,
        captureMs: msg.captureMs,
        encoderPosted: wall(),
      },
    },
    [msg.frame],
  );
}
self.onmessage = (event) => {
  const config = event.data;
  port = config.port;
  if (config.canvas) renderer = new VsrWorkerRenderer(config.canvas, send);
  encoder = new Worker("encode-worker.js");
  encoder.onerror = (e) => send({ type: "error", message: e.message });
  encoder.onmessage = (event) => {
    const msg = event.data;
    if (msg.type === "error") {
      send(msg);
      return;
    }
    if (stopped) return;
    requests.push({
      ...msg.meta,
      encodeMs: msg.encodeMs,
      socketSent: performance.now(),
      encoderHandoffMs: wall() - msg.encodedWall,
    });
    ws.send(msg.bytes);
    encoding = false;
    encodeNext();
    stage("waiting-for-engine");
  };
  port.onmessageerror = () =>
    send({ type: "error", message: "Browser blocked a transferred image" });
  port.onmessage = (event) => {
    const msg = event.data;
    if (msg.type === "render-clock") {
      renderer?.updateClock(msg);
      return;
    }
    if (msg.type === "render-reset") {
      renderer?.reset(msg.epoch);
      return;
    }
    if (msg.type === "stop") {
      stop();
      return;
    }
    if (msg.type === "raw-frame") {
      if (ws?.readyState !== WebSocket.OPEN) {
        send({ type: "error", message: "Engine socket not ready" });
        return;
      }
      requests.push({
        epoch: msg.epoch,
        mediaTime: msg.mediaTime,
        capturedWall: msg.capturedWall,
        captureStart: msg.captureStart,
        captureMs: msg.captureMs,
        encodeMs: 0,
        inputHandoffMs: wall() - msg.capturedWall,
        encoderHandoffMs: 0,
        socketSent: performance.now(),
      });
      ws.send(msg.bytes);
      return;
    }
    if (msg.type !== "frame") return;
    if (
      ws?.readyState !== WebSocket.OPEN ||
      captures.length + requests.length > 4
    ) {
      msg.frame.close();
      send({ type: "error", message: "Unexpected capture backlog" });
      return;
    }
    captures.push(msg);
    encodeNext();
  };
  port.start();
  ws = new WebSocket("ws://127.0.0.1:8730/v1/stream");
  ws.binaryType = "arraybuffer";
  ws.onopen = () => {
    ws.send(
      JSON.stringify({
        token: config.token,
        quality: config.quality,
        scale: config.scale,
        format: config.format,
      }),
    );
    keepalive = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) ws.send("ping");
    }, 20000);
  };
  ws.onmessage = (event) => {
    if (typeof event.data === "string") {
      const message = JSON.parse(event.data);
      if (message.type === "frame") stats = message;
      else if (message.type !== "pong") send(message);
      return;
    }
    const request = requests.shift();
    if (!request) {
      send({ type: "error", message: "Engine response without a capture" });
      return;
    }
    stage("decoding-output");
    completed++;
    if (renderer) {
      renderer
        .decode(
          event.data,
          stats,
          request,
          performance.now() - request.socketSent,
          wall(),
        )
        .catch((error) => send({ type: "error", message: error.message }));
      return;
    }
    send(
      {
        type: "encoded-result",
        bytes: event.data,
        stats,
        meta: request,
        socketMs: performance.now() - request.socketSent,
        receivedWall: wall(),
      },
      [event.data],
    );
  };
  ws.onerror = () =>
    send({
      type: "error",
      message: "Cannot reach local engine. Run ./vsr start.",
    });
  ws.onclose = () => send({ type: "closed" });
};
