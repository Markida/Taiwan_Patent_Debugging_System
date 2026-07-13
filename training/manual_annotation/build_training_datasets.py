"""Build real-only locator and crop-classifier datasets from reviewed pages."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image

try:
    from .common import (
        Annotation,
        CLASS_NAMES,
        find_page_records,
        link_or_copy,
        read_page_record,
    )
except ImportError:
    from common import (
        Annotation,
        CLASS_NAMES,
        find_page_records,
        link_or_copy,
        read_page_record,
    )


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[1]
DEFAULT_ANNOTATIONS = SCRIPT_DIR / "annotation_set"
DEFAULT_OUTPUT = SCRIPT_DIR / "real_training_dataset"
DEFAULT_CROP_MANIFEST = (
    PROJECT_ROOT
    / "training"
    / "crop_classifier"
    / "review_dataset"
    / "manifest.csv"
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Build two-stage real patent-character training datasets."
    )
    parser.add_argument("--annotations", type=Path, default=DEFAULT_ANNOTATIONS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--crop-manifest", type=Path, default=DEFAULT_CROP_MANIFEST)
    parser.add_argument("--minimum-per-class", type=int, default=20)
    parser.add_argument("--minimum-val-per-class", type=int, default=3)
    parser.add_argument("--minimum-critical-class", type=int, default=50)
    parser.add_argument("--minimum-critical-val", type=int, default=5)
    parser.add_argument("--classifier-pad", type=int, default=6)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument(
        "--allow-unready",
        action="store_true",
        help="Build despite unfinished pages or insufficient classes (diagnostics only).",
    )
    return parser.parse_args()


def evaluate_readiness(
    class_counts,
    reviewed_pages,
    total_pages,
    minimum_per_class=20,
    minimum_val_per_class=3,
    minimum_critical_class=50,
    minimum_critical_val=5,
):
    overall = Counter(class_counts.get("train", {}))
    overall.update(class_counts.get("val", {}))
    insufficient_total = {
        name: overall[name]
        for name in CLASS_NAMES
        if overall[name] < minimum_per_class
    }
    insufficient_val = {
        name: int(class_counts.get("val", {}).get(name, 0))
        for name in CLASS_NAMES
        if int(class_counts.get("val", {}).get(name, 0)) < minimum_val_per_class
    }
    critical_classes = ("I", "V", "X", "prime")
    insufficient_critical_total = {
        name: overall[name]
        for name in critical_classes
        if overall[name] < minimum_critical_class
    }
    insufficient_critical_val = {
        name: int(class_counts.get("val", {}).get(name, 0))
        for name in critical_classes
        if int(class_counts.get("val", {}).get(name, 0)) < minimum_critical_val
    }
    return {
        "ready": (
            reviewed_pages == total_pages
            and not insufficient_total
            and not insufficient_val
            and not insufficient_critical_total
            and not insufficient_critical_val
        ),
        "reviewed_pages": reviewed_pages,
        "total_pages": total_pages,
        "unreviewed_pages": total_pages - reviewed_pages,
        "minimum_per_class": minimum_per_class,
        "minimum_val_per_class": minimum_val_per_class,
        "critical_classes": list(critical_classes),
        "minimum_critical_class": minimum_critical_class,
        "minimum_critical_val": minimum_critical_val,
        "insufficient_total": insufficient_total,
        "insufficient_val": insufficient_val,
        "insufficient_critical_total": insufficient_critical_total,
        "insufficient_critical_val": insufficient_critical_val,
    }


def load_annotation_state(annotation_root):
    records = []
    class_counts = {"train": Counter(), "val": Counter()}
    size_rows = []
    reviewed_pages = 0

    for record_path in find_page_records(annotation_root):
        record = read_page_record(record_path)
        if record.get("reviewed"):
            reviewed_pages += 1
        annotations = []
        for raw in record.get("annotations", []):
            annotation = Annotation(**raw).normalized(record["width"], record["height"])
            if not annotation.is_valid(record["width"], record["height"]):
                continue
            annotations.append(annotation)
            class_counts[record["split"]][annotation.label] += 1
            scale = 1536 / max(record["width"], record["height"])
            size_rows.append(
                (
                    (annotation.x2 - annotation.x1) * scale,
                    (annotation.y2 - annotation.y1) * scale,
                )
            )
        record["_record_path"] = str(record_path)
        record["_annotations"] = annotations
        records.append(record)

    return records, class_counts, size_rows, reviewed_pages


def summarize_sizes(size_rows):
    if not size_rows:
        return {}
    values = np.asarray(size_rows, dtype=float)
    return {
        "input_size": 1536,
        "box_width_percentiles_10_50_90": [
            round(float(value), 2)
            for value in np.percentile(values[:, 0], [10, 50, 90])
        ],
        "box_height_percentiles_10_50_90": [
            round(float(value), 2)
            for value in np.percentile(values[:, 1], [10, 50, 90])
        ],
    }


def write_report(output_root, annotation_root, class_counts, size_rows, readiness):
    report = {
        "source": str(annotation_root),
        "synthetic_pages_used": 0,
        "class_counts": {
            split: dict(sorted(counts.items()))
            for split, counts in class_counts.items()
        },
        "box_size_distribution": summarize_sizes(size_rows),
        "readiness": readiness,
        "architecture": {
            "stage_1": "one-class full-page locator including letters and prime",
            "stage_2": "0-9/A-Z/prime/background crop classifier",
        },
    }
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "build_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def crop_annotation(image, annotation, pad):
    x1 = max(0, int(round(annotation.x1)) - pad)
    y1 = max(0, int(round(annotation.y1)) - pad)
    x2 = min(image.width, int(round(annotation.x2)) + pad)
    y2 = min(image.height, int(round(annotation.y2)) + pad)
    return image.crop((x1, y1, x2, y2))


def rotate_annotation(annotation, image_width, image_height, rotation):
    """Rotate one source-coordinate box clockwise with its page."""

    rotation = int(rotation) % 360
    item = annotation.normalized(image_width, image_height)

    if rotation == 90:
        return Annotation(
            label=item.label,
            x1=image_height - item.y2,
            y1=item.x1,
            x2=image_height - item.y1,
            y2=item.x2,
            source=item.source,
        )
    if rotation == 180:
        return Annotation(
            label=item.label,
            x1=image_width - item.x2,
            y1=image_height - item.y2,
            x2=image_width - item.x1,
            y2=image_height - item.y1,
            source=item.source,
        )
    if rotation == 270:
        return Annotation(
            label=item.label,
            x1=item.y1,
            y1=image_width - item.x2,
            x2=item.y2,
            y2=image_width - item.x1,
            source=item.source,
        )
    return item


def rotate_page(image, annotations, rotation):
    rotation = int(rotation) % 360
    width, height = image.size
    rotated_annotations = [
        rotate_annotation(item, width, height, rotation)
        for item in annotations
    ]

    if rotation == 90:
        return image.transpose(Image.Transpose.ROTATE_270), rotated_annotations
    if rotation == 180:
        return image.transpose(Image.Transpose.ROTATE_180), rotated_annotations
    if rotation == 270:
        return image.transpose(Image.Transpose.ROTATE_90), rotated_annotations
    return image, rotated_annotations


def load_page_rotations(crop_manifest):
    crop_manifest = Path(crop_manifest)
    if not crop_manifest.exists():
        return {}

    votes = {}
    with crop_manifest.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            key = (row.get("split"), Path(row.get("source_image", "")).stem)
            rotation = int(row.get("rotation") or 0) % 360
            votes.setdefault(key, Counter())[rotation] += 1

    return {
        key: counts.most_common(1)[0][0]
        for key, counts in votes.items()
    }


def add_background_crops(crop_manifest, classifier_root):
    crop_manifest = Path(crop_manifest)
    if not crop_manifest.exists():
        return 0

    copied = 0
    dataset_root = crop_manifest.parent
    with crop_manifest.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("status") != "skipped":
                continue
            split = row.get("split") if row.get("split") in {"train", "val"} else "train"
            source = dataset_root / row["crop_path"]
            if not source.exists():
                continue
            destination = (
                classifier_root
                / split
                / "background"
                / f"background_{row['id']}.png"
            )
            link_or_copy(source, destination)
            copied += 1
    return copied


def build(records, annotation_root, output_root, classifier_pad, crop_manifest):
    locator_root = output_root / "locator"
    classifier_root = output_root / "classifier"
    page_rotations = load_page_rotations(crop_manifest)

    for record in records:
        split = record["split"]
        page_id = record["page_id"]
        source_image = annotation_root / record["image_path"]
        locator_image = locator_root / "images" / split / f"{page_id}.png"
        locator_label = locator_root / "labels" / split / f"{page_id}.txt"
        locator_label.parent.mkdir(parents=True, exist_ok=True)
        source_stem = Path(record["image_path"]).stem
        rotation = page_rotations.get(
            (split, source_stem),
            int(record.get("rotation") or 0) % 360,
        )
        with Image.open(source_image) as image:
            image = image.convert("RGB")
            image, annotations = rotate_page(
                image,
                record["_annotations"],
                rotation,
            )
            locator_image.parent.mkdir(parents=True, exist_ok=True)
            image.save(locator_image, format="PNG", optimize=True)
            yolo_lines = [
                annotation.to_yolo(image.width, image.height, class_id=0)
                for annotation in annotations
            ]
            yolo_lines = [line for line in yolo_lines if line]
            locator_label.write_text(
                "\n".join(yolo_lines) + ("\n" if yolo_lines else ""),
                encoding="utf-8",
            )

            for index, annotation in enumerate(annotations, start=1):
                crop = crop_annotation(image, annotation, classifier_pad)
                destination = (
                    classifier_root
                    / split
                    / annotation.label
                    / f"{page_id}_{index:04d}.png"
                )
                destination.parent.mkdir(parents=True, exist_ok=True)
                crop.save(destination, format="PNG", optimize=True)

    locator_yaml = "\n".join(
        [
            f'path: "{locator_root.resolve().as_posix()}"',
            "train: images/train",
            "val: images/val",
            "names:",
            "  0: patent_character",
            "",
        ]
    )
    (locator_root / "data.yaml").write_text(locator_yaml, encoding="utf-8")
    background_count = add_background_crops(crop_manifest, classifier_root)
    (classifier_root / "classes.json").write_text(
        json.dumps(
            {
                "classes": list(CLASS_NAMES) + ["background"],
                "prime_display": "'",
                "background_crops": background_count,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return background_count


def main():
    args = parse_args()
    annotation_root = args.annotations.resolve()
    output_root = args.output.resolve()
    records, class_counts, size_rows, reviewed_pages = load_annotation_state(
        annotation_root
    )
    readiness = evaluate_readiness(
        class_counts,
        reviewed_pages=reviewed_pages,
        total_pages=len(records),
        minimum_per_class=args.minimum_per_class,
        minimum_val_per_class=args.minimum_val_per_class,
        minimum_critical_class=args.minimum_critical_class,
        minimum_critical_val=args.minimum_critical_val,
    )
    report = write_report(
        output_root,
        annotation_root,
        class_counts,
        size_rows,
        readiness,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if args.check_only:
        return
    if not readiness["ready"] and not args.allow_unready:
        raise RuntimeError(
            "Training data is not ready. Finish every page and satisfy the per-class "
            f"minimums first. See: {output_root / 'build_report.json'}"
        )
    if output_root.exists() and any(
        path.name != "build_report.json" for path in output_root.iterdir()
    ):
        raise FileExistsError(
            f"Output already contains a dataset: {output_root}\n"
            "Choose a new --output path to avoid mixing dataset versions."
        )

    background_count = build(
        records,
        annotation_root=annotation_root,
        output_root=output_root,
        classifier_pad=args.classifier_pad,
        crop_manifest=args.crop_manifest.resolve(),
    )
    print(f"Built real-only two-stage dataset: {output_root}")
    print(f"Background crops: {background_count}")


if __name__ == "__main__":
    main()
