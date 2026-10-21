"""Reproduce PyNvVideoCodec's extra three-frame queue with CUDA input."""

import json
import time
from pathlib import Path
import cupy as cp
import PyNvVideoCodec as nvc

ROOT = Path(__file__).resolve().parents[1]
a = cp.empty((1440, 2560, 4), dtype=cp.uint8)
a[:] = cp.asarray([200, 100, 50, 255], dtype=cp.uint8)
cp.cuda.runtime.deviceSynchronize()
encoder = nvc.CreateEncoder(
    2560,
    1440,
    "ABGR",
    False,
    codec="av1",
    preset="P1",
    tuning_info="ultra_low_latency",
    rc="constqp",
    constqp=16,
    fps=60,
    gop=1,
    bf=0,
    cudacontext=cp.cuda.driver.ctxGetCurrent(),
    cudastream=cp.cuda.get_current_stream().ptr,
)


class Frame:
    # The shipped C++ implementation calls cuda() and accepts the array itself.
    def cuda(self):
        return a


rows = []
for i in range(12):
    start = time.perf_counter()
    packets = encoder.Encode(Frame())
    rows.append(
        {
            "input_frame": i,
            "packets": len(packets),
            "returned_timestamps": [p["timestamp"] for p in packets],
            "encode_ms": (time.perf_counter() - start) * 1000,
        }
    )
report = {
    "python_wrapper": nvc.__version__,
    "frames": rows,
    "packets_held_until_flush": len(encoder.EndEncode()),
}
(ROOT / "artifacts/python-nvenc-queue.json").write_text(
    json.dumps(report, indent=2) + "\n"
)
print(json.dumps(report, indent=2))
