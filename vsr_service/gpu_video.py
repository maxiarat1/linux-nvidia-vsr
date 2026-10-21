"""Zero-extra-queue AV1 NVENC from GPU tensors; raw pixels never leave CUDA."""

import ctypes as C
from functools import lru_cache
from pathlib import Path
import cupy as cp
from .av1 import codec_string


@lru_cache(maxsize=1)
def library():
    lib = C.CDLL(
        str(Path(__file__).resolve().parents[1] / ".state/native/libvsr_nvenc.so")
    )
    lib.vsr_nvenc_error.restype = C.c_char_p
    lib.vsr_nvenc_limits.argtypes = [
        C.c_void_p,
        C.c_void_p,
        C.POINTER(C.c_int),
        C.POINTER(C.c_int),
    ]
    return lib


@lru_cache(maxsize=8)
def _encoder_limits(context, stream):
    lib = library()
    width, height = C.c_int(), C.c_int()
    if lib.vsr_nvenc_limits(context, stream, C.byref(width), C.byref(height)):
        raise RuntimeError(lib.vsr_nvenc_error().decode())
    return width.value, height.value


def encoder_limits():
    cp.cuda.runtime.getDevice()  # Initialize the driver even on the first AV1 request.
    context = cp.cuda.driver.ctxGetCurrent()
    if not context:
        cp.cuda.runtime.free(0)  # Lazily initialize the primary CUDA context.
        context = cp.cuda.driver.ctxGetCurrent()
    return _encoder_limits(context, cp.cuda.get_current_stream().ptr)


RGB_TO_NV12 = cp.RawKernel(
    r"""
extern "C" __global__ void rgb_nv12(const float* rgb, unsigned char* yuv,int w,int h) {
 int i=blockDim.x*blockIdx.x+threadIdx.x, n=w*h;if(i>=n+n/2)return;
 if(i<n) {
  float y=.2126f*rgb[i]+.7152f*rgb[n+i]+.0722f*rgb[2*n+i];
  yuv[i]=(unsigned char)roundf(fminf(235,fmaxf(16,16+219*y)));
 } else {
  int j=i-n,x=(j%w)&~1,yy=(j/w)*2;float r=0,g=0,b=0;
  for(int dy=0;dy<2;dy++)for(int dx=0;dx<2;dx++){
   int p=(yy+dy)*w+x+dx;r+=rgb[p];g+=rgb[n+p];b+=rgb[2*n+p];
  }
  r*=.25f;g*=.25f;b*=.25f;float y=.2126f*r+.7152f*g+.0722f*b;
  float chroma=(j&1)?(r-y)/1.5748f:(b-y)/1.8556f;
  yuv[i]=(unsigned char)roundf(fminf(240,fmaxf(16,128+224*chroma)));
 }
}""",
    "rgb_nv12",
)


class GpuVideoEncoder:
    def __init__(self, w, h, qp=14):
        self.w, self.h = w, h
        self.lib = library()
        self.codec = None
        self.lib.vsr_nvenc_create.argtypes = [
            C.c_int,
            C.c_int,
            C.c_void_p,
            C.c_void_p,
            C.c_int,
        ]
        self.lib.vsr_nvenc_create.restype = C.c_void_p
        self.lib.vsr_nvenc_encode.argtypes = [
            C.c_void_p,
            C.c_void_p,
            C.POINTER(C.c_void_p),
            C.POINTER(C.c_size_t),
        ]
        self.lib.vsr_nvenc_destroy.argtypes = [C.c_void_p]
        # Allocate our buffer before creating native resources so an allocation
        # failure cannot strand an encoder session during __init__.
        self.nv12 = cp.empty(w * h * 3 // 2, dtype=cp.uint8)
        self.handle = self.lib.vsr_nvenc_create(
            w, h, cp.cuda.driver.ctxGetCurrent(), cp.cuda.get_current_stream().ptr, qp
        )
        if not self.handle:
            raise RuntimeError(self.lib.vsr_nvenc_error().decode())

    def encode(self, rgb):
        n = self.w * self.h * 3 // 2
        RGB_TO_NV12(((n + 255) // 256,), (256,), (rgb, self.nv12, self.w, self.h))
        # Producer CUDA work completes before NVENC reads the device surface.
        cp.cuda.get_current_stream().synchronize()
        ptr, length = C.c_void_p(), C.c_size_t()
        if self.lib.vsr_nvenc_encode(
            self.handle, self.nv12.data.ptr, C.byref(ptr), C.byref(length)
        ):
            raise RuntimeError(self.lib.vsr_nvenc_error().decode())
        packet = C.string_at(ptr, length.value)
        if self.codec is None:
            self.codec = codec_string(packet)
        return packet

    def close(self):
        if self.handle:
            result = self.lib.vsr_nvenc_destroy(self.handle)
            self.handle = None
            if result:
                raise RuntimeError(self.lib.vsr_nvenc_error().decode())
        self.nv12 = None
