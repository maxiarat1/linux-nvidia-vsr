import io
import json
import statistics
import time
from pathlib import Path

import cupy as cp
import numpy as np
from PIL import Image
from vsr_service.gpu_jpeg import GpuJpeg

root = Path(__file__).resolve().parents[1]
rows = []
encoder = GpuJpeg()
try:
    for w, h in ((1280, 720), (2560, 1440), (3840, 2160)):
        image = Image.open(root / "artifacts/probe-input.png").resize((w, h))
        rgb = cp.asarray(np.asarray(image))
        cp.cuda.get_current_stream().synchronize()
        times = []
        for i in range(20):
            t = time.perf_counter()
            data = encoder.encode(rgb)
            if i > 3:
                times.append((time.perf_counter() - t) * 1000)
        decoded = np.asarray(Image.open(io.BytesIO(data)).convert("RGB"))
        assert decoded.shape == (h, w, 3)
        error = float(
            np.abs(decoded.astype(float) - np.asarray(image).astype(float)).mean()
        )
        assert error < 5, f"Incorrect RGB/channel encoding: {error}"
        rows.append(
            {
                "size": [w, h],
                "median_ms": statistics.median(times),
                "mean_error_8bit": error,
                "bytes": len(data),
            }
        )
        (root / f"artifacts/nvjpeg-{w}.jpg").write_bytes(data)
finally:
    encoder.close()
(root / "artifacts/probe-jpeg.json").write_text(json.dumps(rows, indent=2) + "\n")
print(json.dumps(rows, indent=2))
