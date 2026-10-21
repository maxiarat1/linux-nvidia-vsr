# Examples

- **Extension dashboard:** open [`dashboard.html`](dashboard.html) locally. It embeds the real extension UI with sample metrics and simulated controls, without a GPU service or browser extension API. `dashboard.png` is a screenshot of that preview, not measured performance.
- **Video player:** `video/` is served at `http://127.0.0.1:8730/` by the backend. `scripts/make-demo.sh` generates its motion fixture inside ignored `artifacts/`. Use `./vsr browser` to try it with the extension.
- **Image API client:** with the engine running, enhance an image without placing your private token in command arguments:

```bash
bash -c 'source scripts/env.sh; .venv/bin/python examples/enhance_image.py input.png artifacts/output.png --quality HIGH --scale 2'
```

The client reads `.state/token` locally. See [the protocol documentation](../docs/api.md) to integrate a different application.
