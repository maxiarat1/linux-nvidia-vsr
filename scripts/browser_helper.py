"""Register the browser-launched helper using removable per-profile links."""

import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST = "com.local_nvidia_vsr.engine"


def extension_id():
    digest = hashlib.sha256(str(ROOT / "extension").encode()).hexdigest()[:32]
    return "".join(chr(ord("a") + int(c, 16)) for c in digest)


def install(profile=None):
    folder = ROOT / ".state/native-host"
    folder.mkdir(parents=True, exist_ok=True)
    manifest = folder / f"{HOST}.json"
    manifest.write_text(
        json.dumps(
            {
                "name": HOST,
                "description": "Start the local NVIDIA VSR engine on demand",
                "path": str(ROOT / "scripts/native_host.sh"),
                "type": "stdio",
                "allowed_origins": [f"chrome-extension://{extension_id()}/"],
            },
            indent=2,
        )
        + "\n"
    )
    manifest.chmod(0o600)
    config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    profiles = (
        [Path(profile)]
        if profile
        else [config / "net.imput.helium", ROOT / ".state/helium-profile"]
    )
    record = folder / "links.json"
    links = (
        {name for name in json.loads(record.read_text()) if Path(name).is_symlink()}
        if record.exists()
        else set()
    )
    for browser_profile in profiles:
        target = browser_profile / "NativeMessagingHosts" / manifest.name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink() and target.resolve() == manifest:
            pass
        elif target.exists() or target.is_symlink():
            raise RuntimeError(
                "A different native helper is already registered at " + str(target)
            )
        else:
            target.symlink_to(manifest)
        links.add(str(target))
    record.write_text(json.dumps(sorted(links), indent=2) + "\n")
    record.chmod(0o600)
    print("Browser helper registered. Enable VSR to start the engine on demand.")


def remove():
    folder = ROOT / ".state/native-host"
    record = folder / "links.json"
    manifest = folder / f"{HOST}.json"
    if record.exists():
        for name in json.loads(record.read_text()):
            link = Path(name)
            if link.is_symlink() and link.resolve() == manifest:
                link.unlink()
        record.unlink()
    manifest.unlink(missing_ok=True)
    print("Browser helper registrations removed; no system service was installed.")
