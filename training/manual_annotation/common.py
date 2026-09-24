"""Shared data helpers for real patent-character page annotations."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

from features.patent_ocr.figure_heading_classes import (
    FIGURE_HEADING_CLASS_NAMES,
    FIGURE_PREFIX_ROTATE_RIGHT_CLASS,
)


BASE_CLASS_NAMES = tuple("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ") + ("prime",)
CLASS_NAMES = BASE_CLASS_NAMES
EXTENDED_CLASS_NAMES = BASE_CLASS_NAMES + tuple("abcdefghijklmnopqrstuvwxyz")
CLASS_TO_ID = {name: index for index, name in enumerate(CLASS_NAMES)}


def normalize_label(value):
    text = str(value or "").strip()

    if text in FIGURE_HEADING_CLASS_NAMES:
        return text
    if text in {"'", "′", "’", "`"} or text.upper() == "PRIME":
        return "prime"
    if len(text) == 1 and text in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz":
        return text
    return ""


def display_label(value):
    labels = {
        "prime": "'",
        "figure_prefix": "圖",
        "figure_identifier": "圖號",
        FIGURE_PREFIX_ROTATE_RIGHT_CLASS: "橫著的圖（右旋90°）",
    }
    return labels.get(value, str(value or ""))


def stable_split(source_key, val_ratio=0.2):
    digest = hashlib.sha1(str(source_key).encode("utf-8")).hexdigest()
    fraction = int(digest[:8], 16) / 0xFFFFFFFF
    return "val" if fraction < val_ratio else "train"


def link_or_copy(source, destination):
    source = Path(source)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    if destination.exists():
        return

    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


@dataclass
class Annotation:
    label: str
    x1: float
    y1: float
    x2: float
    y2: float
    source: str = "manual"
    text: str = ""
    seed_text: str = ""
    pair_id: str = ""

    def normalized(self, image_width, image_height):
        x1 = max(0.0, min(float(image_width), min(self.x1, self.x2)))
        y1 = max(0.0, min(float(image_height), min(self.y1, self.y2)))
        x2 = max(0.0, min(float(image_width), max(self.x1, self.x2)))
        y2 = max(0.0, min(float(image_height), max(self.y1, self.y2)))
        return Annotation(
            label=normalize_label(self.label),
            x1=x1,
            y1=y1,
            x2=x2,
            y2=y2,
            source=self.source,
            text=str(self.text or "").strip(),
            seed_text=str(self.seed_text or "").strip(),
            pair_id=str(self.pair_id or "").strip(),
        )

    def is_valid(self, image_width, image_height, min_size=2.0):
        item = self.normalized(image_width, image_height)
        return (
            bool(item.label)
            and item.x2 - item.x1 >= min_size
            and item.y2 - item.y1 >= min_size
        )

    def to_yolo(self, image_width, image_height, class_id=None):
        item = self.normalized(image_width, image_height)
        if not item.is_valid(image_width, image_height):
            return ""
        if class_id is None:
            class_id = CLASS_TO_ID[item.label]

        width = item.x2 - item.x1
        height = item.y2 - item.y1
        center_x = (item.x1 + item.x2) / 2.0
        center_y = (item.y1 + item.y2) / 2.0
        return (
            f"{int(class_id)} "
            f"{center_x / image_width:.6f} {center_y / image_height:.6f} "
            f"{width / image_width:.6f} {height / image_height:.6f}"
        )

    @classmethod
    def from_yolo(cls, line, image_width, image_height, label, source="seed"):
        parts = str(line).split()
        if len(parts) != 5:
            raise ValueError(f"Invalid YOLO annotation: {line}")

        center_x, center_y, width, height = map(float, parts[1:])
        box_width = width * image_width
        box_height = height * image_height
        pixel_x = center_x * image_width
        pixel_y = center_y * image_height
        return cls(
            label=normalize_label(label),
            x1=pixel_x - box_width / 2,
            y1=pixel_y - box_height / 2,
            x2=pixel_x + box_width / 2,
            y2=pixel_y + box_height / 2,
            source=source,
        ).normalized(image_width, image_height)


def make_page_record(
    page_id,
    split,
    image_path,
    source_path,
    image_width,
    image_height,
    annotations=None,
    reviewed=False,
):
    return {
        "version": 1,
        "page_id": str(page_id),
        "split": split,
        "image_path": Path(image_path).as_posix(),
        "source_path": str(source_path),
        "width": int(image_width),
        "height": int(image_height),
        "reviewed": bool(reviewed),
        "annotations": [
            asdict(item) if isinstance(item, Annotation) else dict(item)
            for item in (annotations or [])
        ],
    }


def read_page_record(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_page_record(path, record):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def find_page_records(annotation_root):
    annotation_root = Path(annotation_root)
    return sorted((annotation_root / "labels").glob("*/*.json"))
