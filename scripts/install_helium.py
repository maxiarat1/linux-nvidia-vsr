"""Opt-in integration with Arch's Helium flags file; never restarts a browser."""

import argparse
import json
import os
from pathlib import Path
import shlex

ROOT = Path(__file__).resolve().parents[1]


def updated_flags(original, extension):
    lines = original.splitlines()

    def setting(name, values, merge=False):
        found, prior = [], []
        for i, line in enumerate(lines):
            tokens = shlex.split(line, comments=True)
            if not any(token.startswith(name + "=") for token in tokens):
                continue
            if len(tokens) != 1 or not tokens[0].startswith(name + "="):
                raise ValueError(
                    "Complex Helium flags file; use the documented manual setup."
                )
            found.append(i)
            prior.extend(tokens[0].split("=", 1)[1].split(","))
        combined = list(dict.fromkeys((prior if merge else []) + values))
        entry = shlex.quote(name + "=" + ",".join(combined))
        if found:
            lines[found[0]] = entry
            for i in reversed(found[1:]):
                del lines[i]
        else:
            lines.append(entry)

    setting("--load-extension", [str(extension)], merge=True)
    setting(
        "--enable-features",
        ["AcceleratedVideoDecodeLinuxGL", "VaapiOnNvidiaGPUs"],
        merge=True,
    )
    setting("--disable-features", ["Vulkan"], merge=True)
    for name, value in [
        ("--ozone-platform", "wayland"),
        ("--use-gl", "angle"),
        ("--use-angle", "gl"),
    ]:
        setting(name, [value])
    if "--ignore-gpu-blocklist" not in [line.strip() for line in lines]:
        lines.append("--ignore-gpu-blocklist")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--remove",
        action="store_true",
        help="Restore original flags if unchanged since installation",
    )
    args = parser.parse_args()
    config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    flags = config / "helium-browser-flags.conf"
    record = ROOT / ".state/helium-integration.json"
    if args.remove:
        if not record.exists():
            raise SystemExit("No saved Helium integration to remove.")
        saved = json.loads(record.read_text())
        flags = Path(saved["path"])
        if not flags.exists() or flags.read_text() != saved["after"]:
            raise SystemExit(
                "Flags changed after installation; preserving your edits. Remove the VSR extension path manually; see docs/browser-setup.md."
            )
        if saved["existed"]:
            flags.write_text(saved["before"])
        else:
            flags.unlink()
        record.unlink()
        print(
            "Original Helium flags restored. Restart Helium and remove the extension if still listed."
        )
        return
    if record.exists():
        saved = json.loads(record.read_text())
        if flags.exists() and flags.read_text() == saved["after"]:
            print("Helium integration already configured.")
            return
        raise SystemExit(
            "Helium flags changed since installation. Preserve your changes and use manual setup or remove the previous integration first."
        )
    existed = flags.exists()
    before = flags.read_text() if existed else ""
    after = updated_flags(before, ROOT / "extension")
    config.mkdir(parents=True, exist_ok=True)
    record.parent.mkdir(exist_ok=True)
    # Save the recovery record before modifying normal browser configuration.
    record.touch(mode=0o600)
    record.write_text(
        json.dumps(
            {"path": str(flags), "existed": existed, "before": before, "after": after},
            indent=2,
        )
        + "\n"
    )
    record.chmod(0o600)
    flags.write_text(after)
    if not existed:
        flags.chmod(0o600)
    print(
        "Helium flags configured; existing extension paths and feature lists preserved."
    )
    print(
        "Restart Helium when convenient. Start the engine with ./vsr start or launch with ./vsr helium."
    )


if __name__ == "__main__":
    main()
