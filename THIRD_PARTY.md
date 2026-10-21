# Third-party software

The repository's MIT license applies to its original source. It does not relicense NVIDIA software, models, downloaded SDK source, Python packages or browser software. No proprietary NVIDIA wheel, shared library, model, browser profile or private token is included in Git.

## NVIDIA VFX and CUDA

`requirements.lock` installs `nvidia-vfx==0.2.0.0`, marked `LicenseRef-NvidiaProprietary` in its package metadata, plus NVIDIA CUDA and nvJPEG packages. These are obtained from their upstream package indexes, not redistributed by this repository. Review the licenses for the versions you install and obtain any required entitlement for your use. Download availability is not a statement of licensing rights or commercial deployment permission.

The VFX wheel includes its agreements under:

```text
.venv/lib/python3.13/site-packages/nvidia_vfx-0.2.0.0.dist-info/licenses/packaging/
```

They include NVIDIA's Software License Agreement, Product-Specific Terms for NVIDIA AI Products, model agreements, and third-party notices. The [NVIDIA VFX catalog](https://catalog.ngc.nvidia.com/orgs/nvidia/maxine/collections/maxine_vfx_sdk) links the governing agreements. NVIDIA's Linux SDK documentation describes a server-oriented support scope; this desktop integration is independent experimental code.

## NVENC helper source

`scripts/build_nvenc.py` downloads the pinned PyNvVideoCodec 2.2.3 SDK archive from NVIDIA NGC and verifies its SHA-256 before extracting helper source into `.cache/nvcodec-sdk`. The native build compiles `NvEncoder_130.cpp` and `NvEncoderCuda.cpp` with this project's adapter. NVIDIA marks these helper files MIT licensed; their copyright and license notices remain in the extracted source. The SDK archive, headers and compiled adapter are ignored by Git.

Archive SHA-256 (base64): `KffAaU1oMmbFY6yppPjpeboMXc6PDMwe/lAAc+CUi+A=`.

If you distribute compiled artifacts yourself, review every applicable SDK/header/runtime license and preserve the required notices. This project publishes source only.

## Other dependencies and examples

Python and test dependencies retain the licenses in their installed distribution metadata. Helium/Chromium, FFmpeg, uv and the NVIDIA VA-API driver are host dependencies; they are not bundled here.

The video fixture is generated locally with FFmpeg's test pattern, rather than shipping a video from a third party. The dashboard screenshot uses the project's interactive preview and explicitly simulated metrics. Links to third-party videos are demonstrations only; no third-party video is included.
