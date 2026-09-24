"""Prepare high-contrast footer crops for Windows Traditional Chinese OCR."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageOps


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True)
    args = parser.parse_args()

    corpus = Path(args.corpus).resolve()
    manifest = corpus / "manifest_raw.jsonl"
    staging = corpus / "cache" / "staging"
    crop_dir = corpus / "cache" / "footer_crops"
    crop_dir.mkdir(parents=True, exist_ok=True)
    rows = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines() if line.strip()]
    paths: list[str] = []
    for index, row in enumerate(rows, start=1):
        source = staging / row["filename"]
        target = crop_dir / row["filename"]
        if not target.exists():
            with Image.open(source) as image:
                gray = image.convert("L")
                width, height = gray.size
                crop = gray.crop(
                    (
                        int(width * 0.15),
                        int(height * 0.88),
                        int(width * 0.85),
                        height,
                    )
                )
                crop = ImageOps.autocontrast(crop)
                crop = crop.resize((int(crop.width * 1.4), int(crop.height * 1.4)))
                crop = crop.point(lambda pixel: 0 if pixel < 245 else 255, mode="1").convert("L")
                crop.save(target, format="PNG", compress_level=6)
        paths.append(str(target))
        if index % 250 == 0 or index == len(rows):
            print(f"footer crops {index}/{len(rows)}", flush=True)

    # Windows PowerShell 5 reliably recognizes UTF-8 input when a BOM is present.
    (corpus / "footer_crop_paths.txt").write_text("\n".join(paths) + "\n", encoding="utf-8-sig")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
