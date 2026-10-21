(() => {
  const proto = VsrAudioSync.prototype,
    original = proto.attach,
    update = proto.update;
  proto.update = function (age) {
    return update.call(this, age + (globalThis.__audioStressMs || 0));
  };
  let sync,
    peaks = [],
    pictures = [],
    timer,
    maxInput = 0,
    maxOutput = 0;
  proto.attach = async function (video) {
    await original.call(this, video);
    sync = this;
    const { context, delay, source } = this.graph;
    const input = context.createAnalyser(),
      output = context.createAnalyser();
    input.fftSize = output.fftSize = 2048;
    source.connect(input);
    delay.connect(output);
    const data = new Float32Array(2048);
    let lastIn = -100,
      lastOut = -100;
    timer = setInterval(() => {
      for (const [kind, node] of [
        ["input", input],
        ["output", output],
      ]) {
        node.getFloatTimeDomainData(data);
        const peak = data.reduce((m, x) => Math.max(m, Math.abs(x)), 0);
        if (kind === "input") maxInput = Math.max(maxInput, peak);
        else maxOutput = Math.max(maxOutput, peak);
        let i = data.findIndex((x) => Math.abs(x) > 0.0001);
        if (i < 0) continue;
        const at = context.currentTime - (data.length - i) / context.sampleRate;
        if (at - (kind === "input" ? lastIn : lastOut) < 0.5) continue;
        if (kind === "input") lastIn = at;
        else lastOut = at;
        const ts = context.getOutputTimestamp();
        peaks.push({
          kind,
          contextTime: at,
          wall:
            performance.timeOrigin +
            ts.performanceTime +
            (at - ts.contextTime) * 1000,
        });
      }
    }, 4);
  };
  const p = document.querySelector("#local-nvidia-vsr");
  let prev = -1;
  new MutationObserver(() => {
    const time = Number(p.dataset.presentedMediaTime),
      n = Math.floor(time + 0.0001);
    if (n > prev) {
      prev = n;
      pictures.push({
        second: n,
        mediaTime: time,
        wall: Number(p.dataset.presentedWall),
      });
    }
  }).observe(p, { attributes: true, attributeFilter: ["data-frames"] });
  globalThis.__syncReport = () => ({
    peaks,
    pictures,
    audio: sync?.stats(),
    maxInput,
    maxOutput,
  });
  return "ready";
})();
