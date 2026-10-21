# Local frame API

Start with `./vsr start`. The service binds only to `127.0.0.1:8730`. `GET /health` returns readiness, whether an effect has loaded, frame count and latest timings. Readiness means the service is reachable, not that every GPU mode is validated.

## One frame

`POST /v1/frame?quality=HIGH&scale=2&format=png`

Send JPEG/PNG/WebP or the raw NV12 packet below as the body. Authenticate with `Authorization: Bearer <contents of .state/token>`. The response is PNG, JPEG, or raw AV1 bytes; `X-VSR-Stats` contains JSON dimensions, codec and timing data. See `examples/enhance_image.py` for a client that reads the private token from disk without placing it in process arguments.

AI qualities: `LOW`, `MEDIUM`, `HIGH`, `ULTRA`, `HIGHBITRATE_HIGH`, `HIGHBITRATE_ULTRA`. Scale is finite and ≥1. Dimensions are rounded to multiples of eight. GPU memory, encoder dimensions and the transport limit still apply. Unsupported modes fail rather than using a different scaler.

## Streaming

Connect to `ws://127.0.0.1:8730/v1/stream`, then send JSON:

```json
{ "token": "YOUR_LOCAL_TOKEN", "quality": "HIGH", "scale": 2, "format": "av1" }
```

Wait for `{"type":"ready",...}`, then send a binary input frame. Each success returns a JSON `{"type":"frame",...}` stats message followed by binary output. JPEG is the API default for compatibility; the extension explicitly selects AV1. Each AV1 response is an independent raw OBU keyframe packet. Decode using the codec string and dimensions returned in stats; do not hardcode a 4K AV1 level.

A simple client waits for both responses before sending the next frame. The extension allows four outstanding inputs and associates results with source timestamps/seek epochs. The GPU executor remains serialized; competing requests can receive a busy error instead of forming an unbounded queue. Errors use `{"type":"error","message":"..."}`; text `ping` gets a JSON pong. Compression is disabled. Browser WebSockets require an allowed loopback-page or extension Origin plus the token. Native API clients can omit Origin.

## Raw NV12 wire format

```text
ASCII "VSR1" | little-endian uint32 JSON length | UTF-8 JSON | NV12 pixel payload
```

The JSON length is at most 2048 bytes. Metadata contains `width`, `height`, two `layout` entries (`offset`, `stride`), and `color` (`matrix`: `bt709`, `smpte170m` or `bt470bg`; `fullRange`: boolean). Plane offsets are relative to the pixel payload. Dimensions must be even and at least 64×64. Strides, offsets, bounds, metadata and the total 64 MiB frame bound are validated before GPU access. See `vsr_service/raw_frame.py` and `tests/gpu_transport.py`.

## Privacy and deployment

The token, per-install extension config and hashed worker contain credentials. Never commit or distribute them. Project code sends video frames only to loopback. Do not change the bind to a public/LAN interface without designing a separate security and licensing model. The HTTP demonstration player is a generated local test fixture, not a remote media proxy.
