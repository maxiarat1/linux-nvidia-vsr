"""Verify larger raw frames, AV1 level signaling and hardware-limit rejection."""

import asyncio
import json
from pathlib import Path
import struct
import subprocess
import time

import httpx
from websockets.asyncio.client import connect

ROOT = Path(__file__).resolve().parents[1]
TOKEN = (ROOT / ".state/token").read_text().strip()


def source(width, height):
    pixels = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size={width}x{height}:rate=60",
            "-frames:v",
            "1",
            "-vf",
            "scale=out_color_matrix=bt709:out_range=limited",
            "-pix_fmt",
            "nv12",
            "-f",
            "rawvideo",
            "pipe:1",
        ],
        capture_output=True,
        check=True,
    ).stdout
    metadata = json.dumps(
        {
            "width": width,
            "height": height,
            "layout": [
                {"offset": 0, "stride": width},
                {"offset": width * height, "stride": width},
            ],
            "color": {"matrix": "bt709", "fullRange": False},
        }
    ).encode()
    return b"VSR1" + struct.pack("<I", len(metadata)) + metadata + pixels


async def main():
    report = []
    for width, height, scale in [(2560, 1440, 1.5), (2560, 1440, 2), (3840, 2160, 2)]:
        wire = source(width, height)
        timings = []
        async with connect(
            "ws://127.0.0.1:8730/v1/stream", compression=None, max_size=64 * 1024 * 1024
        ) as ws:
            await ws.send(
                json.dumps(
                    {"token": TOKEN, "format": "av1", "scale": scale, "quality": "HIGH"}
                )
            )
            assert json.loads(await ws.recv())["type"] == "ready"
            for _ in range(8):
                start = time.perf_counter()
                await ws.send(wire)
                stats = json.loads(await ws.recv())
                assert stats["type"] == "frame", stats
                packet = await ws.recv()
                timings.append((time.perf_counter() - start) * 1000)
            expected = [round(width * scale / 8) * 8, round(height * scale / 8) * 8]
            probe = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-f",
                    "obu",
                    "-show_streams",
                    "-of",
                    "json",
                    "pipe:0",
                ],
                input=packet,
                capture_output=True,
                check=True,
            )
            stream = json.loads(probe.stdout)["streams"][0]
            assert [stream["width"], stream["height"]] == expected, stream
            level = int(stats["codec"].split(".")[2][:2])
            assert stream["level"] == level, (stream, stats)
            # Decode actual pixels as well as inspecting metadata.
            subprocess.run(
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
                    "null",
                    "-",
                ],
                input=packet,
                capture_output=True,
                check=True,
            )
            (
                ROOT / f"artifacts/resolution-{width}x{height}-{scale:g}x.av1"
            ).write_bytes(packet)
            report.append(
                {
                    "input": [width, height],
                    "output": expected,
                    "input_bytes": len(wire),
                    "codec": stats["codec"],
                    "decoder_level": stream["level"],
                    "frames": 8,
                    "warm_roundtrip_ms": sum(timings[1:]) / len(timings[1:]),
                    "last": stats,
                }
            )
    with httpx.Client(timeout=60) as client:
        bad = client.post(
            "http://127.0.0.1:8730/v1/frame?format=av1&scale=3",
            headers={"Authorization": f"Bearer {TOKEN}"},
            content=wire,
        )
        assert bad.status_code == 422 and "NVENC AV1 dimensions" in bad.text, (
            bad.status_code,
            bad.text,
        )
        good = client.post(
            "http://127.0.0.1:8730/v1/frame?format=av1&scale=1",
            headers={"Authorization": f"Bearer {TOKEN}"},
            content=wire,
        )
        good.raise_for_status()
    result = {
        "passed": True,
        "cases": report,
        "hardware_limit_rejected": True,
        "recovered_after_rejection": True,
    }
    (ROOT / "artifacts/resolution-support.json").write_text(
        json.dumps(result, indent=2) + "\n"
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
