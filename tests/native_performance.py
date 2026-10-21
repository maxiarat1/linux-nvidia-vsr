"""FPS measurement with CDP Network left disabled (no video-payload tracing)."""

import argparse
from contextlib import nullcontext
import asyncio
import base64
import json
import os
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

from websockets.asyncio.client import connect
from scripts.browser_config import BROWSER, VIDEO_FLAGS, browser_environment

ROOT = Path(__file__).resolve().parents[1]


class Page:
    def __init__(self, ws):
        self.ws, self.counter, self.events, self.aux = ws, 0, [], 0

    async def call(self, method, params=None):
        self.counter += 1
        await self.ws.send(
            json.dumps({"id": self.counter, "method": method, "params": params or {}})
        )
        while True:
            value = json.loads(await self.ws.recv())
            if "method" in value:
                self.events.append(value)
            if value.get("method") == "Target.attachedToTarget":
                session = value["params"]["sessionId"]
                for command, arguments in [
                    ("Media.enable", {}),
                    (
                        "Target.setAutoAttach",
                        {
                            "autoAttach": True,
                            "waitForDebuggerOnStart": True,
                            "flatten": True,
                        },
                    ),
                    ("Runtime.runIfWaitingForDebugger", {}),
                ]:
                    self.aux += 1
                    await self.ws.send(
                        json.dumps(
                            {
                                "id": -self.aux,
                                "sessionId": session,
                                "method": command,
                                "params": arguments,
                            }
                        )
                    )
            if value.get("id") != self.counter:
                continue
            if "error" in value:
                raise RuntimeError(value["error"])
            return value["result"]

    async def evaluate(self, fn, arg=None):
        result = await self.call(
            "Runtime.evaluate",
            {
                "expression": f"({fn})({json.dumps(arg)})",
                "returnByValue": True,
                "awaitPromise": True,
                "userGesture": True,
            },
        )
        if "exceptionDetails" in result:
            raise RuntimeError(result["exceptionDetails"])
        return result["result"].get("value")


async def wait(page, fn, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await page.evaluate(fn):
            return
        if "dataset.frames" in fn:
            error = await page.evaluate(
                "() => {const p=document.querySelector('#local-nvidia-vsr');return p?.dataset.active==='false'?p.shadowRoot.querySelector('#status').textContent:null;}"
            )
            if error:
                raise RuntimeError(error)
        await asyncio.sleep(0.1)
    raise TimeoutError(fn)


async def sample(page, seconds):
    await page.evaluate("() => globalThis.__beginCadence?.()")
    await page.evaluate("""() => {
      const v=document.querySelector('video'),p=document.querySelector('#local-nvidia-vsr');
      window.__fps={start:performance.now(),before:v.getVideoPlaybackQuality(),first:0,last:0,callbacks:0,enhanced:Number(p.dataset.frames||0),timings:[]};
      window.__fps.sampler=setInterval(()=>{if(p.dataset.timings)window.__fps.timings.push(JSON.parse(p.dataset.timings));},50);
      const tick=(t,m)=>{const s=window.__fps;s.callbacks++;s.first||=m.presentedFrames;s.last=m.presentedFrames;s.handle=v.requestVideoFrameCallback(tick);};
      window.__fps.handle=v.requestVideoFrameCallback(tick);
    }""")
    await asyncio.sleep(seconds)
    return await page.evaluate("""() => {
      const s=window.__fps,v=document.querySelector('video'),p=document.querySelector('#local-nvidia-vsr'),q=v.getVideoPlaybackQuality(),elapsed=(performance.now()-s.start)/1000;
      v.cancelVideoFrameCallback(s.handle);
      clearInterval(s.sampler);
      const timingSummary={};for(const key of Object.keys(s.timings[0]||{})){const a=s.timings.map(t=>t[key]).sort((a,b)=>a-b);timingSummary[key]={mean:a.reduce((x,y)=>x+y,0)/a.length,p50:a[Math.floor(a.length*.5)],p95:a[Math.floor(a.length*.95)]};}
      return {elapsed,source_fps:(s.last-s.first)/elapsed,callback_fps:s.callbacks/elapsed,
        enhanced_fps:(Number(p.dataset.frames||0)-s.enhanced)/elapsed,decoded_frames:q.totalVideoFrames-s.before.totalVideoFrames,
        dropped_frames:q.droppedVideoFrames-s.before.droppedVideoFrames,source:[v.videoWidth,v.videoHeight],
        cadence:globalThis.__endCadence?.(),timings:timingSummary,panel:{...p.dataset},status:p.shadowRoot.querySelector('#status').textContent};
    }""")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--youtube", action="store_true")
    parser.add_argument("--source-height", type=int, default=720)
    parser.add_argument("--scale", type=float, default=2)
    parser.add_argument(
        "--target-height", type=int, choices=[720, 1080, 1440, 2160, 4320]
    )
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--seconds", type=int, default=15)
    parser.add_argument("--label", default="native-jpeg")
    parser.add_argument("--transport", choices=["jpeg", "av1"], default="av1")
    parser.add_argument("--input-format", choices=["jpeg", "nv12"], default="nv12")
    parser.add_argument("--verify-controls", action="store_true")
    parser.add_argument("--sync-test", action="store_true")
    parser.add_argument("--audible", action="store_true")
    parser.add_argument("--audio-stress-ms", type=float, default=0)
    parser.add_argument("--min-fps", type=float)
    parser.add_argument("--compare-pacing", action="store_true")
    parser.add_argument("--main-thread-stress-ms", type=float, default=0)
    args = parser.parse_args()
    profile_context = (
        nullcontext(args.profile.resolve())
        if args.profile
        else tempfile.TemporaryDirectory(
            prefix="native-fps-", dir=ROOT / ".state", ignore_cleanup_errors=True
        )
    )
    with profile_context as profile:
        if args.profile:
            (Path(profile) / "DevToolsActivePort").unlink(missing_ok=True)
        url = (
            "https://www.youtube.com/watch?v=aqz-KE-bpKQ"
            if args.youtube
            else "http://127.0.0.1:8730/"
        )
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
                    "--window-size=1440,1000",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--password-store=basic",
                    "--autoplay-policy=no-user-gesture-required",
                    *VIDEO_FLAGS,
                    url,
                ],
                env=browser_environment(),
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        try:
            port_file = Path(profile) / "DevToolsActivePort"
            for _ in range(200):
                if port_file.exists():
                    break
                if child.poll() is not None:
                    raise RuntimeError("Browser failed to open")
                await asyncio.sleep(0.05)
            port = int(port_file.read_text().splitlines()[0])
            tabs = json.load(
                urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list")
            )
            tab = next(t for t in tabs if t["type"] == "page")
            async with connect(
                tab["webSocketDebuggerUrl"], max_size=8 * 1024 * 1024
            ) as ws:
                page = Page(ws)
                await page.call("Media.enable")
                await page.call(
                    "Target.setAutoAttach",
                    {
                        "autoAttach": True,
                        "waitForDebuggerOnStart": True,
                        "flatten": True,
                    },
                )
                if args.youtube:
                    for _ in range(60):
                        if await page.evaluate(
                            "() => {const b=[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='Reject all');if(b){b.click();return true;}return !!document.querySelector('video')?.videoWidth;}"
                        ):
                            break
                        await asyncio.sleep(0.2)
                await wait(
                    page,
                    "() => !!document.querySelector('#local-nvidia-vsr') && document.querySelector('video')?.videoWidth>0",
                )
                await page.evaluate(
                    "async audible => {const v=document.querySelector('video');v.muted=!audible;v.volume=.05;await v.play();}",
                    args.audible,
                )
                if args.youtube:
                    await page.evaluate(
                        "q => document.querySelector('#movie_player').setPlaybackQualityRange(q,q)",
                        f"hd{args.source_height}",
                    )
                    await wait(
                        page,
                        f"() => document.querySelector('video').videoHeight==={args.source_height}",
                    )
                await asyncio.sleep(2)
                if args.sync_test:
                    await page.evaluate(
                        "async () => {const v=document.querySelector('video');v.src='/sync.mp4';v.loop=true;v.volume=.05;v.muted=false;await v.play();}"
                    )
                    await asyncio.sleep(1)
                if args.sync_test or args.audible:
                    await page.call("Runtime.enable")
                    contexts = [
                        e["params"]["context"]
                        for e in page.events
                        if e["method"] == "Runtime.executionContextCreated"
                    ]
                    context = next(
                        c for c in contexts if c.get("name") == "Local NVIDIA VSR"
                    )
                    result = await page.call(
                        "Runtime.evaluate",
                        {
                            "contextId": context["id"],
                            "expression": f"globalThis.__audioStressMs={args.audio_stress_ms};"
                            + (ROOT / "tests/audio_probe.js").read_text(),
                            "returnByValue": True,
                        },
                    )
                    if "exceptionDetails" in result:
                        raise RuntimeError(result)
                await page.evaluate(
                    "() => {" + (ROOT / "tests/cadence_probe.js").read_text() + "}"
                )
                if args.main_thread_stress_ms:
                    await page.evaluate(
                        "ms => {window.__pacingLoad=setInterval(()=>{const until=performance.now()+ms;while(performance.now()<until){}},100);}",
                        args.main_thread_stress_ms,
                    )
                off = await sample(page, args.seconds)
                legacy = None
                if args.compare_pacing:
                    await page.evaluate(
                        "async () => {const v=document.querySelector('video');v.currentTime=20;await v.play();}"
                    )
                    await wait(page, "() => !document.querySelector('video').seeking")
                    await page.call("Runtime.enable")
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
                            "expression": "globalThis.__originalClock = VsrPresentationClock.prototype.mediaTime; globalThis.__originalLatency=VsrAudioSync.prototype.outputLatency; VsrAudioSync.prototype.outputLatency=function(){const c=this.graph?.context;return c?c.baseLatency+(c.outputLatency||0):0}; VsrPresentationClock.prototype.mediaTime = () => document.querySelector('video').currentTime;",
                        },
                    )
                    await page.evaluate(
                        "config => {const p=document.querySelector('#local-nvidia-vsr');p.dataset.renderer='legacy';p.shadowRoot.querySelector('#scale').value=String(config.scale);p.shadowRoot.querySelector('#toggle').click();}",
                        {"scale": args.scale},
                    )
                    await wait(
                        page,
                        "() => Number(document.querySelector('#local-nvidia-vsr').dataset.frames)>120",
                    )
                    await wait(
                        page, "() => document.querySelector('video').currentTime>=24"
                    )
                    legacy = await sample(page, args.seconds)
                    await page.evaluate(
                        "() => document.querySelector('#local-nvidia-vsr').shadowRoot.querySelector('#toggle').click()"
                    )
                    await page.call(
                        "Runtime.evaluate",
                        {
                            "contextId": context["id"],
                            "expression": "VsrPresentationClock.prototype.mediaTime = globalThis.__originalClock; VsrAudioSync.prototype.outputLatency=globalThis.__originalLatency;",
                        },
                    )
                    await page.evaluate(
                        "async () => {document.querySelector('#local-nvidia-vsr').dataset.renderer='worker';const v=document.querySelector('video');v.currentTime=20;await v.play();}"
                    )
                    await wait(page, "() => !document.querySelector('video').seeking")
                await page.evaluate(
                    "config => {const p=document.querySelector('#local-nvidia-vsr');p.dataset.outputFormat=config.format;p.dataset.inputFormat=config.input;p.shadowRoot.querySelector('#scale').value=config.target?'auto':String(config.scale);if(config.target)p.shadowRoot.querySelector('#target').value=String(config.target);p.shadowRoot.querySelector('#toggle').click();}",
                    {
                        "format": args.transport,
                        "input": args.input_format,
                        "scale": args.scale,
                        "target": args.target_height,
                    },
                )
                await wait(
                    page,
                    "() => Number(document.querySelector('#local-nvidia-vsr').dataset.frames)>30",
                )
                if args.compare_pacing:
                    await wait(
                        page, "() => document.querySelector('video').currentTime>=24"
                    )
                on = await sample(page, args.seconds)
                if args.audible:
                    result = await page.call(
                        "Runtime.evaluate",
                        {
                            "contextId": context["id"],
                            "expression": "JSON.stringify(globalThis.__syncReport())",
                            "returnByValue": True,
                        },
                    )
                    signal = json.loads(result["result"]["value"])
                    on["audio_signal"] = {
                        k: signal[k] for k in ["maxInput", "maxOutput", "audio"]
                    }
                    assert signal["maxInput"] > 1e-5 and signal["maxOutput"] > 1e-5, (
                        signal
                    )
                if args.sync_test:
                    result = await page.call(
                        "Runtime.evaluate",
                        {
                            "contextId": context["id"],
                            "expression": "JSON.stringify(globalThis.__syncReport())",
                            "returnByValue": True,
                        },
                    )
                    on["sync"] = json.loads(result["result"]["value"])
                    probe = on["sync"]
                    peaks = [p for p in probe["peaks"] if p["kind"] == "output"]
                    errors = []
                    for picture in probe["pictures"][2:]:
                        if abs(picture["mediaTime"] - picture["second"]) > 0.001:
                            continue
                        peak = min(
                            peaks, key=lambda p: abs(p["wall"] - picture["wall"])
                        )
                        delta = picture["wall"] - peak["wall"]
                        if abs(delta) < 500:
                            errors.append(delta)
                    assert len(errors) >= 3, probe
                    probe["flash_audio_error_ms"] = errors
                    probe["max_abs_error_ms"] = max(map(abs, errors))
                    probe["measurement"] = (
                        "Canvas submission vs AudioContext output timestamp estimate; excludes monitor scanout and acoustic measurement"
                    )
                    assert probe["max_abs_error_ms"] < 25, probe
                    if args.audio_stress_ms:
                        assert probe["audio"]["delay_ms"] > 10, probe
                media = [
                    p
                    for e in page.events
                    if e["method"] == "Media.playerPropertiesChanged"
                    for p in e["params"]["properties"]
                ]
                decoder = {
                    p["name"]: p["value"]
                    for p in media
                    if p["name"]
                    in [
                        "kVideoDecoderName",
                        "kIsPlatformVideoDecoder",
                        "kAudioDecoderName",
                    ]
                }
                report = {
                    "main_thread_stress_ms": args.main_thread_stress_ms,
                    "legacy": legacy,
                    "label": args.label,
                    "youtube": args.youtube,
                    "transport": args.transport,
                    "network_tracing": False,
                    "decoder": decoder,
                    "off": off,
                    "on": on,
                }
                players = {}
                for e in page.events:
                    if e["method"] != "Media.playerPropertiesChanged":
                        continue
                    props = players.setdefault(e["params"]["playerId"], {})
                    props.update(
                        {
                            p["name"]: p["value"]
                            for p in e["params"]["properties"]
                            if p["name"]
                            in [
                                "kVideoDecoderName",
                                "kIsPlatformVideoDecoder",
                                "kAudioDecoderName",
                                "kVideoTracks",
                            ]
                        }
                    )
                report["media_decoders"] = list(players.values())
                (ROOT / f"artifacts/performance-{args.label}.json").write_text(
                    json.dumps(report, indent=2) + "\n"
                )
                if args.transport == "av1":
                    size = await page.evaluate(
                        "() => {const c=document.querySelector('[data-nvidia-vsr-overlay]');return `${c.dataset.outputWidth||c.width}x${c.dataset.outputHeight||c.height}`;}"
                    )
                    assert any(
                        p.get("kVideoDecoderName") == "VaapiVideoDecoder"
                        and p.get("kIsPlatformVideoDecoder") == "true"
                        and size in p.get("kVideoTracks", "")
                        and "av1" in p.get("kVideoTracks", "")
                        for p in players.values()
                    ), players
                if args.verify_controls:
                    await page.evaluate("() => document.querySelector('video').pause()")
                    await wait(
                        page,
                        "() => document.querySelector('[data-nvidia-vsr-overlay]').style.display==='none'",
                    )
                    report["pause_restores_source"] = True
                    count = await page.evaluate(
                        "() => Number(document.querySelector('#local-nvidia-vsr').dataset.frames)"
                    )
                    await page.evaluate(
                        "async () => {const v=document.querySelector('video');v.currentTime+=2;await v.play();}"
                    )
                    await wait(
                        page,
                        f"() => Number(document.querySelector('#local-nvidia-vsr').dataset.frames)>{count + 10}",
                    )
                    report["seek_resumes_enhancement"] = True
                    if args.youtube:
                        await page.evaluate(
                            "() => document.querySelector('.ytp-fullscreen-button').click()"
                        )
                        await wait(
                            page,
                            "() => !!document.fullscreenElement && document.fullscreenElement!==document.querySelector('video')",
                        )
                        count = await page.evaluate(
                            "() => Number(document.querySelector('#local-nvidia-vsr').dataset.frames)"
                        )
                        await wait(
                            page,
                            f"() => Number(document.querySelector('#local-nvidia-vsr').dataset.frames)>{count + 10}",
                        )
                        report["youtube_fullscreen"] = True
                    point = await page.evaluate(
                        "() => {const b=document.querySelector('#local-nvidia-vsr').shadowRoot.querySelector('#ab').getBoundingClientRect();return {x:b.x+b.width/2,y:b.y+b.height/2};}"
                    )
                    await page.call(
                        "Input.dispatchMouseEvent",
                        {
                            "type": "mousePressed",
                            "button": "left",
                            "clickCount": 1,
                            **point,
                        },
                    )
                    await wait(
                        page,
                        "() => document.querySelector('[data-nvidia-vsr-overlay]').style.display==='none'",
                    )
                    await page.call(
                        "Input.dispatchMouseEvent",
                        {
                            "type": "mouseReleased",
                            "button": "left",
                            "clickCount": 1,
                            **point,
                        },
                    )
                    await wait(
                        page,
                        "() => document.querySelector('[data-nvidia-vsr-overlay]').style.display==='block'",
                    )
                    report["original_comparison"] = True
                    shot = await page.call("Page.captureScreenshot", {"format": "png"})
                    (ROOT / f"artifacts/{args.label}.png").write_bytes(
                        base64.b64decode(shot["data"])
                    )
                    await page.evaluate(
                        "async () => {if(document.fullscreenElement)await document.exitFullscreen();document.querySelector('#local-nvidia-vsr').shadowRoot.querySelector('#toggle').click();}"
                    )
                    assert await page.evaluate(
                        "() => !document.querySelector('[data-nvidia-vsr-overlay]') && !document.querySelector('video').paused"
                    )
                    report["toggle_off_restores_video"] = True
                    await asyncio.sleep(0.15)
                    audio = await page.evaluate(
                        "() => JSON.parse(document.querySelector('#local-nvidia-vsr').dataset.audio)"
                    )
                    assert audio["target_ms"] == 0, audio
                    report["toggle_off_clears_audio_delay"] = True
                    for _ in range(2):
                        await page.evaluate(
                            "() => document.querySelector('#local-nvidia-vsr').shadowRoot.querySelector('#toggle').click()"
                        )
                        await wait(
                            page,
                            "() => Number(document.querySelector('#local-nvidia-vsr').dataset.frames)>10",
                        )
                        await page.evaluate(
                            "() => document.querySelector('#local-nvidia-vsr').shadowRoot.querySelector('#toggle').click()"
                        )
                    report["repeated_audio_graph_reuse"] = True
                (ROOT / f"artifacts/performance-{args.label}.json").write_text(
                    json.dumps(report, indent=2) + "\n"
                )
                print(json.dumps(report, indent=2))
                assert on["panel"]["active"] == "true", on["status"]
                if args.min_fps:
                    assert on["enhanced_fps"] >= args.min_fps
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
