"""NVIDIA VFX only. No substitution or silent non-AI fallback."""

import io
import math
import time

import cupy as cp
import numpy as np
from PIL import Image
from nvvfx import VideoSuperRes
from .gpu_jpeg import GpuJpeg
from .gpu_video import GpuVideoEncoder
from . import raw_frame
from .limits import MAX_FRAME_BYTES, FRAME_SIZE_ERROR
from .gpu_video import encoder_limits

QUALITIES = ("LOW", "MEDIUM", "HIGH", "ULTRA", "HIGHBITRATE_HIGH", "HIGHBITRATE_ULTRA")


class Engine:
    def __init__(self):
        self.effect = None
        self.key = None
        self.frames = 0
        self.last = None
        self.jpeg = None
        self.video = None

    def close(self):
        if self.video:
            self.video.close()
            self.video = None
        if self.effect:
            self.effect.close()
        self.effect = None
        self.key = None
        if self.jpeg:
            self.jpeg.close()
            self.jpeg = None
        cp.get_default_memory_pool().free_all_blocks()

    def process(self, data, quality="HIGH", scale=2.0, output_format="jpeg"):
        start = time.perf_counter()
        if quality not in QUALITIES:
            raise ValueError("Unsupported AI quality mode")
        if not math.isfinite(scale) or scale < 1:
            raise ValueError("Scale must be finite and at least 1")
        if output_format not in ("jpeg", "png", "av1"):
            raise ValueError("Output format must be jpeg, png or av1")
        if len(data) > MAX_FRAME_BYTES:
            raise ValueError(FRAME_SIZE_ERROR)
        raw = data.startswith(b"VSR1")
        if raw:
            metadata, pixels = raw_frame.parse(data)
            w, h = metadata["width"], metadata["height"]
        else:
            with Image.open(io.BytesIO(data)) as im:
                w, h = im.size
                if im.format not in ("JPEG", "PNG", "WEBP"):
                    raise ValueError("Input must be JPEG, PNG, WebP or raw NV12")
                if w < 64 or h < 64:
                    raise ValueError("Input must be at least 64x64")
        if not math.isfinite(w * scale) or not math.isfinite(h * scale):
            raise ValueError("Invalid output dimensions")
        ow, oh = round(w * scale / 8) * 8, round(h * scale / 8) * 8
        if output_format == "av1":
            max_width, max_height = encoder_limits()
            if ow > max_width or oh > max_height:
                raise ValueError(
                    f"Requested {ow}x{oh} exceeds this GPU's NVENC AV1 dimensions "
                    f"({max_width}x{max_height}); reduce scale"
                )
        # Reject allocations which cannot fit even the visible image buffers.
        # This is a live memory check, not a fixed resolution/pixel budget.
        free, _ = cp.cuda.runtime.memGetInfo()
        reusable = cp.get_default_memory_pool().free_bytes()
        if w * h * 12 + ow * oh * 24 > free + reusable:
            raise ValueError("Insufficient available GPU memory for these dimensions")
        if not raw:
            with Image.open(io.BytesIO(data)) as im:
                pixels = np.asarray(im.convert("RGB"))
        decoded_at = time.perf_counter()
        key = (w, h, ow, oh, quality)
        if self.key != key:
            self.close()
            self.effect = VideoSuperRes(quality=VideoSuperRes.QualityLevel[quality])
            self.effect.output_width, self.effect.output_height = ow, oh
            self.effect.load()
            self.key = key
        configured_at = time.perf_counter()
        if raw:
            a = raw_frame.to_cuda(metadata, pixels)
        else:
            a = cp.ascontiguousarray(
                cp.asarray(pixels).transpose(2, 0, 1), dtype=cp.float32
            )
            a *= 1 / 255
        cp.cuda.runtime.deviceSynchronize()
        gpu_start = time.perf_counter()
        out = cp.from_dlpack(self.effect.run(a).image).copy()
        cp.cuda.runtime.deviceSynchronize()
        inference_ms = (time.perf_counter() - gpu_start) * 1000
        inferred_at = time.perf_counter()
        if output_format == "av1":
            if self.video is None:
                self.video = GpuVideoEncoder(ow, oh)
            result = self.video.encode(out)
        else:
            rgb = cp.ascontiguousarray(
                cp.clip(out.transpose(1, 2, 0) * 255, 0, 255).astype(cp.uint8)
            )
        if output_format == "png":
            result = io.BytesIO()
            Image.fromarray(cp.asnumpy(rgb)).save(result, "PNG", compress_level=1)
            result = result.getvalue()
        elif output_format == "jpeg":
            if self.jpeg is None:
                self.jpeg = GpuJpeg()
            result = self.jpeg.encode(rgb)
        self.frames += 1
        self.last = {
            "type": "frame",
            "engine": "NVIDIA VideoSuperRes",
            "quality": quality,
            "input_width": w,
            "input_height": h,
            "width": ow,
            "height": oh,
            "encoder": {"jpeg": "nvJPEG CUDA", "av1": "NVENC AV1 CUDA", "png": "PNG"}[
                output_format
            ],
            "format": output_format,
            "codec": self.video.codec if output_format == "av1" else None,
            "input_format": "NV12 CUDA" if raw else "CPU image decoder",
            "keyframe": True if output_format == "av1" else None,
            "inference_ms": round(inference_ms, 3),
            "input_decode_ms": round((decoded_at - start) * 1000, 3),
            "configure_ms": round((configured_at - decoded_at) * 1000, 3),
            "upload_ms": round((gpu_start - configured_at) * 1000, 3),
            "output_encode_ms": round((time.perf_counter() - inferred_at) * 1000, 3),
            "service_ms": round((time.perf_counter() - start) * 1000, 3),
            "frames": self.frames,
        }
        return result, self.last.copy()
