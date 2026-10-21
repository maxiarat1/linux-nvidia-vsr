# Development

Read [AGENTS.md](AGENTS.md) and [docs/architecture.md](docs/architecture.md) first. Changes should preserve real NVIDIA AI processing, bounded frame queues, original playback on failure, private bridge authorization, and timestamp-based presentation.

## Layout

| Directory      | Purpose                                                                    |
| -------------- | -------------------------------------------------------------------------- |
| `extension/`   | MV3 source, toolbar dashboard, capture, trusted bridge and worker renderer |
| `vsr_service/` | Loopback API, NVIDIA VFX integration and GPU transport                     |
| `native/`      | Small C ABI NVENC adapter                                                  |
| `scripts/`     | Installation, local environment, lifecycle and diagnostic tools            |
| `tests/`       | Timing unit tests, API/GPU correctness and real-browser checks             |
| `examples/`    | Dashboard preview, generated-video player and external image client        |
| `docs/`        | Architecture, protocol, measured results and browser troubleshooting       |

`.state/`, `.cache/`, `.venv/`, `artifacts/` and `logs/` are local, ignored output. `extension/manifest.template.json` is the source manifest; `./vsr configure` creates the install-specific `manifest.json`, `config.js` and hashed background worker. Never commit these generated credentials.

## Checks without a GPU

```bash
node --test tests/presentation.test.cjs
python -m unittest tests.test_helium_install tests.test_browser_helper
for file in extension/*.js; do node --check "$file"; done
python -m compileall -q scripts vsr_service tests examples
```

The CI workflow performs these checks without installing NVIDIA packages. It does not establish runtime GPU or browser compatibility.

## Integration checks on a supported system

Install with `scripts/install.sh`, then install optional development dependencies locally:

```bash
bash -c 'source scripts/env.sh; uv pip install --python .venv/bin/python -r requirements-dev.lock'
./vsr start
bash -c 'source scripts/env.sh; .venv/bin/python tests/smoke_api.py'
bash -c 'source scripts/env.sh; .venv/bin/python tests/gpu_transport.py'
bash -c 'source scripts/env.sh; .venv/bin/python -m tests.visible_output'
bash -c 'source scripts/env.sh; .venv/bin/python -m tests.dashboard --on-demand'
bash -c 'source scripts/env.sh; .venv/bin/python -m tests.startup'
```

The visible-output check verifies composited pixels and player-control layering; a frame counter alone missed a real YouTube overlay occlusion bug. It also checks immediate stop and records first-frame startup timing.

The dashboard check uses actual GPU output, controls the extension UI, changes the live target to 4K, verifies that hiding page controls keeps playback running, and checks stop. `--on-demand` stops the preloaded fixture service, verifies native browser startup without terminal intervention, pauses for effect unload, resumes real GPU frames, then waits for the managed process to exit after stop. Browser checks create temporary profiles inside `.state/`. They can open visible windows. Do not run concurrent GPU benchmarks.

For source resolution switching and saved preferences, close the project-owned browser first:

```bash
./vsr close-browser
bash -c 'source scripts/env.sh; .venv/bin/python -m tests.auto_resolution'
```

For a moving 720p60 flash/beep sync fixture:

```bash
./scripts/make-sync.sh
bash -c 'source scripts/env.sh; .venv/bin/python -m tests.native_performance --sync-test --audible --scale 2 --target-height 2160 --seconds 10 --label sync-check --verify-controls'
```

For same-scene UI-load cadence comparison, use `--youtube --source-height 1080 --scale 2 --target-height 2160 --compare-pacing --main-thread-stress-ms 8 --seconds 10`. Reports and screenshots go into `artifacts/`, which is never committed. Measure source fps/drops and presentation gaps as well as mean enhanced fps. Record display refresh, scene, warm-up and window placement. Software A/V checks exclude physical scanout and speaker latency.

`tests/resolution_support.py` checks higher resolutions and recovery after an oversized request. `tests/codec_support.py --vaapi` inspects real Media decoder names, rather than trusting API capability flags. `scripts/probe_nvenc.py` reproduces the Python wrapper queue investigation; it is not the production encoder.

## Updates

Do not call configure during runtime engine startup. A changed service-worker URL needs a changed Chromium manifest version; the generated local version handles this. Verify updates in an existing profile as well as a fresh test profile.

After editing extension assets, run `./vsr configure`, then reload the extension and video page in your regular browser. `./vsr browser` automatically restarts only its isolated profile if its extension build changed. Never edit profile Preferences or delete generated workers while a browser is using them. No test should alter a regular profile or system packages.

Document driver/browser/SDK versions and meaningful runtime evidence with a change. Remove private URLs, usernames, cookies, tokens and profile data before attaching reports to an issue. Report security-sensitive problems privately using GitHub's security reporting feature where available.
