"""Select only high-value ambiguous crops from the company-approved corpus."""

from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter, defaultdict
from pathlib import Path

from training.manual_annotation.common import Annotation, make_page_record, write_page_record
from training.v3.common import load_config, read_image, read_jsonl, write_png


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = SCRIPT_DIR / "config.yaml"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Select v4 company-approved manual character-box reviews."
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--audit", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def prediction_payload(record: dict) -> dict | None:
    return record.get("prediction") or record.get("v1") or record.get("v2")


def classify(row: dict) -> tuple[str, float]:
    accepted = row.get("accepted", [])
    rejected = row.get("rejected", [])
    predicted = [*accepted]
    predicted.extend(
        payload
        for payload in (prediction_payload(item) for item in rejected)
        if payload
    )
    labels = {str(item.get("label")) for item in predicted if item.get("label")}
    reasons = {str(item.get("reason")) for item in rejected}

    if "1" in labels:
        category, bonus = "digit_one_recall", 5.0
    elif labels & {"0", "J"} or any("high_risk" in reason for reason in reasons):
        category, bonus = "zero_or_j_hard_case", 4.0
    elif labels & {"prime", "I", "V", "X"}:
        category, bonus = "roman_or_prime", 3.0
    elif any(label in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" for label in labels):
        category, bonus = "letters", 2.0
    else:
        category, bonus = "other_missed_character", 1.0

    confidence = max(
        [float(item.get("confidence", 0.0)) for item in predicted] or [0.0]
    )
    score = bonus + confidence + float(row.get("group_confidence", 0.0))
    if category == "zero_or_j_hard_case" and not accepted:
        score += 1.0
    return category, score


def select_rows(rows: list[dict], limit: int) -> list[dict]:
    categories: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if not row.get("review_required"):
            continue
        category, score = classify(row)
        candidate = dict(row)
        candidate["manual_review_category"] = category
        candidate["manual_review_score"] = score
        categories[category].append(candidate)
    for candidates in categories.values():
        candidates.sort(key=lambda row: row["manual_review_score"], reverse=True)

    quotas = {
        "digit_one_recall": round(limit * 0.30),
        "zero_or_j_hard_case": round(limit * 0.20),
        "roman_or_prime": round(limit * 0.20),
        "letters": round(limit * 0.20),
        "other_missed_character": limit - round(limit * 0.30) - round(limit * 0.20) * 3,
    }
    selected: list[dict] = []
    used_stems: set[str] = set()
    per_page_category: Counter = Counter()
    for category, quota in quotas.items():
        for row in categories[category]:
            crop_key = row["crop_stem"]
            page_key = (row["page"], category)
            if crop_key in used_stems or per_page_category[page_key] >= 3:
                continue
            selected.append(row)
            used_stems.add(crop_key)
            per_page_category[page_key] += 1
            if sum(item["manual_review_category"] == category for item in selected) >= quota:
                break

    if len(selected) < limit:
        remainder = sorted(
            (
                row
                for candidates in categories.values()
                for row in candidates
                if row["crop_stem"] not in used_stems
            ),
            key=lambda row: row["manual_review_score"],
            reverse=True,
        )
        for row in remainder:
            if len(selected) >= limit:
                break
            if sum(item["page"] == row["page"] for item in selected) >= 8:
                continue
            selected.append(row)
            used_stems.add(row["crop_stem"])
    return selected


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    audit = (args.audit or config["paths"]["work"] / "pseudo_audit.jsonl").resolve()
    output = (args.output or SCRIPT_DIR / "manual_review").resolve()
    limit = args.limit or int(config["dataset"]["manual_review_target"])
    if not audit.exists():
        raise FileNotFoundError(f"Pseudo-label audit is missing: {audit}")
    report_path = output / "selection_report.json"
    if output.exists() and any(output.iterdir()):
        if args.overwrite:
            shutil.rmtree(output)
        elif report_path.exists():
            print(report_path.read_text(encoding="utf-8"))
            return 0
        else:
            raise FileExistsError(f"Manual review output is not empty: {output}")
    (output / "images" / "train").mkdir(parents=True, exist_ok=True)
    (output / "labels" / "train").mkdir(parents=True, exist_ok=True)

    rows = list(read_jsonl(audit))
    selected = select_rows(rows, limit)
    page_cache: dict[str, object] = {}
    category_counts: Counter = Counter()
    seeded_counts: Counter = Counter()
    for index, row in enumerate(selected, start=1):
        page_path = row["page_path"]
        image = page_cache.get(page_path)
        if image is None:
            image = read_image(Path(page_path))
            if image is None:
                raise RuntimeError(f"Cannot read source page: {page_path}")
            page_cache = {page_path: image}
        x1, y1, x2, y2 = (int(round(value)) for value in row["crop_box"])
        crop = image[y1:y2, x1:x2]
        page_id = f"v4_review_{index:04d}_{row['crop_stem']}"
        relative_image = Path("images") / "train" / f"{page_id}.png"
        write_png(output / relative_image, crop)
        annotations = [
            Annotation(
                label=item["label"],
                x1=float(item["x1"]),
                y1=float(item["y1"]),
                x2=float(item["x2"]),
                y2=float(item["y2"]),
                source="pseudo_seed",
            )
            for item in row.get("accepted", [])
        ]
        record = make_page_record(
            page_id=page_id,
            split="train",
            image_path=relative_image,
            source_path=page_path,
            image_width=crop.shape[1],
            image_height=crop.shape[0],
            annotations=annotations,
            reviewed=False,
        )
        record["v4_review"] = {
            "category": row["manual_review_category"],
            "score": row["manual_review_score"],
            "source_crop_stem": row["crop_stem"],
            "source_crop_box": row["crop_box"],
            "rejection_reasons": [item["reason"] for item in row.get("rejected", [])],
        }
        write_page_record(output / "labels" / "train" / f"{page_id}.json", record)
        category_counts[row["manual_review_category"]] += 1
        seeded_counts.update(item.label for item in annotations)

    report = {
        "format_version": 1,
        "selected": len(selected),
        "requested_maximum": limit,
        "available_review_candidates": sum(row.get("review_required", False) for row in rows),
        "reviewed": 0,
        "source_audit": str(audit),
        "category_counts": dict(category_counts),
        "seeded_class_counts": dict(seeded_counts),
        "policy": (
            "Only real model disagreements/low-confidence groups are selected. "
            "Digit 1 recall is prioritized; no synthetic quota fillers are created."
        ),
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
