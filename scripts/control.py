import argparse
import fcntl
import hashlib
import json
import os
import signal
import shlex
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from scripts.browser_config import VIDEO_FLAGS, BROWSER, browser_environment
from scripts.extension_build import local_version

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".state"
PIDFILE = STATE / "engine.pid"
PYTHON = ROOT / ".venv/bin/python"


def health():
    with urllib.request.urlopen("http://127.0.0.1:8730/health", timeout=1) as r:
        return json.load(r)


def own_pid(file=PIDFILE):
    try:
        pid = int(file.read_text())
        env = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
        return pid if f"VSR_ROOT={ROOT}".encode() in env else None
    except (OSError, ValueError):
        return None


def browser_pid():
    # Chromium can make /proc/PID/environ unreadable even to its owner.
    # Verify the owner, executable argument, exact isolated profile and main
    # process role instead; never match the normal browser's processes.
    needle = f"--user-data-dir={STATE / 'helium-profile'}".encode()
    for proc in Path("/proc").iterdir():
        if not proc.name.isdecimal():
            continue
        try:
            args = (proc / "cmdline").read_bytes().split(b"\0")
            # Helium rewrites its process title into one space-separated argv.
            if len(args) == 2 and b" --" in args[0]:
                args = [a.encode() for a in shlex.split(args[0].decode())]
            if (
                proc.stat().st_uid == os.getuid()
                and args[0] == os.fsencode(BROWSER)
                and needle in args
                and not any(a.startswith(b"--type=") for a in args)
            ):
                return int(proc.name)
        except (OSError, ValueError):
            continue
    return None


def configure_extension():
    STATE.mkdir(exist_ok=True)
    with (STATE / "extension-build.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        _configure_extension()


def _configure_extension():
    # Importing the app creates the random local token with mode 0600.
    from vsr_service.app import TOKEN

    path = ROOT / "extension/config.js"
    path.write_text("const VSR_CONFIG = " + json.dumps({"token": TOKEN}) + ";\n")
    path.chmod(0o600)
    # Command-line unpacked installs can retain an older service worker even
    # after restarting with an updated manifest. Give the exact worker bytes
    # their own URL; a version bump alone did not invalidate Helium's cache.
    # Inline the private config so imported-script caches cannot mix builds.
    manifest_path = ROOT / "extension/manifest.json"
    manifest = json.loads((ROOT / "extension/manifest.template.json").read_text())
    manifest["background"]["service_worker"] = "background.js"
    build = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode())
    for asset in sorted(path.parent.iterdir()):
        if asset.is_file() and asset.suffix in {".js", ".html", ".css"}:
            build.update(asset.name.encode() + b"\0" + asset.read_bytes())
    source = (ROOT / "extension/background.js").read_text()
    worker = (
        f"// Extension build: {build.hexdigest()}\n"
        + path.read_text()
        + source.replace('importScripts("config.js");\n', "", 1)
    )
    digest = hashlib.sha256(worker.encode()).hexdigest()[:20]
    release = manifest["version"]
    version_file = STATE / "extension-build.json"
    previous = json.loads(version_file.read_text()) if version_file.exists() else None
    if manifest_path.exists():
        installed = json.loads(manifest_path.read_text())["version"]
        # A surviving generated manifest also preserves the last version if
        # the private build record was removed. Never assign random versions.
        if previous is None or tuple(map(int, installed.split("."))) > tuple(
            map(int, previous["version"].split("."))
        ):
            previous = {"version": installed}
    manifest["version"] = local_version(release, digest, previous)
    manifest["version_name"] = release
    generated = ROOT / "extension/generated"
    generated.mkdir(exist_ok=True)
    worker_path = generated / f"background-{digest}.js"
    if not worker_path.exists() or worker_path.read_text() != worker:
        worker_path.write_text(worker)
        worker_path.chmod(0o600)
    manifest["background"]["service_worker"] = f"generated/{worker_path.name}"
    encoded = json.dumps(manifest, indent=2) + "\n"
    if not manifest_path.exists() or manifest_path.read_text() != encoded:
        manifest_path.write_text(encoded)
    record = {"version": manifest["version"], "digest": digest}
    temporary = version_file.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(record) + "\n")
    temporary.chmod(0o600)
    temporary.replace(version_file)
    # Keep old entries while a browser may still need them. They contain the
    # same private config and are removed on the next configure with it closed.
    # Do not prune here: another profile may still be using an older build.
    # Generated workers are private and ignored; explicit cleanup requires
    # closing every browser that has loaded this directory.


def start(managed=False):
    native = STATE / "native/libvsr_nvenc.so"
    sources = [ROOT / "native/nvenc.cpp", ROOT / "scripts/build_nvenc.py"]
    if not native.exists() or any(
        p.stat().st_mtime > native.stat().st_mtime for p in sources
    ):
        subprocess.run([str(PYTHON), str(ROOT / "scripts/build_nvenc.py")], check=True)
    # Runtime startup must not rewrite an installed extension's worker URL.
    # Explicit configure/build and install commands own extension updates.
    if not (ROOT / "extension/manifest.json").exists():
        configure_extension()
    if own_pid():
        print("Engine already running.")
        return
    try:
        health()
    except Exception:
        pass
    else:
        raise SystemExit("Port 8730 is occupied by another service; not starting.")
    log = open(ROOT / "logs/engine.log", "a")
    child = subprocess.Popen(
        ["bash", str(ROOT / "scripts/serve.sh")],
        cwd=ROOT,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        env={**os.environ, "VSR_BROWSER_MANAGED": "1" if managed else "0"},
    )
    PIDFILE.write_text(str(child.pid))
    for _ in range(100):
        if child.poll() is not None:
            raise SystemExit("Engine failed to start; see logs/engine.log")
        try:
            if health().get("engine") == "NVIDIA VideoSuperRes":
                print("NVIDIA VSR engine: http://127.0.0.1:8730")
                return
        except Exception:
            pass
        time.sleep(0.1)
    raise SystemExit(
        "Engine startup is taking longer than expected; see logs/engine.log"
    )


def stop():
    pid = own_pid()
    if pid is None:
        print("No project-owned engine running.")
        return
    os.kill(pid, signal.SIGTERM)
    for _ in range(100):
        if own_pid() is None:
            PIDFILE.unlink(missing_ok=True)
            print("Engine stopped.")
            return
        time.sleep(0.1)
    raise SystemExit("Engine is still shutting down; see logs/engine.log")


def close_browser():
    pid = browser_pid()
    if pid is None:
        print("No project-owned browser running.")
        return
    os.kill(pid, signal.SIGTERM)
    for _ in range(100):
        if browser_pid() is None:
            (STATE / "browser.pid").unlink(missing_ok=True)
            print("Isolated Helium browser closed.")
            return
        time.sleep(0.1)
    raise SystemExit(
        "Helium is still shutting down; close its isolated window before cleanup."
    )


def browser(url):
    from scripts.browser_helper import install

    configure_extension()
    install(STATE / "helium-profile")
    start(managed=True)
    build = json.loads((ROOT / "extension/manifest.json").read_text())["background"][
        "service_worker"
    ]
    build_file = STATE / "browser-extension-build"
    launched_build = build_file.read_text() if build_file.exists() else None
    if browser_pid() and launched_build != build:
        print("Updating the extension in the isolated Helium window.")
        close_browser()
        configure_extension()
    # Bypass the distro wrapper so global user flags/extensions cannot contaminate this profile.
    args = [
        BROWSER,
        f"--user-data-dir={STATE / 'helium-profile'}",
        f"--load-extension={ROOT / 'extension'}",
        "--no-first-run",
        "--no-default-browser-check",
        "--password-store=basic",
        "--ozone-platform=wayland",
        "--disable-features=Vulkan",
        *VIDEO_FLAGS,
        url,
    ]
    with open(ROOT / "logs/helium.log", "a") as log:
        env = browser_environment()
        child = subprocess.Popen(
            args,
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    for _ in range(50):
        pid = browser_pid()
        if pid:
            (STATE / "browser.pid").write_text(str(pid))
            break
        if child.poll() is not None:
            raise SystemExit("Helium failed to start; see logs/helium.log")
        time.sleep(0.1)
    else:
        raise SystemExit("Helium has not opened yet; see logs/helium.log")
    build_file.write_text(build)
    print("Opened an isolated Helium profile. Your normal profile was not changed.")


def helium(url):
    """Open normal Helium without editing its flags or closing other windows."""
    wrapper = shutil.which("helium-browser") or shutil.which("helium")
    if not wrapper:
        raise SystemExit(
            "Helium launcher not found. Use ./vsr browser or load extension/ in your browser."
        )
    start(managed=True)
    with open(ROOT / "logs/helium-normal.log", "a") as log:
        subprocess.Popen(
            [wrapper, url],
            env=browser_environment(),
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    print("Opened normal Helium. Load/pin extension/ if it is not already installed.")


def main():
    parser = argparse.ArgumentParser(
        description="Local NVIDIA VSR engine and browser controls"
    )
    parser.add_argument(
        "command",
        choices=[
            "start",
            "managed-start",
            "install-browser-helper",
            "remove-browser-helper",
            "stop",
            "status",
            "probe",
            "browser",
            "helium",
            "install-helium",
            "remove-helium",
            "configure",
            "close-browser",
        ],
    )
    parser.add_argument("url", nargs="?", default="http://127.0.0.1:8730/")
    args = parser.parse_args()
    if args.command == "start":
        start()
    elif args.command == "managed-start":
        start(managed=True)
    elif args.command in {"install-browser-helper", "remove-browser-helper"}:
        from scripts.browser_helper import install, remove

        if args.command == "remove-browser-helper":
            remove()
        else:
            install()
    elif args.command == "stop":
        stop()
    elif args.command == "configure":
        configure_extension()
    elif args.command == "probe":
        raise SystemExit(
            subprocess.call([str(PYTHON), str(ROOT / "scripts/probe.py")], cwd=ROOT)
        )
    elif args.command == "status":
        try:
            print(json.dumps(health(), indent=2))
        except Exception:
            print("Engine offline.")
    elif args.command == "browser":
        browser(args.url)
    elif args.command == "helium":
        helium(args.url)
    elif args.command in {"install-helium", "remove-helium"}:
        configure_extension()
        subprocess.run(
            [
                str(PYTHON),
                str(ROOT / "scripts/install_helium.py"),
                *(["--remove"] if args.command == "remove-helium" else []),
            ],
            check=True,
        )
        from scripts.browser_helper import install, remove

        if args.command == "install-helium":
            install()
        else:
            remove()
    elif args.command == "close-browser":
        close_browser()


if __name__ == "__main__":
    main()
