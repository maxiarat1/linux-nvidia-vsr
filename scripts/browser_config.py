"""Settings verified with this project's NVIDIA/Wayland Helium probes."""

import os
import shutil
from pathlib import Path

# Prefer the real binary so isolated launches do not inherit distro wrapper
# flags, profiles or unrelated extensions. Override with an absolute path.
BROWSER = os.environ.get("VSR_BROWSER") or next(
    (
        str(p)
        for p in map(
            Path,
            [
                "/opt/helium-browser-bin/helium",
                "/usr/lib/chromium/chromium",
                "/opt/google/chrome/chrome",
            ],
        )
        if p.is_file()
    ),
    shutil.which("helium-browser") or shutil.which("chromium") or "helium-browser",
)

VIDEO_FLAGS = [
    "--enable-features=AcceleratedVideoDecodeLinuxGL,VaapiOnNvidiaGPUs",
    "--ignore-gpu-blocklist",
    "--use-gl=angle",
    "--use-angle=gl",
]


def browser_environment():
    return {
        **os.environ,
        "LIBVA_DRIVER_NAME": "nvidia",
        "LD_LIBRARY_PATH": str(Path(BROWSER).parent)
        + ":"
        + os.environ.get("LD_LIBRARY_PATH", ""),
    }
