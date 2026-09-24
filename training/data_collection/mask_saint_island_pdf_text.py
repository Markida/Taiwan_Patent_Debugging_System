from __future__ import annotations

import argparse
import json
import os
import shutil
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pdfplumber
from PIL import Image, ImageDraw

try:
    from .preprocess_saint_island_latest300 import (
        exact_group_split,
        file_sha256,
        link_or_copy,
        make_contact_sheet,
        pixel_sha256,
        write_manifest,
    )
except ImportError:
    from preprocess_saint_island_latest300 import (
        exact_group_split,
        file_sha256,
        link_or_copy,
        make_contact_sheet,
        pixel_sha256,
        write_manifest,
    )


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CORPUS = (
    SCRIPT_DIR / "saint_island_latest_300_20260727" / "preprocessed_corpus"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Mask gazette text overlays while preserving raster patent drawings."
    )
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--validation-pages", type=int, default=175)
    parser.add_argument("--padding", type=int, default=4)
    parser.add_argument("--seed", type=int, default=20260727)
    return parser.parse_args()


def read_manifest(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def safe_remove_generated(path: Path, corpus: Path) -> None:
    if not path.exists():
        return
    resolved = path.resolve()
    if corpus.resolve() not in resolved.parents:
        raise RuntimeError(f"Refusing to remove generated path outside corpus: {resolved}")
    shutil.rmtree(resolved)


def mask_page_text(
    row: dict[str, Any],
    page: pdfplumber.page.Page,
    padding: int,
) -> tuple[int, int]:
    rendered = Path(row["rendered_path"])
    lines = page.extract_text_lines(return_chars=True) or []
    with Image.open(rendered) as source:
        source.load()
        image = source.copy()
    scale_x = image.width / float(page.width)
    scale_y = image.height / float(page.height)
    draw = ImageDraw.Draw(image)
    masked_characters = 0
    for line in lines:
        text = str(line.get("text") or "")
        x0 = max(0, round(float(line["x0"]) * scale_x) - padding)
        y0 = max(0, round(float(line["top"]) * scale_y) - padding)
        x1 = min(image.width, round(float(line["x1"]) * scale_x) + padding)
        y1 = min(image.height, round(float(line["bottom"]) * scale_y) + padding)
        if x1 <= x0 or y1 <= y0:
            continue
        draw.rectangle((x0, y0, x1, y1), fill=1 if image.mode == "1" else 255)
        masked_characters += len(text.replace(" ", ""))
    # Save directly to preserve any hard links created for split folders.
    image.save(rendered, format="PNG", optimize=True)
    row["pdf_text_regions_masked"] = len(lines)
    row["pdf_text_characters_masked"] = masked_characters
    row["pdf_text_mask_method"] = "pdfplumber line coordinates; raster drawing layer preserved"
    row["preprocess_status"] = "clean_pdf_text_masked"
    row["file_sha256"] = file_sha256(rendered)
    with Image.open(rendered) as verified:
        verified.load()
        row["pixel_sha256"] = pixel_sha256(verified)
        row["ink_fraction"] = round(verified.histogram()[0] / max(1, verified.width * verified.height), 6)
    return len(lines), masked_characters


def main() -> int:
    args = parse_args()
    corpus = args.corpus.resolve()
    manifest = corpus / "manifest.jsonl"
    stats_path = corpus / "stats.json"
    if not manifest.exists() or not stats_path.exists():
        raise FileNotFoundError("Run preprocess_saint_island_latest300.py first")
    rows = read_manifest(manifest)
    if len(rows) != 1355:
        raise RuntimeError(f"Expected 1355 manifest rows, found {len(rows)}")

    by_pdf: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_pdf[row["source_pdf"]].append(row)

    regions = characters = processed = 0
    for pdf_number, (pdf_path, pdf_rows) in enumerate(sorted(by_pdf.items()), start=1):
        with pdfplumber.open(pdf_path) as document:
            for row in pdf_rows:
                page_index = int(row["source_pdf_page"]) - 1
                if page_index < 0 or page_index >= len(document.pages):
                    raise IndexError(
                        f"{pdf_path}: invalid page {row['source_pdf_page']}"
                    )
                line_count, character_count = mask_page_text(
                    row, document.pages[page_index], args.padding
                )
                regions += line_count
                characters += character_count
                processed += 1
        if pdf_number % 25 == 0 or pdf_number == len(by_pdf):
            print(f"masked PDFs {pdf_number}/{len(by_pdf)}", flush=True)

    blank_rows = [row for row in rows if float(row["ink_fraction"]) < 0.0005]
    eligible_rows = [row for row in rows if row not in blank_rows]
    pixel_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in eligible_rows:
        pixel_groups[row["pixel_sha256"]].append(row)
    duplicate_groups = [group for group in pixel_groups.values() if len(group) > 1]
    duplicate_rows: list[dict[str, Any]] = []
    unique_rows: list[dict[str, Any]] = []
    for group in pixel_groups.values():
        unique_rows.append(group[0])
        for duplicate in group[1:]:
            duplicate["duplicate_of"] = group[0]["filename"]
            duplicate_rows.append(duplicate)

    safe_remove_generated(corpus / "images", corpus)
    safe_remove_generated(corpus / "manual_page_cleanup", corpus)
    safe_remove_generated(corpus / "duplicates_excluded", corpus)
    safe_remove_generated(corpus / "blank_pages_excluded", corpus)
    validation_publications = exact_group_split(unique_rows, args.validation_pages, args.seed)
    for row in unique_rows:
        split = "val" if row["publication_number"] in validation_publications else "train"
        relative = Path("images") / split / row["filename"]
        link_or_copy(Path(row["rendered_path"]), corpus / relative)
        row["split"] = split
        row["path"] = relative.as_posix()
    for row in duplicate_rows:
        relative = Path("duplicates_excluded") / row["filename"]
        link_or_copy(Path(row["rendered_path"]), corpus / relative)
        row["split"] = "duplicate_excluded"
        row["path"] = relative.as_posix()
    for row in blank_rows:
        relative = Path("blank_pages_excluded") / row["filename"]
        link_or_copy(Path(row["rendered_path"]), corpus / relative)
        row["split"] = "blank_page_excluded"
        row["path"] = relative.as_posix()
    write_manifest(corpus, rows)

    make_contact_sheet(
        [row for row in rows if row["split"] == "train"],
        corpus / "contact_sheet_train.png",
        args.seed,
        "Saint-Island approved cases - text overlays removed - train sample",
    )
    make_contact_sheet(
        [row for row in rows if row["split"] == "val"],
        corpus / "contact_sheet_val.png",
        args.seed + 1,
        "Saint-Island approved cases - text overlays removed - holdout sample",
    )
    mixed_sheet = corpus / "contact_sheet_manual_page_cleanup.png"
    mixed_sheet.unlink(missing_ok=True)

    stats = json.loads(stats_path.read_text(encoding="utf-8"))
    split_counts = Counter(row["split"] for row in rows)
    stats.update(
        {
            "updated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "preprocess_version": 2,
            "rendered_pages": len(rows),
            "clean_pages": len(unique_rows),
            "blank_pages_excluded": len(blank_rows),
            "exact_pixel_duplicates_excluded": len(duplicate_rows),
            "exact_pixel_duplicate_groups": len(duplicate_groups),
            "mixed_text_drawing_pages_original": 11,
            "mixed_text_drawing_pages_remaining": 0,
            "pdf_text_overlay_pages_processed": processed,
            "pdf_text_regions_masked": regions,
            "pdf_text_characters_masked": characters,
            "pdf_text_mask_policy": (
                "All extractable gazette text lines are whitened by PDF coordinates; "
                "embedded raster drawing and reference labels are preserved"
            ),
            "split_counts": dict(split_counts),
            "patent_counts": {
                split: len(
                    {row["publication_number"] for row in rows if row["split"] == split}
                )
                for split in ("train", "val")
            },
            "publication_overlap_train_val": sorted(
                {row["publication_number"] for row in rows if row["split"] == "train"}
                & {row["publication_number"] for row in rows if row["split"] == "val"}
            ),
            "manual_page_cleanup_policy": (
                "No manual page cleanup required after PDF-coordinate text masking"
            ),
        }
    )
    stats_path.write_text(
        json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
