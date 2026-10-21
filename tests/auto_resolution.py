"""Real-browser Auto sizing, source changes, original bypass and persistence."""

import asyncio
import base64
import json
from pathlib import Path
import subprocess
import urllib.request

from websockets.asyncio.client import connect
from scripts.browser_config import BROWSER, VIDEO_FLAGS, browser_environment
from tests.startup import RealPage
from tests.native_performance import ROOT, wait

PROFILE = ROOT / ".state/helium-profile"


async def check(page, scale, width, height):
    await wait(
        page,
        f"""() => {{
        const p=document.querySelector('#local-nvidia-vsr'),c=document.querySelector('[data-nvidia-vsr-overlay]');
        return Number(p.dataset.effectiveScale)==={scale} && Number(c?.dataset.outputWidth||c?.width)==={width} && Number(c?.dataset.outputHeight||c?.height)==={height} && Number(p.dataset.frames)>35;
    }}""",
    )
    return await page.evaluate("""() => {
        const p=document.querySelector('#local-nvidia-vsr'),s=p.shadowRoot;
        return {factor:p.dataset.effectiveScale,target:p.dataset.target,
            dimensions:s.querySelector('#sizing').textContent,
            automatic:s.querySelector('#scale').value==='auto',
            target_enabled:!s.querySelector('#target').disabled,status:s.querySelector('#status').textContent};
    }""")


async def select(page, control, value):
    await page.evaluate(
        """arg => {
        const e=document.querySelector('#local-nvidia-vsr').shadowRoot.querySelector('#'+arg.control);
        e.value=arg.value;e.dispatchEvent(new Event('change'));
    }""",
        {"control": control, "value": value},
    )


async def main():
    # Call only with the project-owned isolated browser closed.
    clip = ROOT / "artifacts/auto-1080.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=1920x1080:rate=1",
            "-frames:v",
            "1",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "26",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(clip),
        ],
        check=True,
    )
    port_file = PROFILE / "DevToolsActivePort"
    port_file.unlink(missing_ok=True)
    with open(ROOT / "logs/auto-resolution.log", "w") as log:
        child = subprocess.Popen(
            [
                BROWSER,
                f"--user-data-dir={PROFILE}",
                f"--load-extension={ROOT / 'extension'}",
                "--remote-debugging-port=0",
                "--remote-debugging-address=127.0.0.1",
                "--ozone-platform=wayland",
                "--disable-features=Vulkan",
                "--no-first-run",
                "--no-default-browser-check",
                "--password-store=basic",
                *VIDEO_FLAGS,
                "http://127.0.0.1:8730/",
            ],
            env=browser_environment(),
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    try:
        for _ in range(200):
            if port_file.exists():
                break
            await asyncio.sleep(0.05)
        port = int(port_file.read_text().splitlines()[0])
        tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list"))
        tab = next(t for t in tabs if t["type"] == "page")
        async with connect(tab["webSocketDebuggerUrl"], max_size=8 * 1024 * 1024) as ws:
            page = RealPage(ws)
            await page.call("Page.bringToFront")
            await wait(
                page,
                "() => !!document.querySelector('#local-nvidia-vsr') && document.querySelector('video')?.videoWidth>0",
            )
            await asyncio.sleep(0.5)
            await select(page, "scale", "auto")
            await select(page, "target", "2160")
            await page.click("#toggle", True)
            report = {"360_to_4k": await check(page, 6, 3840, 2160)}
            await select(page, "target", "1080")
            report["360_to_1080"] = await check(page, 3, 1920, 1080)
            await select(page, "scale", "2")
            report["manual_2x"] = await check(page, 2, 1280, 720)
            assert report["manual_2x"]["target_enabled"] is False
            await select(page, "scale", "auto")
            await select(page, "target", "2160")
            await check(page, 6, 3840, 2160)
            await page.evaluate(
                "async () => {const v=document.querySelector('video');v.src='/sync.mp4';v.loop=true;await v.play();}"
            )
            report["automatic_source_resize"] = await check(page, 3, 3840, 2160)
            await select(page, "target", "720")
            await check(page, 1, 1280, 720)
            await page.evaluate(
                """async encoded => {
                const bytes=Uint8Array.from(atob(encoded),c=>c.charCodeAt(0));
                const v=document.querySelector('video');
                v.src=URL.createObjectURL(new Blob([bytes],{type:'video/mp4'}));
                v.loop=true;await v.play();
            }""",
                base64.b64encode(clip.read_bytes()).decode(),
            )
            await wait(
                page,
                "() => document.querySelector('#local-nvidia-vsr').dataset.stage==='original' && document.querySelector('video').videoHeight===1080",
            )
            report["above_target_original"] = await page.evaluate("""() => {
                const p=document.querySelector('#local-nvidia-vsr');
                return {active:p.dataset.active,stage:p.dataset.stage,status:p.dataset.status,
                    no_overlay:!document.querySelector('[data-nvidia-vsr-overlay]')};
            }""")
            assert report["above_target_original"]["no_overlay"]
            await page.click("#toggle", True)
            await select(page, "target", "2160")
            await page.call("Page.reload")
            await wait(
                page,
                "() => document.querySelector('#local-nvidia-vsr')?.shadowRoot.querySelector('#scale').value==='auto' && document.querySelector('#local-nvidia-vsr')?.shadowRoot.querySelector('#target').value==='2160'",
            )
            report["settings_persisted"] = True
            (ROOT / "artifacts/auto-resolution.json").write_text(
                json.dumps(report, indent=2) + "\n"
            )
            shot = await page.call("Page.captureScreenshot", {"format": "png"})
            (ROOT / "artifacts/auto-resolution-ui.png").write_bytes(
                base64.b64decode(shot["data"])
            )
            print(json.dumps(report, indent=2))
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
