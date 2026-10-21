"""Prove composited output is above the video and below player controls."""

import asyncio
import io
import json
import tempfile
from pathlib import Path

from PIL import Image
from playwright.async_api import async_playwright

from scripts.browser_config import BROWSER, VIDEO_FLAGS, browser_environment
from scripts.browser_helper import install
from tests.dashboard import wait

ROOT = Path(__file__).resolve().parents[1]


async def pixel(page, x, y):
    image = Image.open(
        io.BytesIO(
            await page.screenshot(clip={"x": x, "y": y, "width": 8, "height": 8})
        )
    )
    return list(image.convert("RGB").getpixel((4, 4)))


def near_pixel(actual, expected):
    # Chromium/display color management can shift the screenshot RGB values.
    return max(abs(a - b) for a, b in zip(actual, expected)) <= 20


async def main():
    with tempfile.TemporaryDirectory(
        prefix="visible-output-", dir=ROOT / ".state"
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
                viewport={"width": 1440, "height": 1000},
            )
            try:
                page = context.pages[0]
                await page.goto("http://127.0.0.1:8730/")
                await wait(page, "document.querySelector('video')?.readyState>=2")
                # Emulate the actual YouTube stacking contexts without relying
                # on a changing network video or on counters as visual proof.
                await page.evaluate("""async () => {
                    const v=document.querySelector('video'),p=v.parentElement;
                    p.classList.add('html5-video-player');p.style.zIndex='0';
                    const layer=document.createElement('div');
                    layer.className='html5-video-container';
                    layer.style.cssText='position:relative;z-index:10';
                    v.before(layer);layer.append(v);
                    const button=document.createElement('button');
                    button.id='visibility-control';button.textContent='Player control';
                    button.style.cssText='position:absolute;z-index:59;left:12px;top:12px;background:lime;border:0;border-radius:0;width:140px;height:40px';
                    p.append(button);v.muted=true;await v.play();
                    document.querySelector('#local-nvidia-vsr').shadowRoot.querySelector('#toggle').click();
                }""")
                await page.bring_to_front()
                await wait(
                    page,
                    "Number(document.querySelector('#local-nvidia-vsr').dataset.frames)>20",
                )
                report = await page.evaluate("""() => {
                    const p=document.querySelector('#local-nvidia-vsr'),o=document.querySelector('[data-nvidia-vsr-overlay]'),v=document.querySelector('video');
                    return {frames:Number(p.dataset.frames),first_frame_ms:Number(p.dataset.firstFrameMs),
                        overlay_z:Number(getComputedStyle(o).zIndex),native_z:Number(getComputedStyle(v.parentElement).zIndex),
                        output:[Number(o.dataset.outputWidth),Number(o.dataset.outputHeight)],timings:JSON.parse(p.dataset.timings)};
                }""")
                bridge = page.frame_locator("[data-nvidia-vsr-overlay]")
                await bridge.locator("canvas").evaluate("c=>c.style.opacity='0'")
                point = await page.evaluate("""() => {
                    const o=document.querySelector('[data-nvidia-vsr-overlay]'),r=o.getBoundingClientRect();
                    o.style.background='rgb(251,17,173)';
                    return {x:r.x+r.width/2,y:r.y+r.height/2};
                }""")
                # Demonstrate the old failure, then test the shipped layer order.
                await page.evaluate(
                    "() => {const s=document.createElement('style');s.id='old-layer-rule';s.textContent='[data-nvidia-vsr-overlay]{z-index:1!important}';document.head.append(s);}"
                )
                hidden_pixel = await pixel(page, **point)
                assert not near_pixel(hidden_pixel, [251, 17, 173]), hidden_pixel
                await page.locator("#old-layer-rule").evaluate("s=>s.remove()")
                visible_pixel = await pixel(page, **point)
                assert near_pixel(visible_pixel, [251, 17, 173]), visible_pixel
                control = await page.locator("#visibility-control").bounding_box()
                control_pixel = await pixel(page, control["x"] + 2, control["y"] + 2)
                assert near_pixel(control_pixel, [0, 255, 0]), control_pixel
                await bridge.locator("canvas").evaluate("c=>c.style.opacity='1'")
                enhanced_pixel = await pixel(page, **point)
                assert not near_pixel(enhanced_pixel, [251, 17, 173]), enhanced_pixel
                await bridge.locator("canvas").evaluate("c=>c.style.opacity='0'")
                report.update(
                    {
                        "old_layer_pixel": hidden_pixel,
                        "fixed_layer_pixel": visible_pixel,
                        "controls_pixel": control_pixel,
                        "enhanced_picture_pixel": enhanced_pixel,
                    }
                )
                off = await page.evaluate("""() => {
                    const p=document.querySelector('#local-nvidia-vsr'),start=performance.now();
                    p.shadowRoot.querySelector('#toggle').click();
                    return {handler_ms:performance.now()-start,overlay_removed:!document.querySelector('[data-nvidia-vsr-overlay]'),original_playing:!document.querySelector('video').paused};
                }""")
                assert off["overlay_removed"] and off["original_playing"], off
                report["off"] = off
                report["off_pixel"] = await pixel(page, **point)
                assert not near_pixel(report["off_pixel"], [251, 17, 173])
                (ROOT / "artifacts/visible-output.json").write_text(
                    json.dumps(report, indent=2) + "\n"
                )
                print(json.dumps(report, indent=2))
            finally:
                await context.close()


if __name__ == "__main__":
    asyncio.run(main())
