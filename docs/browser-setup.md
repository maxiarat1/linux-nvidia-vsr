# Browser setup and troubleshooting

## Isolated profile

`./vsr browser [URL]` starts the engine and a project-owned profile in `.state/helium-profile`. The extension and known-working NVIDIA/Wayland flags are passed only to that process. `./vsr close-browser` matches the exact project profile and user before terminating it. Normal profiles are not closed by these commands.

Browser discovery prefers known real binaries; override it with an absolute `VSR_BROWSER` path. Helium is verified. Other Chromium builds may differ in VA-API, WebCodecs, unpacked extension policy, driver workarounds and iframe rendering behavior.

## Regular Helium

For Arch-packaged Helium, `./vsr install-helium` adds the extension and the verified flags to `helium-browser-flags.conf`, preserving other extensions and feature lists. It saves the original configuration inside `.state/helium-integration.json`, registers the on-demand browser helper, and does not restart the browser. `./vsr remove-helium` restores that configuration if it is still unchanged; otherwise it preserves your later edits and reports how to remove the VSR path manually.

For manual setup, run `./vsr install-browser-helper` once, then load `extension/` through Developer mode at `chrome://extensions`. Keep that directory in place. To use command-line loading on Helium, append its absolute path to the existing `--load-extension` comma-separated list in the distro's flags configuration, preserving other extensions. A browser restart is required for new process flags; extension reload plus a page refresh is required after source updates.

The verified GPU decoding arguments are:

```text
--enable-features=AcceleratedVideoDecodeLinuxGL,VaapiOnNvidiaGPUs
--ignore-gpu-blocklist
--use-gl=angle
--use-angle=gl
--ozone-platform=wayland
--disable-features=Vulkan
```

The verified environment includes `LIBVA_DRIVER_NAME=nvidia` and a working nvidia-vaapi-driver installation. Do not assume that `VideoDecoder.isConfigSupported` proves hardware decoding: the codec probe inspects actual Media events. Browser flags can change between Chromium versions; inspect `scripts/browser_config.py` and upstream driver documentation before applying them globally. Preserve existing enabled/disabled feature lists when combining flags.

`./vsr helium [URL]` is a convenience launcher for an already-installed Helium extension in the normal profile. It starts a browser-managed backend for the local demo, applies this environment, then invokes the distro's `helium-browser` wrapper so existing normal-profile settings and other extensions are retained. It does not restart or modify a running browser automatically. Flags/environment only take effect when a new browser process starts.

The toolbar popup controls the active tab. Its expand button opens a larger extension page bound to that video tab. Return to the video tab after changing controls: hidden video tabs intentionally stop VSR capture. The full dashboard refreshes state while it is visible.

## Troubleshooting

| Symptom                                               | Check                                                                                                                                                                                                                                                                                                                                     |
| ----------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Engine stopped                                        | Normal while VSR is off. Enable for this video starts the engine. For missing-helper guidance, run `./vsr install-browser-helper` once; launch errors are in `logs/browser-helper.log`.                                                                                                                                                   |
| Receiving end does not exist / background unavailable | Run `./vsr configure`, reload the extension at `chrome://extensions`, and refresh the video page. For the isolated window, rerun `./vsr browser`. The generated worker URL and extension build version must change together. A stale registration can be rejected before any listener starts; starting the GPU engine cannot repair that. |
| No playable video                                     | Start the video, wait for dimensions to load, and reopen the dashboard. Embedded cross-origin frames are not a general supported target.                                                                                                                                                                                                  |
| Protected / cross-origin frames                       | DRM or a non-origin-clean player cannot be processed through this browser API.                                                                                                                                                                                                                                                            |
| Lower enhanced fps / motion jumps                     | Compare source fps/drops to enhanced fps. Reduce target/quality, inspect component timing, verify actual hardware decoding and keep the window on one display. Auto target is not automatic quality/fps adaptation.                                                                                                                       |
| Audio differs after stopping                          | Extra delay is removed, but Web Audio routing persists until page refresh. Refresh for native routing.                                                                                                                                                                                                                                    |
| Source above target                                   | Auto intentionally shows the original instead of downscaling. Choose a larger target or manual scale if desired.                                                                                                                                                                                                                          |
| Encoder dimensions / memory error                     | Respect reported hardware bounds or select a smaller target. No fixed 1080p/4K application cap exists.                                                                                                                                                                                                                                    |
| File moved / extension disappeared                    | Reconfigure from its new location and load the new directory in Developer mode. Per-profile extension IDs can depend on the unpacked path.                                                                                                                                                                                                |

The source service worker reads `config.js`. `./vsr configure` inlines its private data into `extension/generated/background-<hash>.js` and generates `manifest.json` from `manifest.template.json`. Do not edit generated files. They are deliberately excluded from Git. The generated four-part manifest version changes with its worker hash. Runtime engine startup does not rebuild the extension. Old workers are kept until all profiles using them are closed; do not delete a worker still referenced by a loaded browser.

## Remove

For an isolated installation, close the project browser and stop the engine. Delete the clone to remove its runtime, cache, generated media and profile.

Run `./vsr remove-browser-helper` to remove this clone's native-messaging registration links.

For a normal-profile installation, remove Local NVIDIA VSR at `chrome://extensions` and remove its path from any `--load-extension` flag. Remove only GPU flags that you added for this project, preserving other feature lists and unrelated extensions. Close affected pages to release their audio graphs. Then stop the engine and delete the clone. The installer does not install a system service, desktop entry or global package.

## Automatic startup and resource use

The browser extension requires a local native process to use CUDA. The helper launches it only when you enable enhancement. Merely opening the popup does not initialize the GPU. GPU processing stops with incoming frames; the effect is unloaded after 15 seconds paused, and the full browser-managed engine exits 30 seconds after its last stream closes. This is separate from a persistent service explicitly started with `./vsr start` for API development.

The default helper registration targets Helium's user-data directory and the project-owned isolated profile. For another Chromium profile, use the Python `scripts.browser_helper.install(profile_path)` function with that browser's actual user-data root; other browsers need verification. All runtime files stay in the clone except the required per-profile registration symlink. Remove registration before deleting or moving the clone.
