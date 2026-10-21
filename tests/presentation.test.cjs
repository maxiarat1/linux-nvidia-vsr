const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
vm.runInThisContext(fs.readFileSync("extension/popup-state.js", "utf8"));
test("Live means enhanced frames are visible, not merely engine connected", () => {
  const s = {
    video: true,
    active: true,
    starting: true,
    tabVisible: true,
    presenting: false,
  };
  assert.equal(VsrPopupState(s).kind, "starting");
  assert.equal(VsrPopupState({ ...s, starting: false }).kind, "waiting");
  assert.equal(
    VsrPopupState({ ...s, starting: false, presenting: true }).kind,
    "live",
  );
  assert.equal(VsrPopupState({ ...s, paused: true }).kind, "paused");
  assert.equal(VsrPopupState({ ...s, tabVisible: false }).kind, "hidden");
  assert.equal(VsrPopupState({ ...s, bypass: true }).kind, "original");
  assert.equal(
    VsrPopupState({ ...s, active: false, starting: false }).tag,
    "OFF",
  );
});
for (const name of ["resolution", "presentation-clock"])
  vm.runInThisContext(fs.readFileSync(`extension/${name}.js`, "utf8"));

test("Auto matches target dimensions, orientation and aspect ratio", () => {
  for (const [w, h, factor, ow, oh] of [
    [1920, 1080, 2, 3840, 2160],
    [2560, 1440, 1.5, 3840, 2160],
    [1280, 720, 3, 3840, 2160],
    [640, 360, 6, 3840, 2160],
    [1080, 1920, 2, 2160, 3840],
    [3840, 2160, 1, 3840, 2160],
  ]) {
    const s = VsrResolution.resolve(w, h, "auto", "2160");
    assert.deepEqual(
      [s.scale, s.width, s.height, s.bypass],
      [factor, ow, oh, false],
    );
  }
  assert.equal(VsrResolution.resolve(7680, 4320, "auto", "2160").bypass, true);
  const wide = VsrResolution.resolve(3440, 1440, "auto", "2160");
  assert.deepEqual([wide.width, wide.height], [3840, 1608]);
});

test("media clock filters time-read jitter without frame-to-frame jumps", () => {
  const clock = new VsrPresentationClock();
  const noise = [-0.004, 0.004, -0.002, 0.002, 0];
  let previous;
  for (let tick = 0; tick < 1200; tick++) {
    const now = (tick * 1000) / 240;
    if (tick % 4 === 0) {
      const frame = tick / 4;
      clock.observe({}, 1, 2 + now / 1000 + noise[frame % noise.length], now);
    }
    const at = clock.mediaTime(now);
    if (tick > 240) {
      assert.ok(Math.abs(at - (2 + now / 1000)) < 0.003);
      assert.ok(Math.abs(at - previous - 1 / 240) < 0.000051);
    }
    previous = at;
  }
  clock.reset();
  assert.equal(clock.mediaTime(9000), null);
  clock.observe({}, 2, 20, 9000);
  assert.equal(clock.mediaTime(9500), 21);
});

test("audio-device estimate changes cannot jump video deadlines by a block", () => {
  vm.runInThisContext(fs.readFileSync("extension/audio-sync.js", "utf8"));
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, "performance");
  let now = 0;
  Object.defineProperty(globalThis, "performance", {
    configurable: true,
    value: { now: () => now },
  });
  try {
    const sync = new VsrAudioSync(),
      context = { baseLatency: 0.01, outputLatency: 0 };
    sync.graph = { context };
    assert.equal(sync.outputLatency(), 0.01);
    now += 16;
    context.outputLatency = 0.048;
    let previous = sync.outputLatency();
    assert.ok(Math.abs(previous - 0.058) < 1e-9);
    for (let i = 0; i < 120; i++) {
      now += 16;
      context.outputLatency = i % 2 ? 0.048 : 0.04;
      const current = sync.outputLatency();
      assert.ok(Math.abs(current - previous) <= 0.000081);
      previous = current;
    }
    now += 16;
    context.outputLatency = 0.2;
    assert.ok(Math.abs(sync.outputLatency() - 0.21) < 1e-9);
  } finally {
    Object.defineProperty(globalThis, "performance", descriptor);
  }
});
