"""Small native-messaging control host. GPU/video data never uses this pipe."""

import fcntl
import json
import struct
import subprocess
import sys
from pathlib import Path

from scripts.browser_helper import extension_id
from scripts.control import health, own_pid

ROOT = Path(__file__).resolve().parents[1]
MAX_MESSAGE = 4096


def read_message(stream):
    header = stream.read(4)
    if not header:
        return None
    if len(header) != 4:
        raise ValueError("Incomplete message header")
    size = struct.unpack("=I", header)[0]
    if not 0 < size <= MAX_MESSAGE:
        raise ValueError("Control message is too large")
    data = stream.read(size)
    if len(data) != size:
        raise ValueError("Incomplete control message")
    return json.loads(data)


def write_message(stream, value):
    data = json.dumps(value).encode()
    stream.write(struct.pack("=I", len(data)) + data)
    stream.flush()


def status():
    try:
        info = health()
        if info.get("engine") != "NVIDIA VideoSuperRes" or not own_pid():
            raise RuntimeError("Loopback port is occupied by a different process")
        return {
            "ready": True,
            "loaded": info.get("loaded", False),
            "managed": info.get("managed", False),
        }
    except (OSError, TimeoutError):
        return {"ready": False}


def handle(message):
    if not isinstance(message, dict) or message.get("action") not in {
        "status",
        "ensure",
    }:
        raise ValueError("Unsupported browser-helper action")
    if message["action"] == "status":
        return status()
    # Serialize starts from multiple tabs/profiles; no shell or caller-supplied
    # executable, arguments, environment, paths, or frame payloads are accepted.
    with (ROOT / ".state/engine-start.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not status()["ready"]:
            with (ROOT / "logs/browser-helper.log").open("a") as log:
                subprocess.run(
                    [str(ROOT / "vsr"), "managed-start"],
                    cwd=ROOT,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=30,
                    check=True,
                )
        result = status()
        if not result["ready"]:
            raise RuntimeError(
                "Local engine did not become ready; see logs/browser-helper.log"
            )
        return result


def main():
    try:
        if len(sys.argv) < 2 or sys.argv[1] != f"chrome-extension://{extension_id()}/":
            raise ValueError("Browser helper caller is not authorized")
        message = read_message(sys.stdin.buffer)
        if message is None:
            return
        response = handle(message)
    except subprocess.SubprocessError:
        response = {
            "error": "The local engine could not start. See logs/browser-helper.log.",
            "code": "ENGINE_START_FAILED",
        }
    except Exception as error:
        response = {"error": str(error), "code": "ENGINE_START_FAILED"}
    write_message(sys.stdout.buffer, response)


if __name__ == "__main__":
    main()
