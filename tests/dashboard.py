"""Exercise extension dashboard commands against real GPU video playback."""

import asyncio
import json
import tempfile
from pathlib import Path

from playwright.async_api import async_playwright
from scripts.browser_config import BROWSER, VIDEO_FLAGS, browser_environment

ROOT = Path(__file__).resolve().parents[1]


async def wait(page, expression, timeout=45000):
    # Extension CSP intentionally disallows eval; poll via CDP evaluation.
    for _ in range(timeout // 100):
        if await page.evaluate("() => (" + expression + ")"):
            return
        await asyncio.sleep(0.1)
    raise TimeoutError(expression + ": " + await page.locator("body").inner_text())


async def main():
    with tempfile.TemporaryDirectory(
        prefix="dashboard-", dir=ROOT / ".state"
    ) as profile:
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
                    "document.querySelector('#engine-dot').classList.contains('online') && !document.querySelector('#toggle').disabled",
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
                    "live_target_change_4k": True,
                    "hide_controls_keeps_playing": True,
                    "preview": True,
                }
                (ROOT / "artifacts/dashboard.json").write_text(
                    json.dumps(result, indent=2) + "\n"
                )
                print(json.dumps(result))
            finally:
                await context.close()


if __name__ == "__main__":
    asyncio.run(main())
