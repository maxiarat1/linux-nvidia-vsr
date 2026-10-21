(() => {
  let state;
  const panel = document.querySelector("#local-nvidia-vsr");
  const video = document.querySelector("video");
  let previousDisplay;
  new MutationObserver(() => {
    if (!state) return;
    const timings = JSON.parse(panel.dataset.timings || "{}");
    const audio = JSON.parse(panel.dataset.audio || "{}");
    state.enhanced.push({
      wall: Number(panel.dataset.presentedWall),
      pts: Number(panel.dataset.presentedMediaTime),
      delay: audio.delay_ms,
      age: timings.frame_age_ms,
    });
  }).observe(panel, { attributes: true, attributeFilter: ["data-frames"] });
  const source = (time, metadata) => {
    if (state)
      state.source.push({
        wall: time,
        expected: metadata.expectedDisplayTime,
        pts: metadata.mediaTime,
        count: metadata.presentedFrames,
      });
    video.requestVideoFrameCallback(source);
  };
  video.requestVideoFrameCallback(source);
  const raf = (time) => {
    if (state) {
      state.raf.push(time);
      const display =
        document.querySelector("[data-nvidia-vsr-overlay]")?.style.display ||
        "absent";
      if (display !== previousDisplay)
        state.visibility.push({ wall: time, display });
      previousDisplay = display;
    }
    requestAnimationFrame(raf);
  };
  requestAnimationFrame(raf);
  const stats = (a) => {
    const b = [...a].sort((x, y) => x - y);
    if (!b.length) return null;
    return {
      count: b.length,
      mean: b.reduce((x, y) => x + y, 0) / b.length,
      p50: b[Math.floor(b.length * 0.5)],
      p95: b[Math.floor(b.length * 0.95)],
      p99: b[Math.floor(b.length * 0.99)],
      max: b.at(-1),
    };
  };
  const analyze = (rows, interval) => {
    const gaps = [],
      steps = [],
      jitter = [];
    let skips = 0;
    for (let i = 1; i < rows.length; i++) {
      const gap = rows[i].wall - rows[i - 1].wall,
        step = (rows[i].pts - rows[i - 1].pts) * 1000;
      gaps.push(gap);
      steps.push(step);
      jitter.push(Math.abs(gap - step / video.playbackRate));
      skips += Math.max(0, Math.round(step / interval) - 1);
    }
    return {
      interval_ms: stats(gaps),
      media_step_ms: stats(steps),
      cadence_error_ms: stats(jitter),
      skipped_source_frames: skips,
      over_25ms: gaps.filter((x) => x > 25).length,
      over_40ms: gaps.filter((x) => x > 40).length,
    };
  };
  globalThis.__beginCadence = () => {
    state = { source: [], enhanced: [], raf: [], visibility: [] };
  };
  globalThis.__endCadence = () => {
    const rows = state.source,
      steps = rows
        .slice(1)
        .map((x, i) => (x.pts - rows[i].pts) * 1000)
        .sort((x, y) => x - y);
    const interval = steps[Math.floor(steps.length * 0.5)] || 1000 / 60;
    const result = {
      source: analyze(rows, interval),
      enhanced: analyze(state.enhanced, interval),
      raf: stats(state.raf.slice(1).map((x, i) => x - state.raf[i])),
      visibility: state.visibility,
      raw: state,
    };
    state = null;
    return result;
  };
})();
