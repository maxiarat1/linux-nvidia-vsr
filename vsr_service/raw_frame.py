"""A small, validated NV12 wire format for browser VideoFrame.copyTo output."""

import json
import struct
import numpy as np
import cupy as cp

NV12_TO_RGB = cp.RawKernel(
    r"""
extern "C" __global__ void nv12_rgb(const unsigned char* a,float* out,int w,int h,int yo,int ys,int uo,int us,int bt709,int full) {
 int i=blockIdx.x*blockDim.x+threadIdx.x,n=w*h;if(i>=n)return;int x=i%w,y=i/w;
 float Y=a[yo+y*ys+x],U=a[uo+(y/2)*us+(x&~1)],V=a[uo+(y/2)*us+(x&~1)+1];
 Y=full?Y/255.f:(Y-16)/219.f;U=(U-128)/(full?255.f:224.f);V=(V-128)/(full?255.f:224.f);
 float r=Y+(bt709?1.5748f:1.402f)*V;
 float g=Y-(bt709?.187324f:.344136f)*U-(bt709?.468124f:.714136f)*V;
 float b=Y+(bt709?1.8556f:1.772f)*U;
 out[i]=fminf(1,fmaxf(0,r));out[n+i]=fminf(1,fmaxf(0,g));out[2*n+i]=fminf(1,fmaxf(0,b));
}""",
    "nv12_rgb",
)


def parse(data):
    if len(data) < 8:
        raise ValueError("Truncated raw frame header")
    length = struct.unpack_from("<I", data, 4)[0]
    if length > 2048 or length < 2 or len(data) < 8 + length:
        raise ValueError("Invalid raw metadata size")
    try:
        m = json.loads(data[8 : 8 + length])
    except (ValueError, UnicodeError) as e:
        raise ValueError("Invalid raw metadata") from e
    if not isinstance(m, dict):
        raise ValueError("Raw metadata must be an object")
    w, h = m.get("width"), m.get("height")
    if type(w) != int or type(h) != int or w < 64 or h < 64 or w % 2 or h % 2:
        raise ValueError("Invalid NV12 dimensions")
    layout = m.get("layout")
    if not isinstance(layout, list) or len(layout) != 2:
        raise ValueError("NV12 requires two planes")
    pixels = memoryview(data)[8 + length :]
    for plane, rows in zip(layout, [h, h // 2]):
        if not isinstance(plane, dict):
            raise ValueError("Invalid NV12 plane")
        offset, stride = plane.get("offset"), plane.get("stride")
        if (
            type(offset) != int
            or type(stride) != int
            or offset < 0
            or stride < w
            or offset + (rows - 1) * stride + w > len(pixels)
        ):
            raise ValueError("NV12 plane exceeds payload")
    color = m.get("color", {})
    if not isinstance(color, dict) or color.get("matrix") not in [
        "bt709",
        "smpte170m",
        "bt470bg",
    ]:
        raise ValueError("Unsupported NV12 color matrix")
    return m, pixels


def to_cuda(metadata, pixels):
    w, h = metadata["width"], metadata["height"]
    y, uv = metadata["layout"]
    color = metadata["color"]
    a = cp.asarray(np.frombuffer(pixels, dtype=np.uint8))
    out = cp.empty((3, h, w), dtype=cp.float32)
    NV12_TO_RGB(
        ((w * h + 255) // 256,),
        (256,),
        (
            a,
            out,
            w,
            h,
            y["offset"],
            y["stride"],
            uv["offset"],
            uv["stride"],
            int(color["matrix"] == "bt709"),
            int(color.get("fullRange") is True),
        ),
    )
    return out
