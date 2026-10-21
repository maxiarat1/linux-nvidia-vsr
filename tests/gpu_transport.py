"""Exercise real raw input, GPU VSR, zero-queue AV1 and decoded color accuracy."""

import io
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile

import httpx
import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
headers = {"Authorization": "Bearer " + (ROOT / ".state/token").read_text().strip()}
image = Image.new("RGB", (640, 360))
draw = ImageDraw.Draw(image)
colors = [(180, 30, 30), (30, 180, 30), (30, 30, 180), (180, 180, 180)]
for i, c in enumerate(colors):
    draw.rectangle((i * 160, 0, (i + 1) * 160 - 1, 359), fill=c)
source = io.BytesIO()
image.save(source, "PNG")
nv12 = subprocess.run(
    [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        "pipe:0",
        "-vf",
        "scale=out_color_matrix=bt709:out_range=limited",
        "-pix_fmt",
        "nv12",
        "-f",
        "rawvideo",
        "pipe:1",
    ],
    input=source.getvalue(),
    capture_output=True,
    check=True,
).stdout
metadata = {
    "width": 640,
    "height": 360,
    "layout": [{"offset": 0, "stride": 640}, {"offset": 640 * 360, "stride": 640}],
    "color": {"matrix": "bt709", "fullRange": False},
}
meta = json.dumps(metadata).encode()
wire = b"VSR1" + struct.pack("<I", len(meta)) + meta + nv12
rows = []
with httpx.Client(timeout=60) as client:
    for malformed in [
        b"VSR1",
        b"VSR1" + struct.pack("<I", 9999),
        wire[:-640],
        b"VSR1" + struct.pack("<I", 2) + b"[]",
    ]:
        r = client.post(
            "http://127.0.0.1:8730/v1/frame", headers=headers, content=malformed
        )
        assert r.status_code == 422, (r.status_code, r.text)
    reference = client.post(
        "http://127.0.0.1:8730/v1/frame?format=png", headers=headers, content=wire
    )
    reference.raise_for_status()
    pixels = np.array(Image.open(io.BytesIO(reference.content)).convert("RGB"))
    for i, color in enumerate(colors):
        mean = pixels[120:600, i * 320 + 80 : (i + 1) * 320 - 80].mean((0, 1))
        assert np.max(np.abs(mean - color)) < 4, (color, mean)
    for i in range(6):
        r = client.post(
            "http://127.0.0.1:8730/v1/frame?format=av1", headers=headers, content=wire
        )
        r.raise_for_status()
        stats = json.loads(r.headers["X-VSR-Stats"])
        (ROOT / f"artifacts/check-frame-{i}.av1").write_bytes(r.content)
        decoded = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "obu",
                "-i",
                "pipe:0",
                "-frames:v",
                "1",
                "-f",
                "image2pipe",
                "-c:v",
                "png",
                "pipe:1",
            ],
            input=r.content,
            capture_output=True,
            check=True,
        ).stdout
        actual = np.array(Image.open(io.BytesIO(decoded)).convert("RGB"))
        assert actual.shape == pixels.shape
        mae = float(np.abs(actual.astype(float) - pixels).mean())
        assert mae < 3, mae
        rows.append(
            {
                "frame": i,
                "bytes": len(r.content),
                "mean_abs_rgb_error": mae,
                "stats": stats,
            }
        )
        if i == 0:
            (ROOT / "artifacts/av1-color-check.png").write_bytes(decoded)
(ROOT / "artifacts/gpu-transport-check.json").write_text(
    json.dumps(rows, indent=2) + "\n"
)
print(
    json.dumps(
        {
            "checks": "passed",
            "frames": len(rows),
            "mae": rows[0]["mean_abs_rgb_error"],
            "last": rows[-1]["stats"],
        },
        indent=2,
    )
)
