import asyncio
import hmac
import json
import os
import secrets
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .engine import Engine
from .limits import MAX_FRAME_BYTES, FRAME_SIZE_ERROR

ROOT = Path(os.environ.get("VSR_ROOT", Path(__file__).resolve().parents[1]))
STATE = ROOT / ".state"
STATE.mkdir(exist_ok=True)
TOKEN_FILE = STATE / "token"
if not TOKEN_FILE.exists():
    fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(secrets.token_urlsafe(32))
TOKEN = TOKEN_FILE.read_text().strip()
engine = Engine()
pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vsr-gpu")
# Exactly one request may own the GPU; no unbounded queue behind active video.
gpu_lock = asyncio.Lock()


@asynccontextmanager
async def lifespan(app):
    yield
    await asyncio.get_running_loop().run_in_executor(pool, engine.close)
    pool.shutdown(wait=True)


app = FastAPI(title="Local NVIDIA VSR", lifespan=lifespan)
app.add_middleware(
    TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"]
)


def authorized(token):
    return isinstance(token, str) and hmac.compare_digest(token, TOKEN)


def authenticate(request):
    if not authorized(request.headers.get("Authorization", "").removeprefix("Bearer ")):
        raise HTTPException(401, "Bearer token required; see .state/token")


async def process(data, quality, scale, output_format="jpeg"):
    if gpu_lock.locked():
        raise HTTPException(429, "Engine busy with another frame; retry later")
    async with gpu_lock:
        try:
            return await asyncio.get_running_loop().run_in_executor(
                pool, partial(engine.process, data, quality, scale, output_format)
            )
        except (ValueError, OSError) as e:
            raise HTTPException(422, str(e)) from e


@app.get("/health")
def health():
    return {
        "engine": "NVIDIA VideoSuperRes",
        "ready": True,
        "loaded": engine.effect is not None,
        "frames": engine.frames,
        "last": engine.last,
        "bind": "127.0.0.1",
    }


@app.post("/v1/frame")
async def frame(
    request: Request, quality: str = "HIGH", scale: float = 2, format: str = "jpeg"
):
    authenticate(request)
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_FRAME_BYTES:
            raise HTTPException(413, FRAME_SIZE_ERROR)
    result, stats = await process(bytes(data), quality, scale, format)
    return Response(
        result,
        media_type="video/av1" if format == "av1" else f"image/{format}",
        headers={"X-VSR-Stats": json.dumps(stats), "Cache-Control": "no-store"},
    )


@app.websocket("/v1/stream")
async def stream(ws: WebSocket):
    origin = ws.headers.get("origin", "")
    if origin and not (
        origin.startswith("chrome-extension://")
        or origin in ("http://127.0.0.1:8730", "http://localhost:8730")
    ):
        await ws.close(code=1008)
        return
    await ws.accept()
    try:
        config = await asyncio.wait_for(ws.receive_json(), timeout=5)
        if not isinstance(config, dict) or not authorized(config.get("token")):
            await ws.close(code=1008, reason="Invalid token")
            return
        quality, scale = config.get("quality", "HIGH"), float(config.get("scale", 2))
        output_format = config.get("format", "jpeg")
        await ws.send_json({"type": "ready", "engine": "NVIDIA VideoSuperRes"})
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            if message.get("text") == "ping":
                await ws.send_json({"type": "pong"})
                continue
            data = message.get("bytes")
            if data is None:
                continue
            try:
                result, stats = await process(data, quality, scale, output_format)
                await ws.send_json(stats)
                await ws.send_bytes(result)
            except HTTPException as e:
                await ws.send_json({"type": "error", "message": e.detail})
    except (WebSocketDisconnect, asyncio.TimeoutError):
        pass
    except Exception as e:
        try:
            await ws.send_json({"type": "error", "message": str(e)})
        except (RuntimeError, WebSocketDisconnect):
            pass


@app.get("/")
def demo():
    return FileResponse(
        ROOT / "examples/video/index.html", headers={"Cache-Control": "no-store"}
    )


@app.get("/demo.js")
def demo_js():
    return FileResponse(ROOT / "examples/video/demo.js", media_type="text/javascript")


@app.get("/demo.mp4")
def demo_video():
    return FileResponse(ROOT / "artifacts/demo.mp4", media_type="video/mp4")


@app.get("/sync.mp4")
def sync_video():
    return FileResponse(ROOT / "artifacts/sync.mp4", media_type="video/mp4")
