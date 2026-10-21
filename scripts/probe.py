"""Real GPU correctness and timing probe, independent of the HTTP layer."""

import json
import time
from pathlib import Path

import cupy as cp
import numpy as np
from PIL import Image, ImageDraw
from nvvfx import VideoSuperRes

ROOT = Path(__file__).resolve().parents[1]


def main():
    image = Image.new("RGB", (640, 360))
    draw = ImageDraw.Draw(image)
    for x in range(640):
        draw.line((x, 0, x, 360), fill=(x * 255 // 640, 90, 255 - x * 255 // 640))
    for i in range(12):
        draw.ellipse(
            (50 + i * 10, 50 + i * 7, 590 - i * 10, 310 - i * 7),
            outline="white",
            width=2,
        )
    draw.text((70, 160), "NVIDIA VSR / RTX 5090 / Linux", fill="white", font_size=28)
    image.save(ROOT / "artifacts/probe-input.png")
    a = cp.asarray(np.asarray(image)).transpose(2, 0, 1).astype(cp.float32) / 255
    a = cp.ascontiguousarray(a)
    cp.cuda.runtime.deviceSynchronize()
    report = {
        "device": cp.cuda.runtime.getDeviceProperties(0)["name"].decode(),
        "modes": {},
    }
    results = {}
    for name in ("BICUBIC", "LOW", "HIGH", "ULTRA"):
        start = time.perf_counter()
        with VideoSuperRes(quality=VideoSuperRes.QualityLevel[name]) as sr:
            sr.output_width, sr.output_height = 1280, 720
            sr.load()
            out = cp.from_dlpack(sr.run(a).image).copy()
            cp.cuda.runtime.deviceSynchronize()
            load_ms = (time.perf_counter() - start) * 1000
            samples = []
            for _ in range(12):
                start = time.perf_counter()
                out = cp.from_dlpack(sr.run(a).image).copy()
                cp.cuda.runtime.deviceSynchronize()
                samples.append((time.perf_counter() - start) * 1000)
            array = cp.asnumpy(out)
            if not np.isfinite(array).all() or array.max() < 0.05 or array.std() < 0.01:
                raise RuntimeError(f"{name} produced invalid/black output")
            results[name] = array
            Image.fromarray(
                np.clip(array.transpose(1, 2, 0) * 255, 0, 255).astype(np.uint8)
            ).save(ROOT / f"artifacts/probe-{name.lower()}.png")
            report["modes"][name] = {
                "first_frame_ms": round(load_ms, 2),
                "median_ms": round(float(np.median(samples)), 2),
                "mean": round(float(array.mean()), 5),
                "std": round(float(array.std()), 5),
            }
    for name in ("LOW", "HIGH", "ULTRA"):
        difference = float(np.abs(results[name] - results["BICUBIC"]).mean())
        if difference < 0.00001:
            raise RuntimeError(f"{name} is indistinguishable from bicubic")
        report["modes"][name]["mean_difference_from_bicubic"] = difference
    (ROOT / "artifacts/probe.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
