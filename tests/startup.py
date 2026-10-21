"""Exercise installed-profile upgrades and trusted clicks with normal autoplay."""

import argparse
import asyncio
from contextlib import nullcontext
import json
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.request

from websockets.asyncio.client import connect

from tests.native_performance import Page, ROOT
from scripts.browser_config import BROWSER, VIDEO_FLAGS, browser_environment


class RealPage(Page):
    async def evaluate(self, fn, arg=None):
        result = await self.call(
            "Runtime.evaluate",
            {
                "expression": f"({fn})({json.dumps(arg)})",
                "returnByValue": True,
                "awaitPromise": True,
                "userGesture": False,
            },
        )
        if "exceptionDetails" in result:
            raise RuntimeError(result["exceptionDetails"])
        return result["result"].get("value")

    async def click(self, selector, shadow=False):
        if shadow:
            # Page controls are optional; the toolbar is the default UI.
            await self.evaluate(
                "() => {document.querySelector('#local-nvidia-vsr').style.display='block';}"
            )
        point = await self.evaluate(
            """arg => {
                const e = arg.shadow
                    ? document.querySelector('#local-nvidia-vsr').shadowRoot.querySelector(arg.selector)
                    : document.querySelector(arg.selector);
                if (!e) return null;
                const r = e.getBoundingClientRect();
                return r.width && r.height ? {x:r.x+r.width/2, y:r.y+r.height/2} : null;
            }""",
            {"selector": selector, "shadow": shadow},
        )
        if not point:
            return False
        for kind in ["mousePressed", "mouseReleased"]:
            await self.call(
                "Input.dispatchMouseEvent",
                {"type": kind, "button": "left", "clickCount": 1, **point},
            )
        return True


STATE = """() => {
    const p=document.querySelector('#local-nvidia-vsr'), v=document.querySelector('video');
    return {dataset:{...p.dataset}, status:p.shadowRoot.querySelector('#status').textContent,
        toggle:p.shadowRoot.querySelector('#toggle').textContent,
        paused:v.paused, width:v.videoWidth, height:v.videoHeight};
}"""


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--idle-seconds", type=float, default=35)
    parser.add_argument("--label", default="startup")
    parser.add_argument("--url", default="http://127.0.0.1:8730/")
    args = parser.parse_args()
    profile_context = (
        nullcontext(args.profile.resolve())
        if args.profile
        else tempfile.TemporaryDirectory(
            prefix="startup-", dir=ROOT / ".state", ignore_cleanup_errors=True
        )
    )
    with profile_context as profile:
        profile = Path(profile)
        with open(ROOT / f"logs/{args.label}.log", "w") as log:
            child = subprocess.Popen(
                [
                    BROWSER,
                    f"--user-data-dir={profile}",
                    f"--load-extension={ROOT / 'extension'}",
                    "--remote-debugging-port=0",
                    "--remote-debugging-address=127.0.0.1",
                    "--ozone-platform=wayland",
                    "--disable-features=Vulkan",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--password-store=basic",
                    *VIDEO_FLAGS,
                    *(["--restore-last-session"] if args.profile else [args.url]),
                ],
                env=browser_environment(),
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        try:
            port_file = profile / "DevToolsActivePort"
            for _ in range(200):
                if port_file.exists():
                    try:
                        port = int(port_file.read_text().splitlines()[0])
                        tabs = json.load(
                            urllib.request.urlopen(
                                f"http://127.0.0.1:{port}/json/list", timeout=1
                            )
                        )
                        break
                    except (OSError, ValueError):
                        pass
                if child.poll() is not None:
                    raise RuntimeError("Browser failed to open")
                await asyncio.sleep(0.05)
            else:
                raise TimeoutError("Browser debugging port")
            tab = next(t for t in tabs if t["type"] == "page")
            async with connect(
                tab["webSocketDebuggerUrl"], max_size=8 * 1024 * 1024
            ) as ws:
                page = RealPage(ws)
                await page.call("Runtime.enable")
                await page.call("Page.bringToFront")
                for _ in range(150):
                    consent = await page.evaluate("""() => {
                        const e=[...document.querySelectorAll('button')].find(e=>e.textContent.trim()==='Reject all');
                        if (!e) return null;
                        const r=e.getBoundingClientRect();
                        return r.width && r.height ? {x:r.x+r.width/2,y:r.y+r.height/2} : null;
                    }""")
                    if consent:
                        for kind in ["mousePressed", "mouseReleased"]:
                            await page.call(
                                "Input.dispatchMouseEvent",
                                {
                                    "type": kind,
                                    "button": "left",
                                    "clickCount": 1,
                                    **consent,
                                },
                            )
                    if await page.evaluate(
                        "() => document.querySelector('video')?.readyState>=2 && !!document.querySelector('#local-nvidia-vsr')"
                    ):
                        break
                    await asyncio.sleep(0.2)
                else:
                    raise TimeoutError("Source video and VSR panel did not load")
                for _ in range(5):
                    if not await page.evaluate(
                        "() => document.querySelector('video').paused"
                    ):
                        break
                    if not await page.click(".ytp-play-button"):
                        await page.click("video")
                    await asyncio.sleep(1)
                before = await page.evaluate(STATE)
                assert not before["paused"], before
                await page.evaluate(
                    "() => document.querySelector('#movie_player')?.setPlaybackQualityRange('hd720','hd720')"
                )
                # No service-worker debugger connection or extension message
                # keeps the worker alive during this wait.
                await asyncio.sleep(args.idle_seconds)
                await page.click("#toggle", True)
                states = []
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    states.append(await page.evaluate(STATE))
                    if states[-1]["dataset"].get("lastError"):
                        raise AssertionError(states[-1])
                    if int(states[-1]["dataset"].get("frames", "0")) > 60:
                        break
                    await asyncio.sleep(0.2)
                else:
                    raise AssertionError(states[-1])
                await page.click("#toggle", True)
                off = await page.evaluate(STATE)
                assert off["dataset"]["active"] == "false", off
                # A lost receiver must leave a persistent, readable error and
                # must allow retry. Fail one request in the isolated world.
                contexts = [
                    e["params"]["context"]
                    for e in page.events
                    if e["method"] == "Runtime.executionContextCreated"
                ]
                context = next(
                    c for c in contexts if c.get("name") == "Local NVIDIA VSR"
                )
                await page.call(
                    "Runtime.evaluate",
                    {
                        "contextId": context["id"],
                        "expression": """
                    { const send = chrome.runtime.sendMessage;
                      chrome.runtime.sendMessage = (...args) => {
                        chrome.runtime.sendMessage = send;
                        return Promise.reject(new Error('Could not establish connection. Receiving end does not exist.'));
                      };
                    }
                """,
                    },
                )
                await page.click("#toggle", True)
                await asyncio.sleep(1)
                error = await page.evaluate(STATE)
                assert "background is unavailable" in error["status"], error
                assert error["dataset"]["lastError"] == error["status"], error
                await asyncio.sleep(1)
                assert (await page.evaluate(STATE))["status"] == error["status"]
                await page.click("#toggle", True)
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    retry = await page.evaluate(STATE)
                    if int(retry["dataset"].get("frames", "0")) > 60:
                        break
                    await asyncio.sleep(0.2)
                else:
                    raise AssertionError(retry)
                report = {
                    "normal_autoplay_policy": True,
                    "idle_seconds": args.idle_seconds,
                    "before": before,
                    "states": states,
                    "off": off,
                    "injected_failure": error,
                    "retry": retry,
                }
                (ROOT / f"artifacts/{args.label}.json").write_text(
                    json.dumps(report, indent=2) + "\n"
                )
                print(
                    json.dumps(
                        {
                            "passed": True,
                            "idle_seconds": args.idle_seconds,
                            "initial_frames": states[-1]["dataset"]["frames"],
                            "retry_frames": retry["dataset"]["frames"],
                            "status": retry["status"],
                        },
                        indent=2,
                    )
                )
                await page.click("#toggle", True)
        finally:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
            await asyncio.sleep(0.5)


if __name__ == "__main__":
    asyncio.run(main())
