from __future__ import annotations

import argparse
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from training.manual_annotation.common import Annotation, find_page_records, read_page_record


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_DATASET = SCRIPT_DIR / "manual_review"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate and preview v4 manual reviews.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--seed", type=int, default=20260727)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.dataset.resolve()
    record_paths = find_page_records(root)
    errors: list[str] = []
    records: list[tuple[Path, dict]] = []
    categories: Counter = Counter()
    seed_classes: Counter = Counter()
    reviewed = 0
    for record_path in record_paths:
        record = read_page_record(record_path)
        image_path = root / record["image_path"]
        if not image_path.exists():
            errors.append(f"missing image: {image_path}")
            continue
        with Image.open(image_path) as image:
            image.load()
            width, height = image.size
        if (width, height) != (int(record["width"]), int(record["height"])):
            errors.append(f"size mismatch: {record_path.name}")
        for raw in record.get("annotations", []):
            item = Annotation(**raw)
            if not item.is_valid(width, height):
                errors.append(f"invalid seed box: {record_path.name}")
            seed_classes[item.label] += 1
        category = record.get("v4_review", {}).get("category", "unknown")
        categories[category] += 1
        reviewed += int(bool(record.get("reviewed")))
        records.append((image_path, record))

    by_category: dict[str, list[tuple[Path, dict]]] = defaultdict(list)
    for item in records:
        by_category[item[1].get("v4_review", {}).get("category", "unknown")].append(item)
    selected: list[tuple[Path, dict]] = []
    rng = random.Random(args.seed)
    for category in sorted(by_category):
        candidates = by_category[category]
        selected.extend(rng.sample(candidates, min(4, len(candidates))))

    columns, cell_w, cell_h = 4, 420, 260
    rows_count = max(1, (len(selected) + columns - 1) // columns)
    sheet = Image.new("RGB", (columns * cell_w, rows_count * cell_h), "white")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    for index, (image_path, record) in enumerate(selected):
        with Image.open(image_path) as source:
            preview = source.convert("RGB")
        box_draw = ImageDraw.Draw(preview)
        for raw in record.get("annotations", []):
            item = Annotation(**raw)
            box_draw.rectangle((item.x1, item.y1, item.x2, item.y2), outline=(0, 180, 0), width=3)
            box_draw.text((item.x1, max(0, item.y1 - 14)), item.label, fill=(0, 120, 0), font=font)
        thumb = ImageOps.contain(preview, (cell_w - 16, cell_h - 42))
        x = (index % columns) * cell_w + (cell_w - thumb.width) // 2
        y = (index // columns) * cell_h + 28
        sheet.paste(thumb, (x, y))
        category = record.get("v4_review", {}).get("category", "unknown")
        draw.text(
            ((index % columns) * cell_w + 6, (index // columns) * cell_h + 6),
            f"{record['page_id']} | {category}",
            fill="black",
            font=font,
        )
    contact_sheet = SCRIPT_DIR / "manual_review_contact_sheet.png"
    sheet.save(contact_sheet, optimize=True)

    report = {
        "dataset": str(root),
        "records": len(record_paths),
        "valid_records": len(records),
        "reviewed": reviewed,
        "remaining": len(records) - reviewed,
        "category_counts": dict(categories),
        "seeded_class_counts": dict(seed_classes),
        "errors": errors,
        "contact_sheet": str(contact_sheet),
        "ready_for_manual_annotation": len(records) > 0 and not errors,
    }
    report_path = SCRIPT_DIR / "manual_review_qa.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
