"""Measure full HTTP image-in/image-out costs, including CPU copies/codecs."""

import io
import json
import statistics
import time
from pathlib import Path

import httpx
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
token = (ROOT / ".state/token").read_text().strip()
report = []
with httpx.Client(
    base_url="http://127.0.0.1:8730",
    timeout=60,
    headers={"Authorization": f"Bearer {token}"},
) as client:
    for w, h in [(640, 360), (1280, 720), (1920, 1080)]:
        image = Image.open(ROOT / "artifacts/probe-input.png").resize((w, h))
        buf = io.BytesIO()
        image.save(buf, "JPEG", quality=95)
        times = []
        for i in range(16):
            t = time.perf_counter()
            r = client.post("/v1/frame?scale=2&quality=HIGH", content=buf.getvalue())
            r.raise_for_status()
            if i >= 4:
                times.append((time.perf_counter() - t) * 1000)
        stats = json.loads(r.headers["X-VSR-Stats"])
        row = {
            "input": [w, h],
            "output": [w * 2, h * 2],
            "median_http_ms": round(statistics.median(times), 2),
            "last_frame": stats,
        }
        report.append(row)
(ROOT / "artifacts/benchmark-api.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report, indent=2))
