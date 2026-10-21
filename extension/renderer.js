// Runs beside the extension socket, with a canvas owned by the visible bridge.
// GPU decoder frames never cross an origin/agent-cluster boundary.
class VsrWorkerRenderer {
  constructor(canvas, send) {
    this.canvas = canvas;
    this.context = canvas.getContext("2d", { alpha: true });
    this.send = send;
    this.pictures = [];
    this.decodes = new Map();
    this.sequence = 0;
    this.epoch = 0;
    this.clock = null;
    this.decoder = null;
    this.key = "";
    this.lastPaint = 0;
    this.lastRaf = performance.now();
    this.handle = requestAnimationFrame((time) => this.present(time));
  }
  reset(epoch) {
    this.epoch = epoch;
    this.clock = null;
    for (const p of this.pictures) p.image.close();
    this.pictures.length = 0;
    this.context.clearRect(0, 0, this.canvas.width, this.canvas.height);
    this.lastPaint = 0;
  }
  updateClock(clock) {
    if (clock.epoch !== this.epoch) this.reset(clock.epoch);
    this.clock = clock;
    // Chromium can suspend an iframe's pending worker animation callback
    // while the overlay is hidden (pause/comparison/fullscreen transition).
    // Re-arm it after visibility returns instead of waiting on a lost tick.
    if (clock.visible && performance.now() - this.lastRaf > 100) {
      cancelAnimationFrame(this.handle);
      this.handle = requestAnimationFrame((t) => this.present(t));
    }
  }
  async decode(bytes, stats, meta, socketMs, receivedWall) {
    const finish = (image) => {
      const decodedWall = performance.timeOrigin + performance.now();
      const p = {
        image,
        stats,
        ...meta,
        readyWall: decodedWall,
        timings: {
          capture_ms: meta.captureMs,
          input_handoff_ms: meta.inputHandoffMs,
          encoder_handoff_ms: meta.encoderHandoffMs,
          encode_ms: meta.encodeMs,
          socket_ms: socketMs,
          decoder_handoff_ms: 0,
          decode_ms: decodedWall - receivedWall,
          service_ms: stats.service_ms,
          inference_ms: stats.inference_ms,
          input_decode_ms: stats.input_decode_ms,
          upload_ms: stats.upload_ms,
          output_encode_ms: stats.output_encode_ms,
          frame_age_ms: decodedWall - meta.captureStart,
        },
      };
      this.send({
        type: "decoded",
        stats,
        meta,
        timings: p.timings,
        decodedWall,
      });
      if (meta.epoch !== this.epoch || !this.clock?.visible) {
        image.close();
        return;
      }
      this.pictures.push(p);
      this.pictures.sort((a, b) => a.mediaTime - b.mediaTime);
      if (
        this.pictures.length > 32 ||
        this.pictures.reduce(
          (n, x) => n + x.stats.width * x.stats.height * 4,
          0,
        ) >
          1024 * 1024 * 1024
      )
        this.send({
          type: "error",
          message: "Audio output buffering exceeds the picture memory budget.",
        });
    };
    if (stats.format !== "av1") {
      const d = new ImageDecoder({ data: bytes, type: "image/jpeg" });
      try {
        const { image } = await d.decode();
        finish(image);
      } finally {
        d.close();
      }
      return;
    }
    const key = `${stats.codec}/${stats.width}/${stats.height}`;
    if (key !== this.key) {
      this.decoder?.close();
      this.decodes.clear();
      this.decoder = new VideoDecoder({
        output: (frame) => {
          const done = this.decodes.get(frame.timestamp);
          this.decodes.delete(frame.timestamp);
          if (done) done(frame);
          else frame.close();
        },
        error: (error) =>
          this.send({
            type: "error",
            message: `Hardware video decoding failed: ${error.message}`,
          }),
      });
      this.decoder.configure({
        codec: stats.codec,
        codedWidth: stats.width,
        codedHeight: stats.height,
        hardwareAcceleration: "prefer-hardware",
        optimizeForLatency: true,
      });
      this.key = key;
    }
    const timestamp = ++this.sequence;
    this.decodes.set(timestamp, finish);
    this.decoder.decode(
      new EncodedVideoChunk({ type: "key", timestamp, data: bytes }),
    );
  }
  present(time) {
    this.lastRaf = performance.now();
    this.handle = requestAnimationFrame((t) => this.present(t));
    const now = performance.timeOrigin + performance.now(),
      c = this.clock;
    if (!c?.visible) return;
    const audible =
      ((performance.timeOrigin + time - c.phaseWall) * c.rate) / 1000 -
      (c.latencyMs * c.rate) / 1000;
    let selected;
    while (this.pictures.length && this.pictures[0].mediaTime <= audible) {
      selected?.image.close();
      selected = this.pictures.shift();
    }
    if (!selected) {
      if (this.lastPaint && now - this.lastPaint > 500) {
        this.context.clearRect(0, 0, this.canvas.width, this.canvas.height);
        this.lastPaint = 0;
      }
      return;
    }
    const p = selected;
    if (p.epoch !== this.epoch || now - p.captureStart > 500) {
      p.image.close();
      return;
    }
    if (
      this.canvas.width !== p.stats.width ||
      this.canvas.height !== p.stats.height
    ) {
      this.canvas.width = p.stats.width;
      this.canvas.height = p.stats.height;
    }
    const begin = performance.now();
    this.context.drawImage(p.image, 0, 0);
    p.image.close();
    this.lastPaint = now;
    Object.assign(p.timings, {
      canvas_submit_ms: performance.now() - begin,
      raf_lateness_ms: begin - time,
      presentation_wait_ms: now - p.readyWall,
      presentation_age_ms: now - p.captureStart,
      estimated_av_error_ms: ((audible - p.mediaTime) * 1000) / c.rate,
      audio_path_ms: c.latencyMs,
      reported_audio_path_ms: c.reportedLatencyMs,
    });
    this.send({
      type: "presented",
      stats: p.stats,
      epoch: p.epoch,
      mediaTime: p.mediaTime,
      presentedWall: now,
      timings: p.timings,
    });
  }
  close() {
    cancelAnimationFrame(this.handle);
    this.reset(this.epoch);
    this.decoder?.close();
    this.decodes.clear();
  }
}
