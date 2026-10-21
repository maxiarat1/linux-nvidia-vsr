"""Build a project-local NVENC adapter from pinned NVIDIA SDK source."""

import base64
import hashlib
import os
from pathlib import Path
import subprocess
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
archive = ROOT / ".cache/PyNvVideoCodec_2.2.3.zip"
archive.parent.mkdir(exist_ok=True)
if not archive.exists():
    urllib.request.urlretrieve(
        "https://api.ngc.nvidia.com/v2/resources/nvidia/pynvvideocodec/versions/v2.2.3/files/PyNvVideoCodec_2.2.3.zip",
        archive,
    )
if (
    base64.b64encode(hashlib.sha256(archive.read_bytes()).digest()).decode()
    != "KffAaU1oMmbFY6yppPjpeboMXc6PDMwe/lAAc+CUi+A="
):
    raise RuntimeError("NVIDIA SDK archive SHA-256 mismatch; refusing to build")
source = ROOT / ".cache/nvcodec-sdk"
with zipfile.ZipFile(archive) as z:
    for n in z.namelist():
        if n.endswith("/") or not any(
            x in n for x in ["/NvEncoder/", "/Interface/", "/helper_classes/Utils/"]
        ):
            continue
        p = source / n.split("/", 1)[1]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(z.read(n))
sdk = source / "src/VideoCodecSDKUtils"
out = ROOT / ".state/native/libvsr_nvenc.so"
out.parent.mkdir(parents=True, exist_ok=True)
build_out = out.with_suffix(".so.new")
subprocess.run(
    [
        "g++",
        "-O2",
        "-std=c++17",
        "-shared",
        "-fPIC",
        "-DNVENC_VER_13_0",
        "-I" + str(Path(os.environ["CUDA_PATH"]) / "include"),
        "-I" + str(sdk / "Interface"),
        "-I" + str(sdk / "helper_classes/NvCodec"),
        "-I" + str(sdk / "helper_classes/Utils"),
        str(ROOT / "native/nvenc.cpp"),
        str(sdk / "helper_classes/NvCodec/NvEncoder/NvEncoder_130.cpp"),
        str(sdk / "helper_classes/NvCodec/NvEncoder/NvEncoderCuda.cpp"),
        "-Wl,-l:libcuda.so.1",
        "-ldl",
        "-pthread",
        "-o",
        str(build_out),
    ],
    check=True,
)
os.replace(build_out, out)
print(out)
