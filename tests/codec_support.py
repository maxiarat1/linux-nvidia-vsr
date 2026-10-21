from scripts.browser_config import BROWSER

"""Probe real Helium codec paths without changing the working VSR pipeline."""

import asyncio
import argparse
import base64
import json
import os
from pathlib import Path
import subprocess
import tempfile
import urllib.request

from websockets.asyncio.client import connect
from native_performance import Page, wait, ROOT

CAPABILITIES = r"""async () => {
 const rows=[];
 for(const [name,codec] of [['AV1','av01.0.08M.08'],['H264','avc1.640028'],['VP9','vp09.00.40.08'],['HEVC','hvc1.1.6.L120.B0']]) {
  for(const hardwareAcceleration of ['no-preference','prefer-hardware','prefer-software']) {
   const config={codec,codedWidth:2560,codedHeight:1440,hardwareAcceleration,optimizeForLatency:true};
   let decoder,encoder;
   try{decoder=(await VideoDecoder.isConfigSupported(config)).supported;}catch(e){decoder=e.message;}
   try{encoder=(await VideoEncoder.isConfigSupported({codec,width:1280,height:720,framerate:60,bitrate:20000000,hardwareAcceleration,latencyMode:'realtime'})).supported;}catch(e){encoder=e.message;}
   rows.push({name,hardwareAcceleration,decoder,encoder});
  }
 }
 return {userAgent:navigator.userAgent,rows};
}"""

DECODE = r"""async args => {
 const data=Uint8Array.from(atob(args.base64),c=>c.charCodeAt(0)), view=new DataView(data.buffer);
 const config={codec:'av01.0.08M.08',codedWidth:1280,codedHeight:720,hardwareAcceleration:args.preference,optimizeForLatency:true};
 if(!(await VideoDecoder.isConfigSupported(config)).supported)return {preference:args.preference,supported:false};
 let next,fail,frames=0,timings=[],errors=[];
 const decoder=new VideoDecoder({output:async frame=>{
   const start=performance.now();
   try{const bitmap=await createImageBitmap(frame);bitmap.close();frames++;next({bitmap_ms:performance.now()-start});}
   catch(e){fail(e);}finally{frame.close();}
 },error:e=>{errors.push(e.message);fail?.(e);}});
 try{
  decoder.configure(config);
  for(let offset=32;offset<data.length;){
   const length=view.getUint32(offset,true),timestamp=Number(view.getBigUint64(offset+4,true));offset+=12;
   const start=performance.now();
   const result=await new Promise((resolve,reject)=>{
    next=resolve;fail=reject;
    decoder.decode(new EncodedVideoChunk({type:'key',timestamp:Math.round(timestamp*1000000/60),data:data.subarray(offset,offset+length)}));
   });
   timings.push({decode_and_bitmap_ms:performance.now()-start,...result});offset+=length;
  }
  await decoder.flush();return {preference:args.preference,supported:true,frames,timings,errors};
 }catch(e){return {preference:args.preference,supported:true,frames,error:e.message,errors};}
 finally{decoder.close();}
}"""


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--vaapi", action="store_true")
    args = parser.parse_args()
    sample = ROOT / "artifacts/probe-av1.ivf"
    if not sample.exists():
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=1280x720:rate=60",
                "-frames:v",
                "12",
                "-c:v",
                "av1_nvenc",
                "-preset",
                "p1",
                "-tune",
                "ull",
                "-zerolatency",
                "1",
                "-rc",
                "constqp",
                "-qp",
                "18",
                "-g",
                "1",
                "-f",
                "ivf",
                "-y",
                str(sample),
            ],
            check=True,
        )
    label = "codec-support-vaapi" if args.vaapi else "codec-support"
    flags = (
        [
            "--enable-features=AcceleratedVideoDecodeLinuxGL,VaapiOnNvidiaGPUs",
            "--ignore-gpu-blocklist",
            "--use-gl=angle",
            "--use-angle=gl",
        ]
        if args.vaapi
        else []
    )
    report = {"experimental_flags": flags}
    with tempfile.TemporaryDirectory(
        prefix="codec-probe-", dir=ROOT / ".state", ignore_cleanup_errors=True
    ) as profile:
        with open(ROOT / f"logs/{label}.log", "w") as log:
            child = subprocess.Popen(
                [
                    BROWSER,
                    f"--user-data-dir={profile}",
                    "--remote-debugging-port=0",
                    "--remote-debugging-address=127.0.0.1",
                    "--ozone-platform=wayland",
                    "--disable-features=Vulkan",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--password-store=basic",
                    "--autoplay-policy=no-user-gesture-required",
                    *flags,
                    "http://127.0.0.1:8730/",
                ],
                env={
                    **os.environ,
                    **({"LIBVA_DRIVER_NAME": "nvidia"} if args.vaapi else {}),
                    "LD_LIBRARY_PATH": "/opt/helium-browser-bin:"
                    + os.environ.get("LD_LIBRARY_PATH", ""),
                },
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        try:
            port_file = Path(profile) / "DevToolsActivePort"
            for _ in range(200):
                if port_file.exists():
                    break
                if child.poll() is not None:
                    raise RuntimeError("Helium failed")
                await asyncio.sleep(0.05)
            lines = port_file.read_text().splitlines()
            port = int(lines[0])
            async with connect(f"ws://127.0.0.1:{port}{lines[1]}") as ws:
                browser = Page(ws)
                system = await browser.call("SystemInfo.getInfo")
                report["gpu"] = system["gpu"]
            tabs = json.load(
                urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list")
            )
            tab = next(t for t in tabs if t["type"] == "page")
            async with connect(
                tab["webSocketDebuggerUrl"], max_size=8 * 1024 * 1024
            ) as ws:
                page = Page(ws)
                await page.call("Media.enable")
                report["capabilities"] = await page.evaluate(CAPABILITIES)
                await wait(page, "() => document.querySelector('video')?.readyState>=2")
                await page.evaluate(
                    "async () => {const v=document.querySelector('video');v.muted=true;await v.play();}"
                )
                await asyncio.sleep(0.5)
                report["raw_capture"] = await page.evaluate(
                    (ROOT / "tests/raw_capture_probe.js").read_text()
                )
                data = base64.b64encode(
                    (ROOT / "artifacts/probe-av1.ivf").read_bytes()
                ).decode()
                report["av1_actual_decode"] = []
                for preference in [
                    "prefer-hardware",
                    "no-preference",
                    "prefer-software",
                ]:
                    report["av1_actual_decode"].append(
                        await page.evaluate(
                            DECODE, {"preference": preference, "base64": data}
                        )
                    )
                report["webcodecs_media_events"] = [
                    e for e in page.events if e["method"].startswith("Media.")
                ]
                page.events = []
                await page.call(
                    "Page.navigate",
                    {"url": "https://www.youtube.com/watch?v=aqz-KE-bpKQ"},
                )
                for _ in range(60):
                    if await page.evaluate(
                        "() => {const b=[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='Reject all');if(b){b.click();return true;}return false;}"
                    ):
                        break
                    await asyncio.sleep(0.2)
                await wait(page, "() => document.querySelector('video')?.videoWidth>0")
                await page.evaluate(
                    "async () => {const v=document.querySelector('video');v.muted=true;await v.play();document.querySelector('#movie_player').setPlaybackQualityRange('hd720','hd720');}"
                )
                await asyncio.sleep(5)
                report["youtube_player"] = await page.evaluate(
                    "() => {const p=document.querySelector('#movie_player'),v=document.querySelector('video');return {source:[v.videoWidth,v.videoHeight],stats:p.getVideoStats?.(),nerds:p.getStatsForNerds?.(),quality:v.getVideoPlaybackQuality()};}"
                )
                report["youtube_media_events"] = [
                    e for e in page.events if e["method"].startswith("Media.")
                ]
            (ROOT / f"artifacts/{label}.json").write_text(
                json.dumps(report, indent=2) + "\n"
            )
            print(
                json.dumps(
                    {
                        k: v
                        for k, v in report.items()
                        if k
                        not in ["gpu", "webcodecs_media_events", "youtube_media_events"]
                    },
                    indent=2,
                )
            )
            props = [
                p
                for e in report["youtube_media_events"]
                if e["method"] == "Media.playerPropertiesChanged"
                for p in e["params"]["properties"]
            ]
            print("YOUTUBE ACTUAL PROPERTIES:", json.dumps(props, indent=2))
            print(
                "GPU:",
                json.dumps(
                    {k: v for k, v in report["gpu"].items() if k != "auxAttributes"},
                    indent=2,
                ),
            )
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
