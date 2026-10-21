"""Exercise extension dashboard commands against real GPU video playback."""

import asyncio
import argparse
import json
import subprocess
import tempfile
import time
from pathlib import Path

from playwright.async_api import async_playwright
from scripts.browser_config import BROWSER, VIDEO_FLAGS, browser_environment
from scripts.browser_helper import install
from scripts.control import health

ROOT = Path(__file__).resolve().parents[1]


async def wait(page, expression, timeout=45000):
    # Extension CSP intentionally disallows eval; poll via CDP evaluation.
    for _ in range(timeout // 100):
        if await page.evaluate("() => (" + expression + ")"):
            return
        await asyncio.sleep(0.1)
    raise TimeoutError(expression + ": " + await page.locator("body").inner_text())


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--on-demand", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(
        prefix="dashboard-", dir=ROOT / ".state"
    ) as profile:
        install(profile)
        async with async_playwright() as playwright:
            context = await playwright.chromium.launch_persistent_context(
                profile,
                executable_path=BROWSER,
                headless=False,
                ignore_default_args=["--disable-extensions"],
                args=[
                    f"--load-extension={ROOT / 'extension'}",
                    "--ozone-platform=wayland",
                    "--disable-features=Vulkan",
                    *VIDEO_FLAGS,
                ],
                env=browser_environment(),
                viewport={"width": 1280, "height": 900},
            )
            try:
                video = context.pages[0]
                await video.goto("http://127.0.0.1:8730/")
                await wait(video, "document.querySelector('video')?.readyState >= 2")
                if await video.evaluate("document.querySelector('video').paused"):
                    await video.locator("video").click()
                await wait(video, "!document.querySelector('video').paused")
                if args.on_demand:
                    subprocess.run(
                        [str(ROOT / "vsr"), "stop"],
                        check=True,
                        stdout=subprocess.DEVNULL,
                    )
                worker = (
                    context.service_workers[0]
                    if context.service_workers
                    else await context.wait_for_event("serviceworker")
                )
                extension_id = worker.url.split("/")[2]
                tab_id = await worker.evaluate(
                    "async () => (await chrome.tabs.query({url:'http://127.0.0.1:8730/*'}))[0].id"
                )
                dashboard = await context.new_page()
                await dashboard.goto(
                    f"chrome-extension://{extension_id}/popup.html?tab={tab_id}"
                )
                await wait(
                    dashboard,
                    "!document.querySelector('#toggle').disabled",
                )
                if args.on_demand:
                    assert not await dashboard.locator("#engine-dot").evaluate(
                        "e=>e.classList.contains('online')"
                    )
                assert not await dashboard.locator("#panel").is_checked()
                assert await video.evaluate(
                    "document.querySelector('#local-nvidia-vsr').style.display==='none'"
                )
                await dashboard.select_option("#target", "1080")
                await wait(
                    dashboard,
                    "document.querySelector('#factor').textContent.includes('3×')",
                )
                await dashboard.click("#toggle")
                await video.bring_to_front()
                await wait(
                    video,
                    "Number(document.querySelector('#local-nvidia-vsr').dataset.frames)>30",
                    timeout=45000,
                )
                await wait(
                    dashboard,
                    "document.querySelector('#state-tag').textContent==='LIVE'",
                )
                first_frame_ms = await video.evaluate(
                    "Number(document.querySelector('#local-nvidia-vsr').dataset.firstFrameMs)"
                )
                if args.on_demand:
                    assert health()["managed"] is True
                    await video.evaluate("document.querySelector('video').pause()")
                    for _ in range(200):
                        if not health()["loaded"]:
                            break
                        await asyncio.sleep(0.1)
                    else:
                        raise TimeoutError(
                            "Paused engine did not unload its GPU effect"
                        )
                    assert health()["active_streams"] == 1
                    await video.evaluate("document.querySelector('video').play()")
                    await wait(
                        video,
                        "Number(document.querySelector('#local-nvidia-vsr').dataset.frames)>60",
                    )
                # Hiding the page controls must leave enhancement running.
                await dashboard.locator("#panel").check()
                await video.bring_to_front()
                await wait(
                    video,
                    "document.querySelector('#local-nvidia-vsr').style.display==='block'",
                )
                await dashboard.locator("#panel").uncheck()
                await video.bring_to_front()
                await wait(
                    video,
                    "document.querySelector('#local-nvidia-vsr').style.display==='none'",
                )
                before = await video.evaluate(
                    "Number(document.querySelector('#local-nvidia-vsr').dataset.frames)"
                )
                await asyncio.sleep(1)
                after = await video.evaluate(
                    "Number(document.querySelector('#local-nvidia-vsr').dataset.frames)"
                )
                assert after > before, (before, after)
                await dashboard.select_option("#target", "2160")
                await video.bring_to_front()
                await wait(
                    video,
                    "document.querySelector('[data-nvidia-vsr-overlay]')?.dataset.outputWidth==='3840'",
                    timeout=45000,
                )
                await asyncio.sleep(2)
                await dashboard.screenshot(
                    path=str(ROOT / "artifacts/dashboard-live.png")
                )
                await dashboard.click("#toggle")
                await wait(
                    video,
                    "document.querySelector('#local-nvidia-vsr').dataset.active==='false'",
                )
                if args.on_demand:
                    deadline = time.monotonic() + 40
                    while time.monotonic() < deadline:
                        try:
                            health()
                        except OSError:
                            break
                        await asyncio.sleep(0.2)
                    else:
                        raise TimeoutError(
                            "Browser-managed engine did not shut down after idle"
                        )
                # The UI example has no engine or page dependency.
                await dashboard.goto(
                    f"chrome-extension://{extension_id}/popup.html?preview=1"
                )
                await wait(dashboard, "!document.querySelector('#toggle').disabled")
                await dashboard.click("#toggle")
                await dashboard.locator("main").screenshot(
                    path=str(ROOT / "examples/dashboard.png")
                )
                result = {
                    "dashboard_start_stop": True,
                    "first_frame_ms": first_frame_ms,
                    "live_target_change_4k": True,
                    "hide_controls_keeps_playing": True,
                    "preview": True,
                    "browser_managed_start_pause_unload_resume_idle_exit": args.on_demand,
                }
                (ROOT / "artifacts/dashboard.json").write_text(
                    json.dumps(result, indent=2) + "\n"
                )
                print(json.dumps(result))
            finally:
                await context.close()


if __name__ == "__main__":
    asyncio.run(main())
