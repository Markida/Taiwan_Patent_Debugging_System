"""Validate reviewed figure-heading pages and build a three-class YOLO dataset."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

import cv2


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from features.patent_ocr.figure_heading import (
    pair_figure_heading_boxes,
    transform_box_clockwise,
)
from features.patent_ocr.figure_heading_classes import (
    FIGURE_HEADING_CLASS_NAMES,
    FIGURE_IDENTIFIER_CLASS,
    FIGURE_PREFIX_CLASS,
    FIGURE_PREFIX_CLASS_NAMES,
    FIGURE_PREFIX_ROTATE_RIGHT_CLASS,
)
from features.patent_ocr.figure_identifiers import normalize_figure_identifier
from features.patent_ocr.image_io import read_image, write_image
from training.manual_annotation.common import (
    Annotation,
    find_page_records,
    link_or_copy,
    read_page_record,
)
from training.figure_heading.dataset_integrity import write_dataset_inventory


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_ANNOTATIONS = (
    PROJECT_ROOT.parent
    / "AI訓練圖集"
    / "prepared_dataset_v1"
    / "figure_heading_annotation_v2"
)
DEFAULT_OUTPUT = SCRIPT_DIR / "figure_heading_yolo_v1"
CLASS_TO_ID = {
    class_name: class_id
    for class_id, class_name in enumerate(FIGURE_HEADING_CLASS_NAMES)
}


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--annotations", type=Path, default=DEFAULT_ANNOTATIONS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--minimum-pairs", type=int, default=400)
    parser.add_argument("--minimum-negative-pages", type=int, default=100)
    parser.add_argument("--minimum-validation-pairs", type=int, default=20)
    parser.add_argument("--minimum-test-pairs", type=int, default=20)
    parser.add_argument("--minimum-validation-negative-pages", type=int, default=5)
    parser.add_argument("--minimum-test-negative-pages", type=int, default=5)
    parser.add_argument("--minimum-validation-documents", type=int, default=5)
    parser.add_argument("--minimum-test-documents", type=int, default=5)
    parser.add_argument("--minimum-letter-pairs", type=int, default=20)
    parser.add_argument("--minimum-prime-pairs", type=int, default=10)
    parser.add_argument("--minimum-pure-alpha-pairs", type=int, default=5)
    parser.add_argument("--minimum-validation-letter-pairs", type=int, default=2)
    parser.add_argument("--minimum-test-letter-pairs", type=int, default=2)
    parser.add_argument("--minimum-validation-prime-pairs", type=int, default=1)
    parser.add_argument("--minimum-test-prime-pairs", type=int, default=1)
    parser.add_argument("--minimum-validation-pure-alpha-pairs", type=int, default=1)
    parser.add_argument("--minimum-test-pure-alpha-pairs", type=int, default=1)
    parser.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="Experimental only: include reviewed pages and skip unfinished pages.",
    )
    parser.add_argument(
        "--allow-small",
        action="store_true",
        help="Experimental only: bypass minimum pair/negative-page gates.",
    )
    return parser.parse_args()


def _normalized_annotations(record):
    try:
        width = int(record["width"])
        height = int(record["height"])
    except (KeyError, TypeError, ValueError):
        raise ValueError(
            f"{record.get('page_id', '未知頁面')} 缺少有效的圖片尺寸。"
        ) from None
    if width <= 0 or height <= 0:
        raise ValueError(f"{record.get('page_id', '未知頁面')} 的圖片尺寸無效。")
    raw_annotations = record.get("annotations", [])
    if not isinstance(raw_annotations, list):
        raise ValueError(f"{record.get('page_id', '未知頁面')} 的標註格式不是清單。")
    output = []
    for index, raw in enumerate(raw_annotations, start=1):
        if not isinstance(raw, dict):
            raise ValueError(
                f"{record.get('page_id', '未知頁面')} 第 {index} 個標註格式錯誤。"
            )
        raw_label = str(raw.get("label") or "")
        try:
            annotation = Annotation(**raw).normalized(width, height)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError(
                f"{record.get('page_id', '未知頁面')} 第 {index} 個標註無法讀取："
                f"{error}"
            ) from error
        if annotation.label not in CLASS_TO_ID:
            raise ValueError(
                f"{record.get('page_id', '未知頁面')} 含不屬於圖題模式的標籤："
                f"{raw_label or '(空白)'}"
            )
        if not annotation.is_valid(width, height):
            raise ValueError(
                f"{record.get('page_id', '未知頁面')} 第 {index} 個"
                f" {annotation.label} 框尺寸過小或座標無效。"
            )
        output.append(annotation)
    return output


def _validate_page(record, annotations):
    invalid_labels = [
        item.label for item in annotations if item.label not in CLASS_TO_ID
    ]
    if invalid_labels:
        raise ValueError(
            f"{record['page_id']} 含不屬於圖題模式的標籤：{invalid_labels}"
        )
    pair_groups = {}
    for item in annotations:
        if not item.pair_id:
            raise ValueError(
                f"{record['page_id']} 的圖題配對編號缺漏；"
                "請在標註工具中重新確認完成本頁。"
            )
        pair_groups.setdefault(item.pair_id, []).append(item)
    for pair_id, group in pair_groups.items():
        labels = Counter(item.label for item in group)
        if (
            len(group) != 2
            or sum(labels[name] for name in FIGURE_PREFIX_CLASS_NAMES) != 1
            or labels[FIGURE_IDENTIFIER_CLASS] != 1
        ):
            raise ValueError(
                f"{record['page_id']} 的配對編號 {pair_id} 必須恰好對應"
                "一個「圖」框與一個「圖號」框，且不得重複共用。"
            )
    prefixes = [
        {
            "x1": item.x1,
            "y1": item.y1,
            "x2": item.x2,
            "y2": item.y2,
            "confidence": 1.0,
            "class_name": item.label,
        }
        for item in annotations
        if item.label in FIGURE_PREFIX_CLASS_NAMES
    ]
    identifiers = [
        {
            "x1": item.x1,
            "y1": item.y1,
            "x2": item.x2,
            "y2": item.y2,
            "confidence": 1.0,
        }
        for item in annotations
        if item.label == FIGURE_IDENTIFIER_CLASS
    ]
    identifier_annotations = [
        item for item in annotations if item.label == FIGURE_IDENTIFIER_CLASS
    ]
    for item in identifier_annotations:
        if not item.text.strip():
            raise ValueError(
                f"{record['page_id']} 有圖號框未輸入實際圖號文字。"
            )
        try:
            item.text = str(normalize_figure_identifier(item.text))
        except ValueError as error:
            raise ValueError(f"{record['page_id']}：{error}") from error
    if len(prefixes) != len(identifiers):
        raise ValueError(
            f"{record['page_id']} 的「圖」與「圖號」框數不相等："
            f"{len(prefixes)} / {len(identifiers)}"
        )
    pairs = pair_figure_heading_boxes(prefixes, identifiers)
    if len(pairs) != len(prefixes):
        raise ValueError(
            f"{record['page_id']} 有框無法依長短軸與距離配成圖題；"
            "請調整框的位置或刪除錯框。"
        )
    prefix_annotations = [
        item for item in annotations if item.label in FIGURE_PREFIX_CLASS_NAMES
    ]
    for pair in pairs:
        prefix = prefix_annotations[pair["prefix_index"]]
        identifier = identifier_annotations[pair["identifier_index"]]
        if not prefix.pair_id or prefix.pair_id != identifier.pair_id:
            raise ValueError(
                f"{record['page_id']} 的圖題配對編號缺漏或不一致；"
                "請在標註工具中重新確認完成本頁。"
            )
    return len(pairs)


def _identifier_types(value):
    text = str(normalize_figure_identifier(value))
    types = ["total"]
    if re.fullmatch(r"\d+", text):
        types.append("numeric")
    if re.search(r"[A-Z]", text):
        types.append("letter_bearing")
    if re.fullmatch(r"[A-Z]+'?", text):
        types.append("pure_alpha")
    if text.endswith("'"):
        types.append("prime")
    return types


def _file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_split_integrity(annotation_root, prepared):
    document_splits = {}
    image_splits = {}
    documents_by_split = Counter()
    image_hashes = {}
    for record, _annotations, _pairs in prepared:
        split = "validation" if record["split"] == "val" else record["split"]
        document_id = str(
            record.get("document_id")
            or record.get("relative_pdf")
            or str(record.get("source_path", "")).split("#page=", 1)[0]
        ).strip()
        if not document_id:
            raise ValueError(f"{record['page_id']} 缺少來源文件識別資料。")
        previous_split = document_splits.setdefault(document_id, split)
        if previous_split != split:
            raise ValueError(
                f"來源文件 {document_id} 同時出現在 {previous_split} 與 {split}。"
            )

        source_image = annotation_root / record["image_path"]
        if not source_image.is_file():
            raise FileNotFoundError(f"找不到標註圖片：{source_image}")
        image_hash = _file_sha256(source_image)
        image_hashes[record["page_id"]] = image_hash
        previous_image_split = image_splits.setdefault(image_hash, split)
        if previous_image_split != split:
            raise ValueError(
                f"相同影像內容同時出現在 {previous_image_split} 與 {split}；"
                "請重新建立文件級分割。"
            )
    for document_id, split in document_splits.items():
        documents_by_split[split] += 1
    return documents_by_split, image_hashes


def _rotated_image(source_path, output_path, degrees):
    if degrees == 0:
        link_or_copy(source_path, output_path)
        return
    image = read_image(source_path)
    if image is None:
        raise ValueError(f"無法讀取標註圖片：{source_path}")
    code = {
        90: cv2.ROTATE_90_CLOCKWISE,
        180: cv2.ROTATE_180,
        270: cv2.ROTATE_90_COUNTERCLOCKWISE,
    }[degrees]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not write_image(output_path, cv2.rotate(image, code)):
        raise RuntimeError(f"無法寫入旋轉訓練圖片：{output_path}")


def _yolo_line(annotation, width, height, degrees, *, class_label=None):
    box = transform_box_clockwise(
        {
            "x1": annotation.x1,
            "y1": annotation.y1,
            "x2": annotation.x2,
            "y2": annotation.y2,
        },
        width,
        height,
        degrees,
    )
    output_width, output_height = (
        (height, width) if degrees in {90, 270} else (width, height)
    )
    output_label = class_label or annotation.label
    transformed = Annotation(
        label=output_label,
        x1=box["x1"],
        y1=box["y1"],
        x2=box["x2"],
        y2=box["y2"],
        source=annotation.source,
    )
    return transformed.to_yolo(
        output_width,
        output_height,
        class_id=CLASS_TO_ID[output_label],
    )


def _caption_ground_truth(annotations):
    prefix_annotations = [
        item for item in annotations if item.label in FIGURE_PREFIX_CLASS_NAMES
    ]
    identifier_annotations = [
        item for item in annotations if item.label == "figure_identifier"
    ]

    def as_box(annotation):
        return {
            "x1": annotation.x1,
            "y1": annotation.y1,
            "x2": annotation.x2,
            "y2": annotation.y2,
            "confidence": 1.0,
            "class_name": annotation.label,
        }

    pairs = pair_figure_heading_boxes(
        [as_box(item) for item in prefix_annotations],
        [as_box(item) for item in identifier_annotations],
    )
    return [
        {
            "pair_id": identifier_annotations[pair["identifier_index"]].pair_id,
            "figure_number": identifier_annotations[
                pair["identifier_index"]
            ].text,
            "correction_degrees": int(pair["correction_degrees"]),
        }
        for pair in pairs
    ]


def _augmented_class_label(annotation, source_corrections, degrees):
    """Encode only a clockwise-90 page state as the sideways prefix class."""

    if annotation.label not in FIGURE_PREFIX_CLASS_NAMES:
        return annotation.label
    try:
        source_correction = int(source_corrections[annotation.pair_id])
    except (KeyError, TypeError, ValueError):
        raise ValueError(
            f"圖題配對 {annotation.pair_id or '(空白)'} 缺少方向真值。"
        ) from None
    resulting_correction = (source_correction - int(degrees)) % 360
    if resulting_correction == 90:
        return FIGURE_PREFIX_ROTATE_RIGHT_CLASS
    return FIGURE_PREFIX_CLASS


def build_dataset(
    annotation_root,
    output_root,
    *,
    minimum_pairs=400,
    minimum_negative_pages=100,
    minimum_validation_pairs=20,
    minimum_test_pairs=20,
    minimum_validation_negative_pages=5,
    minimum_test_negative_pages=5,
    minimum_validation_documents=5,
    minimum_test_documents=5,
    minimum_letter_pairs=20,
    minimum_prime_pairs=10,
    minimum_pure_alpha_pairs=5,
    minimum_validation_letter_pairs=2,
    minimum_test_letter_pairs=2,
    minimum_validation_prime_pairs=1,
    minimum_test_prime_pairs=1,
    minimum_validation_pure_alpha_pairs=1,
    minimum_test_pure_alpha_pairs=1,
    allow_incomplete=False,
    allow_small=False,
):
    annotation_root = Path(annotation_root).resolve()
    output_root = Path(output_root).resolve()
    record_paths = find_page_records(annotation_root)
    if not record_paths:
        raise FileNotFoundError(f"找不到圖題標註記錄：{annotation_root}")

    records = [read_page_record(path) for path in record_paths]
    unfinished = [record["page_id"] for record in records if not record.get("reviewed")]
    if unfinished and not allow_incomplete:
        raise RuntimeError(
            f"仍有 {len(unfinished)} 頁尚未人工確認；完成全部頁面後才能建立正式資料集。"
        )
    if allow_incomplete:
        records = [record for record in records if record.get("reviewed")]
    if not records:
        raise RuntimeError("目前沒有已確認頁面可建立資料集。")

    prepared = []
    pair_count = 0
    negative_pages = 0
    split_pair_counts = Counter()
    split_negative_counts = Counter()
    identifier_type_counts = Counter()
    split_identifier_type_counts = Counter()
    identifier_text_count = 0
    for record in records:
        annotations = _normalized_annotations(record)
        pairs = _validate_page(record, annotations)
        pair_count += pairs
        negative_pages += int(pairs == 0)
        split_name = "validation" if record["split"] == "val" else record["split"]
        split_pair_counts[split_name] += pairs
        split_negative_counts[split_name] += int(pairs == 0)
        identifier_text_count += sum(
            item.label == "figure_identifier" for item in annotations
        )
        for item in annotations:
            if item.label != "figure_identifier":
                continue
            for identifier_type in _identifier_types(item.text):
                identifier_type_counts[identifier_type] += 1
                split_identifier_type_counts[(split_name, identifier_type)] += 1
        prepared.append((record, annotations, pairs))

    documents_by_split, image_hashes = _validate_split_integrity(
        annotation_root,
        prepared,
    )

    if not allow_small and pair_count < int(minimum_pairs):
        raise RuntimeError(
            f"已確認圖題只有 {pair_count} 組，正式訓練至少需要 {minimum_pairs} 組。"
        )
    if not allow_small and negative_pages < int(minimum_negative_pages):
        raise RuntimeError(
            f"已確認負頁只有 {negative_pages} 頁，正式訓練至少需要 "
            f"{minimum_negative_pages} 頁。"
        )
    split_requirements = {
        "validation": (
            int(minimum_validation_pairs),
            int(minimum_validation_negative_pages),
            int(minimum_validation_documents),
        ),
        "test": (
            int(minimum_test_pairs),
            int(minimum_test_negative_pages),
            int(minimum_test_documents),
        ),
    }
    if not allow_small:
        for split_name, requirements in split_requirements.items():
            required_pairs, required_negatives, required_documents = requirements
            actual_pairs = split_pair_counts[split_name]
            actual_negatives = split_negative_counts[split_name]
            if actual_pairs < required_pairs:
                raise RuntimeError(
                    f"{split_name} 只有 {actual_pairs} 組圖題，正式訓練至少需要 "
                    f"{required_pairs} 組。"
                )
            if actual_negatives < required_negatives:
                raise RuntimeError(
                    f"{split_name} 只有 {actual_negatives} 張負頁，正式訓練至少需要 "
                    f"{required_negatives} 張。"
                )
            if documents_by_split[split_name] < required_documents:
                raise RuntimeError(
                    f"{split_name} 只有 {documents_by_split[split_name]} 份來源文件，"
                    f"正式訓練至少需要 {required_documents} 份。"
                )
        rare_requirements = (
            ("letter_bearing", None, int(minimum_letter_pairs), "含英文字母圖號"),
            ("prime", None, int(minimum_prime_pairs), "含 prime 圖號"),
            ("pure_alpha", None, int(minimum_pure_alpha_pairs), "純英文圖號"),
            (
                "letter_bearing",
                "validation",
                int(minimum_validation_letter_pairs),
                "validation 含英文字母圖號",
            ),
            (
                "letter_bearing",
                "test",
                int(minimum_test_letter_pairs),
                "test 含英文字母圖號",
            ),
            (
                "prime",
                "validation",
                int(minimum_validation_prime_pairs),
                "validation 含 prime 圖號",
            ),
            (
                "prime",
                "test",
                int(minimum_test_prime_pairs),
                "test 含 prime 圖號",
            ),
            (
                "pure_alpha",
                "validation",
                int(minimum_validation_pure_alpha_pairs),
                "validation 純英文圖號",
            ),
            (
                "pure_alpha",
                "test",
                int(minimum_test_pure_alpha_pairs),
                "test 純英文圖號",
            ),
        )
        for identifier_type, split_name, required, label in rare_requirements:
            actual = (
                identifier_type_counts[identifier_type]
                if split_name is None
                else split_identifier_type_counts[(split_name, identifier_type)]
            )
            if actual < required:
                raise RuntimeError(
                    f"{label}只有 {actual} 組，正式訓練至少需要 {required} 組。"
                )

    if output_root.exists():
        raise FileExistsError(
            f"輸出資料夾已存在，為避免混入舊檔不會覆寫：{output_root}。"
            "請改用新的 --output 路徑。"
        )
    output_root.mkdir(parents=True, exist_ok=False)

    counts = Counter()
    generated = 0
    evaluation_items = []
    for record, annotations, pairs in prepared:
        source_image = annotation_root / record["image_path"]
        page_captions = _caption_ground_truth(annotations)
        source_corrections = {
            caption["pair_id"]: int(caption["correction_degrees"])
            for caption in page_captions
        }
        source_split = record["split"]
        split = "val" if source_split == "validation" else source_split
        if split not in {"train", "val", "test"}:
            raise ValueError(f"未知資料分割：{source_split}")
        for degrees in (0, 90, 180, 270):
            item_id = f"{record['page_id']}__r{degrees:03d}"
            image_path = output_root / "images" / split / f"{item_id}.png"
            label_path = output_root / "labels" / split / f"{item_id}.txt"
            _rotated_image(source_image, image_path, degrees)
            label_path.parent.mkdir(parents=True, exist_ok=True)
            lines = [
                _yolo_line(
                    annotation,
                    int(record["width"]),
                    int(record["height"]),
                    degrees,
                    class_label=_augmented_class_label(
                        annotation,
                        source_corrections,
                        degrees,
                    ),
                )
                for annotation in annotations
            ]
            label_path.write_text(
                "\n".join(line for line in lines if line)
                + ("\n" if lines else ""),
                encoding="utf-8",
            )
            counts[f"{split}_images"] += 1
            counts[f"{split}_pairs"] += pairs
            generated += 1

            rotated_captions = [
                {
                    **caption,
                    "correction_degrees": (
                        int(caption["correction_degrees"]) - degrees
                    )
                    % 360,
                }
                for caption in page_captions
            ]
            expected_numbers = []
            for caption in rotated_captions:
                if caption["figure_number"] not in expected_numbers:
                    expected_numbers.append(caption["figure_number"])
            corrections = {
                caption["correction_degrees"] for caption in rotated_captions
            }
            expected_correction = (
                next(iter(corrections)) if len(corrections) == 1 else None
            )
            if not rotated_captions:
                orientation_status = "no_evidence"
            elif expected_correction is None:
                orientation_status = "mixed_orientation"
            elif expected_correction == 0:
                orientation_status = "upright"
            else:
                orientation_status = "needs_rotation"
            evaluation_items.append(
                {
                    "item_id": item_id,
                    "page_id": record["page_id"],
                    "document_id": record.get("document_id"),
                    "split": split,
                    "rotation_applied_clockwise": degrees,
                    "image_path": image_path.relative_to(output_root).as_posix(),
                    "expected_figure_numbers": expected_numbers,
                    "expected_orientation_status": orientation_status,
                    "expected_correction_degrees": expected_correction,
                    "captions": rotated_captions,
                }
            )

    data_yaml = (
        f"path: {output_root.as_posix()}\n"
        "train: images/train\n"
        "val: images/val\n"
        "test: images/test\n"
        "names:\n"
        f"  0: {FIGURE_PREFIX_CLASS}\n"
        f"  1: {FIGURE_IDENTIFIER_CLASS}\n"
        f"  2: {FIGURE_PREFIX_ROTATE_RIGHT_CLASS}\n"
    )
    (output_root / "data.yaml").write_text(data_yaml, encoding="utf-8")
    (output_root / "evaluation_manifest.json").write_text(
        json.dumps(evaluation_items, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    dataset_inventory = write_dataset_inventory(output_root)
    report = {
        "version": 2,
        "ready_for_training": (
            not unfinished
            and pair_count >= int(minimum_pairs)
            and negative_pages >= int(minimum_negative_pages)
            and split_pair_counts["validation"]
            >= int(minimum_validation_pairs)
            and split_pair_counts["test"] >= int(minimum_test_pairs)
            and split_negative_counts["validation"]
            >= int(minimum_validation_negative_pages)
            and split_negative_counts["test"]
            >= int(minimum_test_negative_pages)
            and documents_by_split["validation"]
            >= int(minimum_validation_documents)
            and documents_by_split["test"] >= int(minimum_test_documents)
            and identifier_type_counts["letter_bearing"]
            >= int(minimum_letter_pairs)
            and identifier_type_counts["prime"] >= int(minimum_prime_pairs)
            and identifier_type_counts["pure_alpha"]
            >= int(minimum_pure_alpha_pairs)
            and split_identifier_type_counts[("validation", "letter_bearing")]
            >= int(minimum_validation_letter_pairs)
            and split_identifier_type_counts[("test", "letter_bearing")]
            >= int(minimum_test_letter_pairs)
            and split_identifier_type_counts[("validation", "prime")]
            >= int(minimum_validation_prime_pairs)
            and split_identifier_type_counts[("test", "prime")]
            >= int(minimum_test_prime_pairs)
            and split_identifier_type_counts[("validation", "pure_alpha")]
            >= int(minimum_validation_pure_alpha_pairs)
            and split_identifier_type_counts[("test", "pure_alpha")]
            >= int(minimum_test_pure_alpha_pairs)
        ),
        "annotation_root": str(annotation_root),
        "reviewed_pages": len(records),
        "unfinished_pages": len(unfinished),
        "figure_heading_pairs": pair_count,
        "identifier_text_ground_truth_count": identifier_text_count,
        "negative_pages": negative_pages,
        "split_pair_counts": dict(sorted(split_pair_counts.items())),
        "split_negative_page_counts": dict(
            sorted(split_negative_counts.items())
        ),
        "identifier_type_counts": dict(sorted(identifier_type_counts.items())),
        "split_identifier_type_counts": {
            split_name: {
                identifier_type: split_identifier_type_counts[
                    (split_name, identifier_type)
                ]
                for identifier_type in (
                    "total",
                    "numeric",
                    "letter_bearing",
                    "pure_alpha",
                    "prime",
                )
            }
            for split_name in ("train", "validation", "test")
        },
        "documents_by_split": dict(sorted(documents_by_split.items())),
        "generated_rotated_images": generated,
        "evaluation_manifest_items": len(evaluation_items),
        "classes": CLASS_TO_ID,
        "counts": dict(sorted(counts.items())),
        "document_level_split_preserved": True,
        "cross_split_duplicate_images": 0,
        "source_image_set_sha256": hashlib.sha256(
            "\n".join(
                f"{page_id}:{image_hashes[page_id]}"
                for page_id in sorted(image_hashes)
            ).encode("utf-8")
        ).hexdigest(),
        "annotation_set_sha256": hashlib.sha256(
            json.dumps(
                records,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        "dataset_file_count": dataset_inventory["file_count"],
        "dataset_content_sha256": dataset_inventory["content_sha256"],
        "dataset_inventory_sha256": dataset_inventory["inventory_sha256"],
        "formal_minimums": {
            "pairs": int(minimum_pairs),
            "negative_pages": int(minimum_negative_pages),
            "validation_pairs": int(minimum_validation_pairs),
            "test_pairs": int(minimum_test_pairs),
            "validation_negative_pages": int(
                minimum_validation_negative_pages
            ),
            "test_negative_pages": int(minimum_test_negative_pages),
            "validation_documents": int(minimum_validation_documents),
            "test_documents": int(minimum_test_documents),
            "letter_pairs": int(minimum_letter_pairs),
            "prime_pairs": int(minimum_prime_pairs),
            "pure_alpha_pairs": int(minimum_pure_alpha_pairs),
            "validation_letter_pairs": int(minimum_validation_letter_pairs),
            "test_letter_pairs": int(minimum_test_letter_pairs),
            "validation_prime_pairs": int(minimum_validation_prime_pairs),
            "test_prime_pairs": int(minimum_test_prime_pairs),
            "validation_pure_alpha_pairs": int(
                minimum_validation_pure_alpha_pairs
            ),
            "test_pure_alpha_pairs": int(minimum_test_pure_alpha_pairs),
        },
        "right_angle_augmentation": [0, 90, 180, 270],
    }
    (output_root / "dataset_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def main():
    args = parse_args()
    report = build_dataset(
        args.annotations,
        args.output,
        minimum_pairs=args.minimum_pairs,
        minimum_negative_pages=args.minimum_negative_pages,
        minimum_validation_pairs=args.minimum_validation_pairs,
        minimum_test_pairs=args.minimum_test_pairs,
        minimum_validation_negative_pages=(
            args.minimum_validation_negative_pages
        ),
        minimum_test_negative_pages=args.minimum_test_negative_pages,
        minimum_validation_documents=args.minimum_validation_documents,
        minimum_test_documents=args.minimum_test_documents,
        minimum_letter_pairs=args.minimum_letter_pairs,
        minimum_prime_pairs=args.minimum_prime_pairs,
        minimum_pure_alpha_pairs=args.minimum_pure_alpha_pairs,
        minimum_validation_letter_pairs=args.minimum_validation_letter_pairs,
        minimum_test_letter_pairs=args.minimum_test_letter_pairs,
        minimum_validation_prime_pairs=args.minimum_validation_prime_pairs,
        minimum_test_prime_pairs=args.minimum_test_prime_pairs,
        minimum_validation_pure_alpha_pairs=(
            args.minimum_validation_pure_alpha_pairs
        ),
        minimum_test_pure_alpha_pairs=args.minimum_test_pure_alpha_pairs,
        allow_incomplete=args.allow_incomplete,
        allow_small=args.allow_small,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
