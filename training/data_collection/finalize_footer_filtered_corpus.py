"""Finalize a TIPO corpus using Windows OCR-confirmed drawing footer labels."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from build_tipo_drawing_corpus import (
    build_contact_sheet,
    choose_grouped_split,
    clear_files,
    link_or_copy,
    utc_now,
)


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def choose_exact_train_and_all_remaining_validation(
    rows: list[dict], train_count: int, seed: int
) -> tuple[list[dict], list[dict]]:
    """Choose whole patent groups totalling train_count; put every other row in val."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        groups[row["publication_number"]].append(row)
    patent_ids = sorted(
        groups,
        key=lambda value: hashlib.sha256(f"{seed}:{value}".encode()).hexdigest(),
    )

    # Subset-sum dynamic programming.  Only totals up to train_count are kept,
    # so memory remains bounded even when the corpus contains thousands of rows.
    reachable: dict[int, tuple[int, str] | None] = {0: None}
    for patent_id in patent_ids:
        size = len(groups[patent_id])
        for total in sorted(list(reachable), reverse=True):
            new_total = total + size
            if new_total <= train_count and new_total not in reachable:
                reachable[new_total] = (total, patent_id)
        if train_count in reachable:
            break
    if train_count not in reachable:
        raise RuntimeError(
            f"Cannot form exactly {train_count} training images without splitting a patent group"
        )

    train_patents: set[str] = set()
    total = train_count
    while total:
        previous, patent_id = reachable[total]  # type: ignore[misc]
        train_patents.add(patent_id)
        total = previous

    train: list[dict] = []
    val: list[dict] = []
    for patent_id in patent_ids:
        destination = train if patent_id in train_patents else val
        destination.extend(sorted(groups[patent_id], key=lambda row: row["source_tiff_frame"]))
    if len(train) != train_count or len(train) + len(val) != len(rows):
        raise RuntimeError("Exact grouped split integrity check failed")
    return train, val


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--train-count", type=int, default=5000)
    parser.add_argument("--val-count", type=int, default=1000)
    parser.add_argument(
        "--all-remaining-to-val",
        action="store_true",
        help="Use exactly --train-count whole-patent images for train and every remaining image for validation",
    )
    parser.add_argument(
        "--all-remaining-to-train",
        action="store_true",
        help="Use exactly --val-count whole-patent images for validation and every remaining image for train",
    )
    parser.add_argument("--seed", type=int, default=20260722)
    args = parser.parse_args()
    if args.all_remaining_to_val and args.all_remaining_to_train:
        parser.error("Choose only one all-remaining split mode")

    corpus = Path(args.corpus).resolve()
    staging = corpus / "cache" / "staging"
    raw_rows = read_jsonl(corpus / "manifest_raw.jsonl")
    ocr_rows = read_jsonl(corpus / "footer_ocr.jsonl")
    ocr_by_name = {Path(row["path"]).name: row for row in ocr_rows}
    confirmed: list[dict] = []
    unknown = 0
    for row in raw_rows:
        ocr = ocr_by_name.get(row["filename"])
        if ocr is None:
            unknown += 1
        elif ocr.get("is_drawing") is True and (staging / row["filename"]).exists():
            confirmed.append({**row, "footer_ocr_text": ocr.get("text", "")})

    unique_by_pixels: dict[str, dict] = {}
    for row in confirmed:
        unique_by_pixels.setdefault(row["pixel_sha256"], row)
    unique_rows = list(unique_by_pixels.values())
    filter_stats = {
        "updated_at_utc": utc_now(),
        "raw_manifest_rows": len(raw_rows),
        "footer_ocr_rows": len(ocr_rows),
        "footer_ocr_unknown": unknown,
        "confirmed_drawing_rows": len(confirmed),
        "confirmed_unique_drawing_rows": len(unique_rows),
        "required_images": (
            args.train_count
            if args.all_remaining_to_val
            else args.val_count
            if args.all_remaining_to_train
            else args.train_count + args.val_count
        ),
        "enough_to_finalize": len(unique_rows) >= (
            args.train_count
            if args.all_remaining_to_val
            else args.val_count
            if args.all_remaining_to_train
            else args.train_count + args.val_count
        ),
    }
    (corpus / "footer_filter_stats.json").write_text(
        json.dumps(filter_stats, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(filter_stats, ensure_ascii=False, sort_keys=True), flush=True)
    if not filter_stats["enough_to_finalize"]:
        return 2

    if args.all_remaining_to_val:
        train_rows, val_rows = choose_exact_train_and_all_remaining_validation(
            unique_rows, args.train_count, args.seed
        )
    elif args.all_remaining_to_train:
        val_rows, train_rows = choose_exact_train_and_all_remaining_validation(
            unique_rows, args.val_count, args.seed
        )
    else:
        train_rows, val_rows = choose_grouped_split(unique_rows, args.train_count, args.val_count, args.seed)
    train_dir = corpus / "images" / "train"
    val_dir = corpus / "images" / "val"
    clear_files(train_dir)
    clear_files(val_dir)
    final_rows: list[dict] = []
    for split, rows, destination in (("train", train_rows, train_dir), ("val", val_rows, val_dir)):
        for row in rows:
            source = staging / row["filename"]
            target = destination / row["filename"]
            link_or_copy(source, target)
            portable = {key: value for key, value in row.items() if key != "staging_path"}
            final_rows.append({**portable, "split": split, "path": str(target.relative_to(corpus))})

    (corpus / "manifest.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in final_rows),
        encoding="utf-8",
    )
    with (corpus / "manifest.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(final_rows[0]))
        writer.writeheader()
        writer.writerows(final_rows)
    (corpus / "integrity_sha256.txt").write_text(
        "".join(f"{row['file_sha256']}  {row['path'].replace(os.sep, '/')}\n" for row in final_rows),
        encoding="utf-8",
    )
    build_contact_sheet(train_rows, staging, corpus / "qa" / "train_contact_sheet.png", args.seed + 1)
    build_contact_sheet(val_rows, staging, corpus / "qa" / "val_contact_sheet.png", args.seed + 2)

    train_patents = {row["publication_number"] for row in train_rows}
    val_patents = {row["publication_number"] for row in val_rows}
    stats = {
        "created_at_utc": utc_now(),
        "source": "Taiwan Intellectual Property Office (TIPO) Patent Open Data",
        "source_page": "https://cloud.tipo.gov.tw/S220/opdata/detail/PatentPub",
        "license_page": "https://data.gov.tw/license",
        "training_started": False,
        "labels_created": False,
        "page_confirmation": "Windows zh-Hant-TW OCR confirmed official drawing footer",
        "accepted_footer_labels": ["發明圖式", "新型圖式", "設計圖說"],
        "orientation_policy": "official original orientation preserved",
        "split_policy": (
            f"exact {args.train_count} train images by whole publication_number groups; all remaining images in validation"
            if args.all_remaining_to_val
            else f"exact {args.val_count} validation images by whole publication_number groups; all remaining images in train"
            if args.all_remaining_to_train
            else "grouped by publication_number; no patent appears in both splits"
        ),
        "train_images": len(train_rows),
        "validation_images": len(val_rows),
        "total_images": len(final_rows),
        "train_patents": len(train_patents),
        "validation_patents": len(val_patents),
        "patent_overlap": len(train_patents & val_patents),
        "confirmed_unique_drawing_rows": len(unique_rows),
        "extensions": dict(Counter(Path(row["filename"]).suffix.lower() for row in final_rows)),
        "ink_fraction": {
            "min": min(row["ink_fraction"] for row in final_rows),
            "max": max(row["ink_fraction"] for row in final_rows),
            "mean": round(float(np.mean([row["ink_fraction"] for row in final_rows])), 6),
        },
    }
    (corpus / "stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(stats, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
