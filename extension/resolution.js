// Target sizes are bounds; preserve the source aspect ratio and orientation.
globalThis.VsrResolution ||= {
  targets: {
    720: [1280, 720],
    1080: [1920, 1080],
    1440: [2560, 1440],
    2160: [3840, 2160],
    4320: [7680, 4320],
  },
  resolve(width, height, selection, target) {
    if (!(width > 0 && height > 0)) return null;
    let scale = Number(selection),
      bypass = false;
    if (selection === "auto") {
      let [tw, th] = this.targets[target] || this.targets["2160"];
      if (height > width) [tw, th] = [th, tw];
      scale = Math.min(tw / width, th / height);
      bypass = scale < 1 - 0.000001;
      scale = Math.max(1, scale);
    }
    if (!Number.isFinite(scale) || scale < 1)
      throw new Error("Invalid upscale factor");
    return {
      scale,
      bypass,
      width: Math.round((width * scale) / 8) * 8,
      height: Math.round((height * scale) / 8) * 8,
    };
  },
  factor(scale) {
    return `${Number(scale.toFixed(3))}×`;
  },
};
