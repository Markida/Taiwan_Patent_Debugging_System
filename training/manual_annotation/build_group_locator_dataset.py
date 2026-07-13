"""Build a real-only YOLO dataset whose boxes cover complete patent labels.

The annotation UI stores one box per character. Thin glyphs such as ``I``
and prime marks are difficult for a full-page detector to locate separately.
This builder joins adjacent character annotations on the same text row so the
detector can learn boxes such as ``VII``, ``10A`` and ``55'`` instead.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from statistics import median

from PIL import Image

try:
    from .build_training_datasets import (
        DEFAULT_ANNOTATIONS,
        DEFAULT_CROP_MANIFEST,
        load_annotation_state,
        load_page_rotations,
        rotate_page,
    )
    from .common import Annotation
except ImportError:
    from build_training_datasets import (
        DEFAULT_ANNOTATIONS,
        DEFAULT_CROP_MANIFEST,
        load_annotation_state,
        load_page_rotations,
        rotate_page,
    )
    from common import Annotation


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT = SCRIPT_DIR / "group_locator_dataset"


@dataclass(frozen=True)
class LabelGroup:
    text: str
    x1: float
    y1: float
    x2: float
    y2: float
    characters: tuple[Annotation, ...]

    def to_annotation(self):
        return Annotation(
            label="0",
            x1=self.x1,
            y1=self.y1,
            x2=self.x2,
            y2=self.y2,
            source="grouped_real",
        )


def display_character(label):
    return "'" if label == "prime" else label


def _height(item):
    return max(1.0, item.y2 - item.y1)


def _center_y(item):
    return (item.y1 + item.y2) / 2.0


def _vertical_overlap(first, second):
    overlap = max(0.0, min(first.y2, second.y2) - max(first.y1, second.y1))
    return overlap / min(_height(first), _height(second))


def _same_text_row(item, row):
    # Prime marks sit near the top of a digit but overlap it vertically.
    if any(_vertical_overlap(item, member) >= 0.25 for member in row):
        return True
    row_center = median(_center_y(member) for member in row)
    row_height = median(_height(member) for member in row)
    return abs(_center_y(item) - row_center) <= row_height * 0.45


def group_character_annotations(
    annotations,
    max_gap_height_ratio=0.35,
    max_label_length=8,
):
    """Join adjacent per-character boxes into complete label groups."""

    rows = []
    for item in sorted(annotations, key=lambda value: (_center_y(value), value.x1)):
        candidates = [row for row in rows if _same_text_row(item, row)]
        if candidates:
            row = min(
                candidates,
                key=lambda values: abs(
                    _center_y(item) - median(_center_y(value) for value in values)
                ),
            )
            row.append(item)
        else:
            rows.append([item])

    groups = []
    for row in rows:
        row = sorted(row, key=lambda value: (value.x1, value.x2))
        current = []
        for item in row:
            if not current:
                current = [item]
                continue

            previous_right = max(value.x2 for value in current)
            typical_height = median(_height(value) for value in current + [item])
            allowed_gap = typical_height * float(max_gap_height_ratio)
            if item.x1 - previous_right <= allowed_gap:
                current.append(item)
            else:
                groups.extend(_finish_group(current, max_label_length))
                current = [item]

        groups.extend(_finish_group(current, max_label_length))

    return sorted(groups, key=lambda value: (value.y1, value.x1))


def _finish_group(characters, max_label_length):
    if not characters:
        return []
    characters = tuple(sorted(characters, key=lambda value: (value.x1, value.x2)))
    text = "".join(display_character(value.label) for value in characters)
    # The requested output vocabulary contains one prime suffix. Some drawings
    # render it with two visible strokes, both of which were correctly boxed.
    text = re.sub(r"'+", "'", text)
    if not text or text == "'" or len(text) > int(max_label_length):
        return []
    return [
        LabelGroup(
            text=text,
            x1=min(value.x1 for value in characters),
            y1=min(value.y1 for value in characters),
            x2=max(value.x2 for value in characters),
            y2=max(value.y2 for value in characters),
            characters=characters,
        )
    ]


def parse_args():
    parser = argparse.ArgumentParser(description="Build complete-label YOLO dataset.")
    parser.add_argument("--annotations", type=Path, default=DEFAULT_ANNOTATIONS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--crop-manifest", type=Path, default=DEFAULT_CROP_MANIFEST)
    parser.add_argument("--max-gap-height-ratio", type=float, default=0.35)
    parser.add_argument("--max-label-length", type=int, default=8)
    return parser.parse_args()


def build_group_dataset(
    annotation_root,
    output_root,
    crop_manifest=DEFAULT_CROP_MANIFEST,
    max_gap_height_ratio=0.35,
    max_label_length=8,
):
    annotation_root = Path(annotation_root).resolve()
    output_root = Path(output_root).resolve()
    if output_root.exists() and any(output_root.iterdir()):
        raise FileExistsError(f"Output already contains files: {output_root}")

    records, _class_counts, _size_rows, reviewed_pages = load_annotation_state(
        annotation_root
    )
    if reviewed_pages != len(records):
        raise RuntimeError(
            f"Only {reviewed_pages}/{len(records)} pages are reviewed; finish review first."
        )

    page_rotations = load_page_rotations(Path(crop_manifest).resolve())
    group_counts = {"train": Counter(), "val": Counter()}
    character_boxes = Counter()
    group_boxes = Counter()

    for record in records:
        split = record["split"]
        page_id = record["page_id"]
        source_image = annotation_root / record["image_path"]
        source_stem = Path(record["image_path"]).stem
        rotation = page_rotations.get(
            (split, source_stem),
            int(record.get("rotation") or 0) % 360,
        )

        with Image.open(source_image) as image:
            image = image.convert("RGB")
            image, annotations = rotate_page(image, record["_annotations"], rotation)
            groups = group_character_annotations(
                annotations,
                max_gap_height_ratio=max_gap_height_ratio,
                max_label_length=max_label_length,
            )

            image_path = output_root / "images" / split / f"{page_id}.png"
            label_path = output_root / "labels" / split / f"{page_id}.txt"
            image_path.parent.mkdir(parents=True, exist_ok=True)
            label_path.parent.mkdir(parents=True, exist_ok=True)
            image.save(image_path, format="PNG", optimize=True)

            yolo_lines = [
                group.to_annotation().to_yolo(image.width, image.height, class_id=0)
                for group in groups
            ]
            label_path.write_text(
                "\n".join(line for line in yolo_lines if line)
                + ("\n" if yolo_lines else ""),
                encoding="utf-8",
            )

        character_boxes[split] += len(record["_annotations"])
        group_boxes[split] += len(groups)
        group_counts[split].update(group.text for group in groups)

    data_yaml = "\n".join(
        [
            f'path: "{output_root.as_posix()}"',
            "train: images/train",
            "val: images/val",
            "names:",
            "  0: patent_label",
            "",
        ]
    )
    (output_root / "data.yaml").write_text(data_yaml, encoding="utf-8")
    report = {
        "source": str(annotation_root),
        "reviewed_pages": reviewed_pages,
        "total_pages": len(records),
        "rotation_normalized": True,
        "synthetic_pages_used": 0,
        "max_gap_height_ratio": max_gap_height_ratio,
        "max_label_length": max_label_length,
        "character_boxes": dict(character_boxes),
        "group_boxes": dict(group_boxes),
        "multi_character_groups": {
            split: sum(count for text, count in counts.items() if len(text) > 1)
            for split, counts in group_counts.items()
        },
        "prime_groups": {
            split: sum(count for text, count in counts.items() if "'" in text)
            for split, counts in group_counts.items()
        },
        "roman_groups": {
            split: {
                text: count
                for text, count in sorted(counts.items())
                if len(text) > 1 and set(text) <= set("IVX")
            }
            for split, counts in group_counts.items()
        },
    }
    (output_root / "build_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def main():
    args = parse_args()
    report = build_group_dataset(
        annotation_root=args.annotations,
        output_root=args.output,
        crop_manifest=args.crop_manifest,
        max_gap_height_ratio=args.max_gap_height_ratio,
        max_label_length=args.max_label_length,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
