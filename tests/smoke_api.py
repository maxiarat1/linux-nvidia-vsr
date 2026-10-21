import asyncio
import io
import json
from pathlib import Path
import time

import httpx
from PIL import Image
from websockets.asyncio.client import connect

ROOT = Path(__file__).resolve().parents[1]
TOKEN = (ROOT / ".state/token").read_text().strip()
DATA = (ROOT / "artifacts/probe-input.png").read_bytes()


async def main():
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with httpx.Client(base_url="http://127.0.0.1:8730", timeout=60) as client:
        assert client.post("/v1/frame", content=DATA).status_code == 401
        assert (
            client.post(
                "/v1/frame?quality=FAKE", content=DATA, headers=headers
            ).status_code
            == 422
        )
        assert (
            client.post("/v1/frame", content=b"bad image", headers=headers).status_code
            == 422
        )
        assert (
            client.post(
                "/v1/frame?scale=0.5", content=DATA, headers=headers
            ).status_code
            == 422
        )
        response = client.post(
            "/v1/frame?quality=HIGH&format=png", content=DATA, headers=headers
        )
        response.raise_for_status()
        assert Image.open(io.BytesIO(response.content)).size == (1280, 720)
        stats = json.loads(response.headers["X-VSR-Stats"])
        assert stats["engine"] == "NVIDIA VideoSuperRes"
        (ROOT / "artifacts/api-high.png").write_bytes(response.content)
    async with connect("ws://127.0.0.1:8730/v1/stream", compression=None) as ws:
        await ws.send(json.dumps({"token": TOKEN, "quality": "HIGH", "scale": 2}))
        assert json.loads(await ws.recv())["type"] == "ready"
        times = []
        for _ in range(30):
            t = time.perf_counter()
            await ws.send(DATA)
            frame_stats = json.loads(await ws.recv())
            image = await ws.recv()
            assert Image.open(io.BytesIO(image)).size == (1280, 720)
            times.append((time.perf_counter() - t) * 1000)
        await ws.send("ping")
        assert json.loads(await ws.recv())["type"] == "pong"
    report = {
        "api_checks": "passed",
        "websocket_frames": 30,
        "average_socket_roundtrip_ms": round(sum(times) / len(times), 2),
        "last_frame": frame_stats,
    }
    (ROOT / "artifacts/api-test.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


asyncio.run(main())
