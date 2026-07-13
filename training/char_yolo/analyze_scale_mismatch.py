"""Compare real and legacy synthetic glyph sizes at the runtime input size."""

import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image

from char_classes import CLASS_NAMES


SCRIPT_DIR = Path(__file__).resolve().parent


def parse_args():
    parser = argparse.ArgumentParser(description="Analyze real/synthetic YOLO box sizes.")
    parser.add_argument("--dataset", type=Path, default=SCRIPT_DIR / "dataset")
    parser.add_argument("--imgsz", type=int, default=1536)
    parser.add_argument("--output", type=Path, default=SCRIPT_DIR / "scale_analysis.json")
    return parser.parse_args()


def collect(dataset_root, prefix, imgsz):
    sizes = []
    class_heights = defaultdict(list)

    for split in ("train", "val"):
        image_dir = dataset_root / "images" / split
        label_dir = dataset_root / "labels" / split
        for image_path in image_dir.glob(f"{prefix}*.png"):
            with Image.open(image_path) as image:
                width, height = image.size
            scale = imgsz / max(width, height)
            label_path = label_dir / f"{image_path.stem}.txt"
            for line in label_path.read_text(encoding="utf-8").splitlines():
                parts = line.split()
                class_name = CLASS_NAMES[int(parts[0])]
                box_width = float(parts[3]) * width * scale
                box_height = float(parts[4]) * height * scale
                sizes.append((box_width, box_height))
                class_heights[class_name].append(box_height)

    values = np.asarray(sizes, dtype=float)
    return {
        "instances": len(sizes),
        "width_percentiles_10_50_90": [
            round(float(value), 2)
            for value in np.percentile(values[:, 0], [10, 50, 90])
        ],
        "height_percentiles_10_50_90": [
            round(float(value), 2)
            for value in np.percentile(values[:, 1], [10, 50, 90])
        ],
        "selected_class_heights": {
            name: {
                "count": len(class_heights.get(name, [])),
                "percentiles_10_50_90": (
                    [
                        round(float(value), 2)
                        for value in np.percentile(class_heights[name], [10, 50, 90])
                    ]
                    if class_heights.get(name)
                    else []
                ),
            }
            for name in ("1", "I", "V", "X", "prime")
        },
    }


def main():
    args = parse_args()
    dataset_root = args.dataset.resolve()
    real = collect(dataset_root, "real_", args.imgsz)
    synthetic = collect(dataset_root, "synthetic_", args.imgsz)
    real_median = real["height_percentiles_10_50_90"][1]
    synthetic_median = synthetic["height_percentiles_10_50_90"][1]
    report = {
        "dataset": str(dataset_root),
        "runtime_imgsz": args.imgsz,
        "real": real,
        "legacy_synthetic": synthetic,
        "median_height_ratio_synthetic_to_real": round(
            synthetic_median / real_median,
            3,
        ),
        "decision": "Legacy synthetic pages are excluded from the next model.",
    }
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
