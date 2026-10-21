// One graph per element: browsers do not allow detaching/recreating a
// MediaElementAudioSourceNode. Bypass keeps the graph alive with zero delay.
globalThis.VsrAudioSync ||= class VsrAudioSync {
  constructor() {
    this.graphs = new WeakMap();
    this.graph = null;
    this.samples = [];
    this.latencySamples = [];
    this.latency = null;
    this.latencySampledAt = -Infinity;
  }
  async attach(video) {
    this.bypass();
    let graph = this.graphs.get(video);
    if (!graph) {
      // Check actual origin cleanliness before irreversibly routing audio.
      // A CORS-restricted MediaElementAudioSourceNode would output silence.
      const canvas = document.createElement("canvas");
      canvas.width = canvas.height = 1;
      const ctx = canvas.getContext("2d");
      ctx.drawImage(video, 0, 0, 1, 1);
      ctx.getImageData(0, 0, 1, 1);
      const context = new AudioContext({ latencyHint: "interactive" });
      try {
        const source = context.createMediaElementSource(video);
        const delay = context.createDelay(1);
        source.connect(delay).connect(context.destination);
        graph = { context, source, delay, video };
        this.graphs.set(video, graph);
      } catch (error) {
        await context.close();
        throw error;
      }
    }
    if (this.graph !== graph) {
      this.latencySamples = [];
      this.latency = null;
      this.latencySampledAt = -Infinity;
    }
    this.graph = graph;
    this.samples = [];
    await graph.context.resume();
    if (graph.context.state !== "running")
      throw new Error("Audio needs a playback gesture.");
  }
  update(mediaAgeMs) {
    const g = this.graph;
    if (!g || !Number.isFinite(mediaAgeMs)) return;
    if (
      this.latency === null &&
      !(g.context.outputLatency > 0) &&
      !g.context.getOutputTimestamp?.().contextTime
    )
      return;
    this.samples.push(Math.max(0, Math.min(500, mediaAgeMs)));
    if (this.samples.length > 45) this.samples.shift();
    const sorted = [...this.samples].sort((a, b) => a - b);
    // The media element's clock is at the graph input. Account for the
    // graph/device output latency before adding any further audio delay.
    const target = Math.max(
      0,
      sorted[Math.floor(sorted.length * 0.9)] / 1000 +
        0.008 -
        this.outputLatency(),
    );
    // Smooth delay changes, rather than changing the audio clock per frame.
    if (Math.abs(target - (this.target || 0)) > 0.002) {
      this.target = target;
      g.delay.delayTime.setTargetAtTime(target, g.context.currentTime, 0.08);
    }
  }
  outputLatency() {
    const c = this.graph?.context;
    if (!c) return 0;
    // Chromium initially reports zero until the device clock is available.
    // Do not turn that missing estimate into a long, slowly corrected bias.
    if (!(c.outputLatency > 0)) return this.latency ?? c.baseLatency;
    const measured = c.baseLatency + (c.outputLatency || 0),
      now = performance.now();
    if (now - this.latencySampledAt < 10) return this.latency;
    const elapsed = (now - this.latencySampledAt) / 1000;
    this.latencySampledAt = now;
    if (this.latency === null || Math.abs(measured - this.latency) > 0.08) {
      this.latency = measured;
      this.latencySamples = [];
    }
    this.latencySamples.push(measured);
    if (this.latencySamples.length > 31) this.latencySamples.shift();
    const sorted = [...this.latencySamples].sort((a, b) => a - b),
      target = sorted[Math.floor(sorted.length / 2)];
    // Device estimates can move by an audio block. Follow drift rather than
    // moving video deadlines back and forth with each individual estimate.
    const step = elapsed * 0.005;
    this.latency += Math.max(-step, Math.min(step, target - this.latency));
    return this.latency;
  }
  audibleTime(video, mediaTime = video.currentTime) {
    return (
      mediaTime -
      (this.outputLatency() + (this.graph?.delay.delayTime.value || 0)) *
        video.playbackRate
    );
  }
  bypass() {
    if (this.graph) {
      const { context, delay } = this.graph;
      delay.delayTime.cancelScheduledValues(context.currentTime);
      delay.delayTime.setTargetAtTime(0, context.currentTime, 0.005);
    }
    this.target = 0;
    this.samples = [];
  }
  stats() {
    const g = this.graph;
    return g
      ? {
          state: g.context.state,
          delay_ms: g.delay.delayTime.value * 1000,
          target_ms: (this.target || 0) * 1000,
          base_latency_ms: g.context.baseLatency * 1000,
          output_latency_ms: (g.context.outputLatency || 0) * 1000,
          filtered_output_path_ms: this.outputLatency() * 1000,
        }
      : { state: "unattached", delay_ms: 0 };
  }
};
