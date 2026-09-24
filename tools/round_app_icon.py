"""Apply an anti-aliased rounded-corner alpha mask to the application icon."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw


def round_icon(source: Path, png_output: Path, ico_output: Path, radius_ratio: float) -> None:
    image = Image.open(source).convert("RGBA")
    width, height = image.size
    supersampling = 4
    mask_size = (width * supersampling, height * supersampling)
    radius = round(min(width, height) * radius_ratio * supersampling)

    mask = Image.new("L", mask_size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, mask_size[0] - 1, mask_size[1] - 1),
        radius=radius,
        fill=255,
    )
    mask = mask.resize((width, height), Image.Resampling.LANCZOS)
    image.putalpha(ImageChops.multiply(image.getchannel("A"), mask))

    png_output.parent.mkdir(parents=True, exist_ok=True)
    ico_output.parent.mkdir(parents=True, exist_ok=True)
    image.save(png_output, format="PNG", optimize=True)
    image.save(
        ico_output,
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("png_output", type=Path)
    parser.add_argument("ico_output", type=Path)
    parser.add_argument("--radius-ratio", type=float, default=0.24)
    args = parser.parse_args()
    if not 0 < args.radius_ratio <= 0.5:
        parser.error("--radius-ratio must be greater than 0 and no more than 0.5")
    round_icon(args.source, args.png_output, args.ico_output, args.radius_ratio)


if __name__ == "__main__":
    main()
