"""Build a review queue for the dedicated 圖 + figure identifier locator.

Only pairs that exist in a PDF text layer are pre-populated. Additional pages
are included with no boxes and must be reviewed manually; they are not treated
as negative training examples until the reviewer marks the page complete.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

import fitz


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from features.patent_ocr.figure_heading import score_heading_geometry
from features.patent_ocr.figure_identifiers import normalize_figure_identifier
from training.manual_annotation.common import (
    Annotation,
    find_page_records,
    link_or_copy,
    make_page_record,
    read_page_record,
    write_page_record,
)


DEFAULT_SOURCE_ROOT = PROJECT_ROOT.parent / "AI訓練圖集"
DEFAULT_PREPARED_ROOT = DEFAULT_SOURCE_ROOT / "prepared_dataset_v1"
DEFAULT_OUTPUT_ROOT = DEFAULT_PREPARED_ROOT / "figure_heading_annotation_v2"
DEFAULT_PREVIOUS_ANNOTATIONS = (
    DEFAULT_PREPARED_ROOT / "figure_heading_annotation_v1"
)
MANIFEST_FIELDS = (
    "page_id",
    "split",
    "source_group",
    "relative_pdf",
    "page_number",
    "figure_number",
    "raw_identifier_text",
    "direction",
    "correction_degrees",
    "geometry_score",
    "prefix_box",
    "identifier_box",
    "line_text",
    "font_size",
    "block_pdf_box",
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Create a three-class figure-heading annotation queue."
    )
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--prepared-root", type=Path, default=DEFAULT_PREPARED_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument(
        "--previous-annotations",
        type=Path,
        default=DEFAULT_PREVIOUS_ANNOTATIONS,
        help=(
            "Optional earlier queue. Pages whose generated seeds are retired "
            "by stricter rules are retained as unreviewed hard-negative pages."
        ),
    )
    parser.add_argument(
        "--target-pages",
        type=int,
        default=500,
        help="Total review queue size including text-layer seed pages.",
    )
    return parser.parse_args()


def _is_true(value):
    return str(value or "").strip().lower() in {"1", "true", "yes"}


def _load_page_rows(prepared_root):
    manifest_path = Path(prepared_root) / "manifests" / "page_splits.csv"
    if not manifest_path.exists():
        raise FileNotFoundError(f"找不到整理後頁面清單：{manifest_path}")
    with manifest_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return [
        row
        for row in rows
        if _is_true(row.get("is_canonical_page"))
        and row.get("status") in {"ok", "existing", "converted"}
        and row.get("split") in {"train", "validation", "test"}
    ]


def _line_characters(line):
    output = []
    for span in line.get("spans", []):
        for character in span.get("chars", []):
            value = str(character.get("c", ""))
            bbox = character.get("bbox")
            if value and bbox and len(bbox) == 4:
                output.append(
                    {
                        "text": value,
                        "bbox": tuple(map(float, bbox)),
                        "font_size": float(span.get("size") or 0.0),
                    }
                )
    return output


def _normalized_character(value):
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    return normalized.replace("′", "'").replace("’", "'").replace("`", "'")


def _union_box(boxes):
    return {
        "x1": min(box[0] for box in boxes),
        "y1": min(box[1] for box in boxes),
        "x2": max(box[2] for box in boxes),
        "y2": max(box[3] for box in boxes),
    }


def _extract_line_pairs(line):
    characters = _line_characters(line)
    line_text = "".join(item["text"] for item in characters)
    cursor = 0
    while (
        cursor < len(characters)
        and _normalized_character(characters[cursor]["text"]).isspace()
    ):
        cursor += 1
    if cursor >= len(characters) or characters[cursor]["text"] != "圖":
        return []
    prefix = characters[cursor]
    cursor += 1
    while (
        cursor < len(characters)
        and _normalized_character(characters[cursor]["text"]).isspace()
    ):
        cursor += 1
    identifier_chars = []
    identifier_text = []
    while cursor < len(characters) and len(identifier_text) < 8:
        normalized = _normalized_character(characters[cursor]["text"])
        if re.fullmatch(r"[0-9A-Za-z']", normalized) is None:
            break
        identifier_text.append(normalized)
        identifier_chars.append(characters[cursor])
        cursor += 1
    if not identifier_chars:
        return []
    if any(
        not _normalized_character(item["text"]).isspace()
        for item in characters[cursor:]
    ):
        return []
    try:
        figure_number = normalize_figure_identifier("".join(identifier_text))
    except ValueError:
        return []
    return [
        {
            "figure_number": figure_number,
            "raw_identifier_text": "".join(
                item["text"] for item in identifier_chars
            ),
            "prefix_pdf_box": _union_box([prefix["bbox"]]),
            "identifier_pdf_box": _union_box(
                [item["bbox"] for item in identifier_chars]
            ),
            "line_text": line_text,
            "font_size": prefix["font_size"],
        }
    ]


def extract_page_pairs(page):
    raw = page.get_text("rawdict")
    output = []
    for block in raw.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for pair in _extract_line_pairs(line):
                pair["block_pdf_box"] = block.get("bbox")
                output.append(pair)
    return output


def _pdf_box_to_processed(box, page, row):
    rectangle = fitz.Rect(box["x1"], box["y1"], box["x2"], box["y2"])
    rectangle = rectangle * page.rotation_matrix
    scale = float(row["dpi"]) / 72.0
    x1 = rectangle.x0 * scale - float(row["crop_left"])
    y1 = rectangle.y0 * scale - float(row["crop_top"])
    x2 = rectangle.x1 * scale - float(row["crop_left"])
    y2 = rectangle.y1 * scale - float(row["crop_top"])
    return {
        "x1": max(0.0, min(float(row["processed_width"]), x1)),
        "y1": max(0.0, min(float(row["processed_height"]), y1)),
        "x2": max(0.0, min(float(row["processed_width"]), x2)),
        "y2": max(0.0, min(float(row["processed_height"]), y2)),
        "confidence": 1.0,
    }


def _page_id(row):
    return f"{row['document_id']}__p{int(row['page_number']):04d}"


def _stable_rank(row):
    key = f"{row['source_group']}|{row['document_id']}|{row['page_number']}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


def _write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _same_annotation_box(raw, candidate, tolerance=1.0):
    return all(
        abs(float(raw.get(key, 0.0)) - float(getattr(candidate, key)))
        <= tolerance
        for key in ("x1", "y1", "x2", "y2")
    )


def _backfill_seed_metadata(record_path, candidates):
    """Add missing seed text/pair metadata without replacing reviewer edits."""

    record = read_page_record(record_path)
    changed = 0
    for raw in record.get("annotations", []):
        if raw.get("source") != "pdf_text_seed":
            continue
        matches = [
            candidate
            for candidate in candidates
            if candidate.label == raw.get("label")
            and _same_annotation_box(raw, candidate)
        ]
        if len(matches) != 1:
            continue
        candidate = matches[0]
        for key in ("text", "seed_text", "pair_id"):
            value = str(getattr(candidate, key) or "")
            if value and not str(raw.get(key) or "").strip():
                raw[key] = value
                changed += 1
    if changed:
        write_page_record(record_path, record)
    return changed


def build_seed(
    source_root,
    prepared_root,
    output_root,
    target_pages=500,
    previous_annotations=None,
):
    source_root = Path(source_root).resolve()
    prepared_root = Path(prepared_root).resolve()
    output_root = Path(output_root).resolve()
    rows = _load_page_rows(prepared_root)
    rows_by_pdf = {}
    for row in rows:
        rows_by_pdf.setdefault(row["relative_pdf"], {})[
            int(row["page_number"])
        ] = row

    page_annotations = {}
    seed_manifest = []
    duplicate_keys = set()
    extraction_errors = []
    backfilled_metadata_fields = 0
    for relative_pdf, pages in sorted(rows_by_pdf.items()):
        pdf_path = source_root / Path(relative_pdf)
        try:
            document = fitz.open(pdf_path)
            for page_number, row in pages.items():
                page = document[page_number - 1]
                for pair in extract_page_pairs(page):
                    prefix_box = _pdf_box_to_processed(
                        pair["prefix_pdf_box"],
                        page,
                        row,
                    )
                    identifier_box = _pdf_box_to_processed(
                        pair["identifier_pdf_box"],
                        page,
                        row,
                    )
                    geometry = score_heading_geometry(prefix_box, identifier_box)
                    if geometry is None:
                        continue
                    dedupe_key = (
                        _page_id(row),
                        str(pair["figure_number"]),
                        *(round(prefix_box[key], 1) for key in ("x1", "y1", "x2", "y2")),
                        *(round(identifier_box[key], 1) for key in ("x1", "y1", "x2", "y2")),
                    )
                    if dedupe_key in duplicate_keys:
                        continue
                    duplicate_keys.add(dedupe_key)
                    annotations = page_annotations.setdefault(_page_id(row), [])
                    pair_id = f"caption-{len(annotations) // 2 + 1:03d}"
                    figure_text = str(pair["figure_number"])
                    seed_text = str(pair.get("raw_identifier_text") or figure_text)
                    annotations.extend(
                        [
                            Annotation(
                                label="figure_prefix",
                                x1=prefix_box["x1"],
                                y1=prefix_box["y1"],
                                x2=prefix_box["x2"],
                                y2=prefix_box["y2"],
                                source="pdf_text_seed",
                                pair_id=pair_id,
                            ),
                            Annotation(
                                label="figure_identifier",
                                x1=identifier_box["x1"],
                                y1=identifier_box["y1"],
                                x2=identifier_box["x2"],
                                y2=identifier_box["y2"],
                                source="pdf_text_seed",
                                text=figure_text,
                                seed_text=seed_text,
                                pair_id=pair_id,
                            ),
                        ]
                    )
                    seed_manifest.append(
                        {
                            "page_id": _page_id(row),
                            "split": row["split"],
                            "source_group": row["source_group"],
                            "relative_pdf": relative_pdf,
                            "page_number": page_number,
                            "figure_number": pair["figure_number"],
                            "raw_identifier_text": seed_text,
                            "direction": geometry["direction"],
                            "correction_degrees": geometry["correction_degrees"],
                            "geometry_score": geometry["geometry_score"],
                            "prefix_box": json.dumps(prefix_box, ensure_ascii=False),
                            "identifier_box": json.dumps(identifier_box, ensure_ascii=False),
                            "line_text": pair.get("line_text", ""),
                            "font_size": pair.get("font_size", 0.0),
                            "block_pdf_box": json.dumps(
                                pair.get("block_pdf_box"),
                                ensure_ascii=False,
                            ),
                        }
                    )
            document.close()
        except Exception as error:
            extraction_errors.append(
                {"relative_pdf": relative_pdf, "error": str(error)}
            )

    row_by_page_id = {_page_id(row): row for row in rows}
    retired_seed_page_ids = set()
    previous_root = (
        Path(previous_annotations).resolve()
        if previous_annotations is not None
        else None
    )
    if (
        previous_root is not None
        and previous_root != output_root
        and previous_root.is_dir()
    ):
        for record_path in find_page_records(previous_root):
            record = read_page_record(record_path)
            page_id = str(record.get("page_id") or "")
            had_generated_seed = any(
                str(item.get("source") or "") == "pdf_text_seed"
                for item in record.get("annotations", [])
                if isinstance(item, dict)
            )
            if (
                had_generated_seed
                and page_id in row_by_page_id
                and page_id not in page_annotations
            ):
                retired_seed_page_ids.add(page_id)
    existing_ids = set()
    for record_path in find_page_records(output_root):
        record = read_page_record(record_path)
        page_id = str(record.get("page_id") or "")
        if page_id not in row_by_page_id:
            raise RuntimeError(
                f"既有標註頁 {page_id or record_path.name} 已不在整理後清單中；"
                "為避免誤動人工成果，請先人工確認。"
            )
        existing_ids.add(page_id)
    selected_ids = (
        set(page_annotations) | existing_ids | retired_seed_page_ids
    )
    requested_count = max(len(selected_ids), int(target_pages))
    # Extra pages are an annotation queue, not trusted negatives. Stable
    # ordering makes a resumed/rebuilt queue deterministic.
    for row in sorted(rows, key=_stable_rank):
        if len(selected_ids) >= requested_count:
            break
        selected_ids.add(_page_id(row))

    for page_id in sorted(selected_ids):
        row = row_by_page_id[page_id]
        source_image = prepared_root / Path(row["processed_path"])
        split = row["split"]
        destination_image = output_root / "images" / split / f"{page_id}.png"
        destination_record = output_root / "labels" / split / f"{page_id}.json"
        link_or_copy(source_image, destination_image)
        # A rebuild may be used to refresh inventory reports. Never overwrite
        # a reviewer's saved draft or completed page.
        if destination_record.exists():
            backfilled_metadata_fields += _backfill_seed_metadata(
                destination_record,
                page_annotations.get(page_id, []),
            )
            continue
        record = make_page_record(
            page_id=page_id,
            split=split,
            image_path=destination_image.relative_to(output_root),
            source_path=f"{row['relative_pdf']}#page={row['page_number']}",
            image_width=int(row["processed_width"]),
            image_height=int(row["processed_height"]),
            annotations=page_annotations.get(page_id, []),
            reviewed=False,
        )
        record["annotation_mode"] = "figure-heading"
        record["document_id"] = row["document_id"]
        record["relative_pdf"] = row["relative_pdf"]
        record["page_number"] = int(row["page_number"])
        write_page_record(destination_record, record)

    manifests = output_root / "manifests"
    _write_csv(manifests / "text_layer_seed_pairs.csv", seed_manifest)
    (manifests / "extraction_errors.json").write_text(
        json.dumps(extraction_errors, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    split_counts = Counter(row_by_page_id[page_id]["split"] for page_id in selected_ids)
    source_counts = Counter(
        row_by_page_id[page_id]["source_group"] for page_id in selected_ids
    )
    seed_split_counts = Counter(item["split"] for item in seed_manifest)
    seed_source_counts = Counter(item["source_group"] for item in seed_manifest)
    seed_direction_counts = Counter(item["direction"] for item in seed_manifest)
    summary = {
        "version": 1,
        "selected_pages": len(selected_ids),
        "seed_pages": len(page_annotations),
        "seed_pairs": len(seed_manifest),
        "seed_boxes": len(seed_manifest) * 2,
        "seed_identifier_texts": len(seed_manifest),
        "backfilled_metadata_fields": backfilled_metadata_fields,
        "retired_seed_review_pages": len(retired_seed_page_ids),
        "additional_unreviewed_pages": len(selected_ids) - len(page_annotations),
        "all_pages_reviewed": False,
        "split_counts": dict(sorted(split_counts.items())),
        "source_counts": dict(sorted(source_counts.items())),
        "seed_split_counts": dict(sorted(seed_split_counts.items())),
        "seed_source_counts": dict(sorted(seed_source_counts.items())),
        "seed_direction_counts": dict(sorted(seed_direction_counts.items())),
        "extraction_error_count": len(extraction_errors),
        "warning": (
            "PDF text-layer boxes are only seeds. Every page and every pair "
            "must be visually reviewed before training."
        ),
    }
    (output_root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def main():
    args = parse_args()
    summary = build_seed(
        args.source_root,
        args.prepared_root,
        args.output,
        target_pages=args.target_pages,
        previous_annotations=args.previous_annotations,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
