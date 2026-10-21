"""Small external-app client for the reusable frame API."""

import argparse
import json
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(
        description="Enhance one image with the local NVIDIA VSR service"
    )
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--scale", type=float, default=2)
    parser.add_argument(
        "--quality",
        choices=[
            "LOW",
            "MEDIUM",
            "HIGH",
            "ULTRA",
            "HIGHBITRATE_HIGH",
            "HIGHBITRATE_ULTRA",
        ],
        default="HIGH",
    )
    args = parser.parse_args()
    token = (ROOT / ".state/token").read_text().strip()
    response = httpx.post(
        "http://127.0.0.1:8730/v1/frame",
        params={
            "scale": args.scale,
            "quality": args.quality,
            "format": "png" if args.output.suffix.lower() == ".png" else "jpeg",
        },
        content=args.input.read_bytes(),
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )
    response.raise_for_status()
    args.output.write_bytes(response.content)
    print(json.dumps(json.loads(response.headers["X-VSR-Stats"]), indent=2))


if __name__ == "__main__":
    main()
