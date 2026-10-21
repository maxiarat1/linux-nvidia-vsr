"""Small ctypes binding to NVIDIA nvJPEG's documented GPU encoding API."""

import ctypes as C

import cupy as cp


class ImageDescriptor(C.Structure):
    _fields_ = [("channel", C.c_void_p * 4), ("pitch", C.c_size_t * 4)]


class GpuJpeg:
    def __init__(self):
        self.lib = C.CDLL("libnvjpeg.so.13")
        ptr, size = C.c_void_p, C.c_size_t
        signatures = {
            "nvjpegCreateSimple": [C.POINTER(ptr)],
            "nvjpegDestroy": [ptr],
            "nvjpegEncoderStateCreate": [ptr, C.POINTER(ptr), ptr],
            "nvjpegEncoderStateDestroy": [ptr],
            "nvjpegEncoderParamsCreate": [ptr, C.POINTER(ptr), ptr],
            "nvjpegEncoderParamsDestroy": [ptr],
            "nvjpegEncoderParamsSetQuality": [ptr, C.c_int, ptr],
            "nvjpegEncoderParamsSetSamplingFactors": [ptr, C.c_int, ptr],
            "nvjpegEncodeGetBufferSize": [ptr, ptr, C.c_int, C.c_int, C.POINTER(size)],
            "nvjpegEncodeImage": [
                ptr,
                ptr,
                ptr,
                C.POINTER(ImageDescriptor),
                C.c_int,
                C.c_int,
                C.c_int,
                ptr,
            ],
            "nvjpegEncodeRetrieveBitstream": [ptr, ptr, ptr, C.POINTER(size), ptr],
        }
        for name, args in signatures.items():
            fn = getattr(self.lib, name)
            fn.argtypes, fn.restype = args, C.c_int
        self.handle, self.state, self.params = ptr(), ptr(), ptr()
        self.buffer = None
        self.capacity = 0
        try:
            self.call("nvjpegCreateSimple", C.byref(self.handle))
            self.call(
                "nvjpegEncoderStateCreate", self.handle, C.byref(self.state), None
            )
            self.call(
                "nvjpegEncoderParamsCreate", self.handle, C.byref(self.params), None
            )
            self.call("nvjpegEncoderParamsSetQuality", self.params, 95, None)
            self.call(
                "nvjpegEncoderParamsSetSamplingFactors", self.params, 0, None
            )  # 4:4:4
        except Exception:
            self.close()
            raise

    def call(self, name, *args):
        status = getattr(self.lib, name)(*args)
        if status:
            raise RuntimeError(f"{name} failed with nvJPEG status {status}")

    def encode(self, rgb):
        if (
            rgb.dtype != cp.uint8
            or rgb.ndim != 3
            or rgb.shape[2] != 3
            or not rgb.flags.c_contiguous
        ):
            raise ValueError("nvJPEG requires contiguous HWC uint8 RGB on CUDA")
        h, w = rgb.shape[:2]
        stream = cp.cuda.get_current_stream()
        maximum = C.c_size_t()
        self.call(
            "nvjpegEncodeGetBufferSize",
            self.handle,
            self.params,
            w,
            h,
            C.byref(maximum),
        )
        if self.buffer is None or self.capacity < maximum.value:
            self.buffer = cp.cuda.alloc_pinned_memory(maximum.value)
            self.capacity = maximum.value
        image = ImageDescriptor()
        image.channel[0], image.pitch[0] = rgb.data.ptr, rgb.strides[0]
        self.call(
            "nvjpegEncodeImage",
            self.handle,
            self.state,
            self.params,
            C.byref(image),
            5,
            w,
            h,
            stream.ptr,
        )
        length = C.c_size_t(self.capacity)
        self.call(
            "nvjpegEncodeRetrieveBitstream",
            self.handle,
            self.state,
            self.buffer.ptr,
            C.byref(length),
            stream.ptr,
        )
        stream.synchronize()
        return bytes(memoryview(self.buffer)[: length.value])

    def close(self):
        for attr, name in (
            ("state", "nvjpegEncoderStateDestroy"),
            ("params", "nvjpegEncoderParamsDestroy"),
            ("handle", "nvjpegDestroy"),
        ):
            value = getattr(self, attr, None)
            if value:
                self.call(name, value)
                setattr(self, attr, None)
        self.buffer = None
