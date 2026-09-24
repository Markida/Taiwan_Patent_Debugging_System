"""Import reviewed v3 group crops into the final YOLO train split."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

try:
    from .common import CLASS_TO_ID, DEFAULT_CONFIG, link_or_copy, load_config, read_image
except ImportError:
    from common import CLASS_TO_ID, DEFAULT_CONFIG, link_or_copy, load_config, read_image

from training.manual_annotation.common import Annotation, find_page_records, read_page_record


def parse_args():
    parser = argparse.ArgumentParser(description="Import completed v3 manual reviews.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--review-root", type=Path, default=None)
    parser.add_argument("--dataset", type=Path, default=None)
    return parser.parse_args()


def main():
    args = parse_args()
    config = load_config(args.config)
    output = (args.dataset or config["paths"]["output_dataset"]).resolve()
    review_root = (args.review_root or Path(__file__).resolve().parent / "manual_review").resolve()
    build_report_path = output / "build_report.json"
    if not build_report_path.exists():
        raise FileNotFoundError("Build the complete v3 dataset before importing reviews.")
    selection_path = review_root / "selection_report.json"
    if not selection_path.exists():
        raise FileNotFoundError(f"Manual review selection is missing: {selection_path}")

    selected = json.loads(selection_path.read_text(encoding="utf-8"))
    reviewed = imported = empty = 0
    class_counts = Counter()
    for record_path in find_page_records(review_root):
        record = read_page_record(record_path)
        if not record.get("reviewed"):
            continue
        reviewed += 1
        image_path = review_root / record["image_path"]
        image = read_image(image_path)
        if image is None:
            raise RuntimeError(f"Cannot read reviewed crop: {image_path}")
        height, width = image.shape[:2]
        annotations = []
        for raw in record.get("annotations", []):
            item = Annotation(**raw).normalized(width, height)
            if item.is_valid(width, height):
                annotations.append(item)
        stem = f"manual_{record['page_id']}"
        target_image = output / "images" / "train" / f"{stem}.png"
        target_label = output / "labels" / "train" / f"{stem}.txt"
        link_or_copy(image_path, target_image)
        lines = [item.to_yolo(width, height, class_id=CLASS_TO_ID[item.label]) for item in annotations]
        target_label.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        imported += 1
        empty += int(not annotations)
        class_counts.update(item.label for item in annotations)

    report = {
        "selected": int(selected["selected"]),
        "reviewed": reviewed,
        "remaining": max(0, int(selected["selected"]) - reviewed),
        "imported": imported,
        "empty_hard_negatives": empty,
        "class_counts": dict(class_counts),
    }
    selected["reviewed"] = reviewed
    selected["remaining"] = report["remaining"]
    selection_path.write_text(
        json.dumps(selected, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    import_report = output / "manual_review_import.json"
    import_report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    build = json.loads(build_report_path.read_text(encoding="utf-8"))
    build["manual_review"] = report
    build["image_counts"] = {
        split: len([path for path in (output / "images" / split).glob("*") if path.is_file()])
        for split in ("train", "val")
    }
    build_report_path.write_text(json.dumps(build, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if reviewed == 0:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
