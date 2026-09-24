"""Create a protected pilot snapshot from reviewed figure-heading pages.

The source annotation queue is never modified. Reviewed records and images are
copied into a document-level split before the existing builder produces four
right-angle training variants.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import random
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from features.patent_ocr.figure_heading_classes import (
    FIGURE_IDENTIFIER_CLASS,
    FIGURE_PREFIX_CLASS,
    FIGURE_PREFIX_ROTATE_RIGHT_CLASS,
)
from training.figure_heading.build_training_dataset import (
    _identifier_types,
    _normalized_annotations,
    _validate_page,
    _validate_split_integrity,
    build_dataset,
)
from training.manual_annotation.common import (
    find_page_records,
    read_page_record,
    write_page_record,
)


DEFAULT_ANNOTATIONS = (
    PROJECT_ROOT.parent
    / "AI訓練圖集"
    / "prepared_dataset_v1"
    / "figure_heading_annotation_v2"
)
DEFAULT_OUTPUT = PROJECT_ROOT / "training" / "figure_heading" / "pilot_250_20260917"
DEFAULT_SEED = 20260917


class PilotDatasetError(RuntimeError):
    """Raised when reviewed gold data cannot form a safe pilot snapshot."""


def _normalized_split(value):
    return "validation" if str(value) == "val" else str(value)


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _document_id(record):
    value = str(
        record.get("document_id")
        or record.get("relative_pdf")
        or str(record.get("source_path", "")).split("#page=", 1)[0]
    ).strip()
    if not value:
        raise PilotDatasetError(
            f"{record.get('page_id', '未知頁面')} 缺少來源文件識別資料。"
        )
    return value


def _source_group(record):
    relative = str(record.get("relative_pdf", "")).replace("\\", "/")
    return relative.split("/", 1)[0] if relative else "unknown"


def _load_and_validate_reviewed(annotation_root):
    annotation_root = Path(annotation_root).resolve()
    record_paths = find_page_records(annotation_root)
    if not record_paths:
        raise PilotDatasetError(f"找不到圖題標註記錄：{annotation_root}")

    entries = []
    prepared = []
    errors = []
    page_ids = set()
    for record_path in record_paths:
        try:
            record = read_page_record(record_path)
        except Exception as error:
            errors.append(f"{record_path.name}：JSON 無法讀取（{error}）")
            continue
        if not record.get("reviewed"):
            continue
        page_id = str(record.get("page_id", "")).strip()
        if not page_id:
            errors.append(f"{record_path.name}：缺少 page_id")
            continue
        if page_id in page_ids:
            errors.append(f"{page_id}：page_id 重複")
            continue
        page_ids.add(page_id)
        try:
            annotations = _normalized_annotations(record)
            pair_count = _validate_page(record, annotations)
            document_id = _document_id(record)
            image_path = annotation_root / record["image_path"]
            if not image_path.is_file():
                raise FileNotFoundError(f"找不到標註圖片：{image_path}")
        except Exception as error:
            errors.append(f"{page_id}：{error}")
            continue
        entry = {
            "record_path": Path(record_path),
            "record": record,
            "annotations": annotations,
            "pair_count": int(pair_count),
            "document_id": document_id,
            "image_path": image_path,
        }
        entries.append(entry)
        prepared.append((record, annotations, pair_count))

    if errors:
        preview = "\n".join(f"- {item}" for item in errors[:20])
        suffix = "" if len(errors) <= 20 else f"\n- 另有 {len(errors) - 20} 筆錯誤"
        raise PilotDatasetError(
            "已完成頁含不完整或無效標註；pilot 不會默默排除：\n"
            + preview
            + suffix
        )
    if not entries:
        raise PilotDatasetError("目前沒有 reviewed=True 的頁面可建立 pilot。")

    try:
        _documents, image_hashes = _validate_split_integrity(
            annotation_root,
            prepared,
        )
    except Exception as error:
        raise PilotDatasetError(f"reviewed 頁的文件／影像 split 驗證失敗：{error}") from error
    for entry in entries:
        entry["image_sha256"] = image_hashes[entry["record"]["page_id"]]
    return entries, len(record_paths) - len(entries)


class _UnionFind:
    def __init__(self, values):
        self.parent = {value: value for value in values}

    def find(self, value):
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, first, second):
        first_root = self.find(first)
        second_root = self.find(second)
        if first_root == second_root:
            return
        if first_root < second_root:
            self.parent[second_root] = first_root
        else:
            self.parent[first_root] = second_root


def _entry_stats(entry):
    stats = Counter(
        pages=1,
        pairs=entry["pair_count"],
        negative_pages=int(entry["pair_count"] == 0),
    )
    stats[f"source:{_source_group(entry['record'])}"] += 1
    for annotation in entry["annotations"]:
        if annotation.label == FIGURE_PREFIX_CLASS:
            stats["prefix_normal"] += 1
        elif annotation.label == FIGURE_PREFIX_ROTATE_RIGHT_CLASS:
            stats["prefix_rotate_right"] += 1
        elif annotation.label == FIGURE_IDENTIFIER_CLASS:
            for identifier_type in _identifier_types(annotation.text):
                stats[f"identifier:{identifier_type}"] += 1
    return stats


def _make_document_groups(entries):
    by_document = defaultdict(list)
    document_splits = defaultdict(set)
    hash_documents = defaultdict(set)
    for entry in entries:
        document_id = entry["document_id"]
        by_document[document_id].append(entry)
        document_splits[document_id].add(
            _normalized_split(entry["record"].get("split"))
        )
        hash_documents[entry["image_sha256"]].add(document_id)

    for document_id, splits in document_splits.items():
        if len(splits) != 1:
            raise PilotDatasetError(
                f"來源文件 {document_id} 同時出現在多個 split：{sorted(splits)}"
            )

    union_find = _UnionFind(by_document)
    for document_ids in hash_documents.values():
        ordered = sorted(document_ids)
        for document_id in ordered[1:]:
            union_find.union(ordered[0], document_id)

    component_documents = defaultdict(set)
    for document_id in by_document:
        component_documents[union_find.find(document_id)].add(document_id)

    groups = []
    for document_ids in component_documents.values():
        group_entries = [
            entry
            for document_id in sorted(document_ids)
            for entry in by_document[document_id]
        ]
        splits = {
            _normalized_split(entry["record"].get("split"))
            for entry in group_entries
        }
        if len(splits) != 1:
            raise PilotDatasetError(
                "相同影像內容連結到不同 split 的文件群："
                + "、".join(sorted(document_ids))
            )
        stats = Counter(documents=len(document_ids))
        for entry in group_entries:
            stats.update(_entry_stats(entry))
        groups.append(
            {
                "key": "|".join(sorted(document_ids)),
                "document_ids": tuple(sorted(document_ids)),
                "entries": group_entries,
                "split": next(iter(splits)),
                "stats": stats,
            }
        )
    return sorted(groups, key=lambda item: item["key"])


def _sum_stats(groups):
    output = Counter()
    for group in groups:
        output.update(group["stats"])
    return output


def _ratio(stats, numerator, denominator):
    return float(stats[numerator]) / max(1.0, float(stats[denominator]))


def _balance_error(validation_stats, pool_stats):
    error = 8.0 * abs(
        _ratio(validation_stats, "prefix_rotate_right", "pairs")
        - _ratio(pool_stats, "prefix_rotate_right", "pairs")
    )
    error += 2.0 * abs(
        _ratio(validation_stats, "negative_pages", "pages")
        - _ratio(pool_stats, "negative_pages", "pages")
    )
    for identifier_type in ("numeric", "letter_bearing", "prime", "pure_alpha"):
        error += 4.0 * abs(
            _ratio(validation_stats, f"identifier:{identifier_type}", "pairs")
            - _ratio(pool_stats, f"identifier:{identifier_type}", "pairs")
        )
    source_names = {
        key.split(":", 1)[1]
        for key in set(validation_stats) | set(pool_stats)
        if key.startswith("source:")
    }
    for source_name in source_names:
        error += abs(
            _ratio(validation_stats, f"source:{source_name}", "pages")
            - _ratio(pool_stats, f"source:{source_name}", "pages")
        )
    return error


def _select_train_groups_for_validation(
    groups,
    *,
    seed,
    target_validation_pages,
    minimum_validation_pages,
    maximum_validation_pages,
    minimum_validation_documents,
    attempts,
):
    train_groups = [group for group in groups if group["split"] == "train"]
    validation_groups = [
        group for group in groups if group["split"] == "validation"
    ]
    if not train_groups:
        raise PilotDatasetError("reviewed 頁沒有 train 文件可供 pilot 重分。")

    locked_stats = _sum_stats(validation_groups)
    train_stats = _sum_stats(train_groups)
    pool_stats = locked_stats + train_stats
    target_moved = max(0, int(target_validation_pages) - locked_stats["pages"])
    minimum_moved = max(0, int(minimum_validation_pages) - locked_stats["pages"])
    maximum_moved = max(0, int(maximum_validation_pages) - locked_stats["pages"])
    minimum_documents = max(
        0,
        int(minimum_validation_documents) - locked_stats["documents"],
    )
    if maximum_moved >= train_stats["pages"]:
        maximum_moved = train_stats["pages"] - 1
    if maximum_moved < minimum_moved or maximum_moved <= 0:
        raise PilotDatasetError(
            "validation 目標會耗盡 train，或無法滿足頁數範圍。"
        )

    ordered = sorted(train_groups, key=lambda item: item["key"])
    rng = random.Random(int(seed))
    best = None
    for attempt in range(max(1, int(attempts))):
        order = list(ordered)
        if attempt:
            rng.shuffle(order)
        else:
            order.sort(
                key=lambda item: hashlib.sha256(
                    f"{seed}:{item['key']}".encode("utf-8")
                ).hexdigest()
            )
        selected = []
        selected_stats = Counter()
        for group in order:
            pages_after = selected_stats["pages"] + group["stats"]["pages"]
            if pages_after > maximum_moved:
                continue
            documents_after = (
                selected_stats["documents"] + group["stats"]["documents"]
            )
            capacity_after = maximum_moved - pages_after
            documents_still_needed = max(0, minimum_documents - documents_after)
            if capacity_after < documents_still_needed:
                continue
            if (
                selected_stats["pages"] >= target_moved
                and selected_stats["documents"] >= minimum_documents
            ):
                break
            selected.append(group)
            selected_stats.update(group["stats"])

        if (
            selected_stats["pages"] < minimum_moved
            or selected_stats["documents"] < minimum_documents
            or selected_stats["pages"] >= train_stats["pages"]
        ):
            continue
        validation_stats = locked_stats + selected_stats
        tie_break = hashlib.sha256(
            (str(seed) + "\n" + "\n".join(sorted(x["key"] for x in selected))).encode(
                "utf-8"
            )
        ).hexdigest()
        score = (
            abs(validation_stats["pages"] - int(target_validation_pages)),
            round(_balance_error(validation_stats, pool_stats), 12),
            abs(
                validation_stats["documents"]
                - max(int(minimum_validation_documents), 1)
            ),
            tie_break,
        )
        if best is None or score < best[0]:
            best = (score, tuple(selected))

    if best is None:
        raise PilotDatasetError(
            "找不到同時保留 train 且符合 validation 頁數／文件數的文件級分割。"
        )
    return list(best[1])


def _distribution(entries, split_by_page):
    output = {}
    for split_name in ("train", "validation", "test"):
        selected = [
            entry
            for entry in entries
            if split_by_page[entry["record"]["page_id"]] == split_name
        ]
        classes = Counter()
        identifiers = Counter()
        sources = Counter()
        documents = set()
        pairs = 0
        negatives = 0
        for entry in selected:
            documents.add(entry["document_id"])
            sources[_source_group(entry["record"])] += 1
            pairs += entry["pair_count"]
            negatives += int(entry["pair_count"] == 0)
            for annotation in entry["annotations"]:
                classes[annotation.label] += 1
                if annotation.label == FIGURE_IDENTIFIER_CLASS:
                    for identifier_type in _identifier_types(annotation.text):
                        identifiers[identifier_type] += 1
        output[split_name] = {
            "pages": len(selected),
            "documents": len(documents),
            "pairs": pairs,
            "negative_pages": negatives,
            "classes": dict(sorted(classes.items())),
            "identifier_types": dict(sorted(identifiers.items())),
            "source_pages": dict(sorted(sources.items())),
        }
    return output


def _write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def prepare_pilot_dataset(
    annotation_root,
    output_root,
    *,
    seed=DEFAULT_SEED,
    target_validation_pages=40,
    minimum_validation_pages=35,
    maximum_validation_pages=40,
    minimum_validation_documents=10,
    selection_attempts=10000,
    build_yolo=True,
):
    """Copy reviewed gold pages, rebalance validation, and build pilot YOLO data."""

    annotation_root = Path(annotation_root).resolve()
    output_root = Path(output_root).resolve()
    snapshot_root = output_root / "snapshot"
    dataset_root = output_root / "dataset"
    if output_root.exists():
        raise FileExistsError(f"pilot 輸出已存在，不會覆寫：{output_root}")

    entries, excluded_unreviewed = _load_and_validate_reviewed(annotation_root)
    groups = _make_document_groups(entries)
    selected_groups = _select_train_groups_for_validation(
        groups,
        seed=seed,
        target_validation_pages=target_validation_pages,
        minimum_validation_pages=minimum_validation_pages,
        maximum_validation_pages=maximum_validation_pages,
        minimum_validation_documents=minimum_validation_documents,
        attempts=selection_attempts,
    )
    moved_documents = {
        document_id
        for group in selected_groups
        for document_id in group["document_ids"]
    }
    split_by_page = {}
    for entry in entries:
        original_split = _normalized_split(entry["record"].get("split"))
        split_by_page[entry["record"]["page_id"]] = (
            "validation"
            if original_split == "train" and entry["document_id"] in moved_documents
            else original_split
        )

    output_root.mkdir(parents=True, exist_ok=False)
    manifest_pages = []
    try:
        used_destinations = set()
        for entry in sorted(entries, key=lambda item: item["record"]["page_id"]):
            record = entry["record"]
            page_id = record["page_id"]
            original_split = _normalized_split(record.get("split"))
            new_split = split_by_page[page_id]
            image_destination = (
                snapshot_root / "images" / new_split / entry["image_path"].name
            )
            record_destination = (
                snapshot_root / "labels" / new_split / entry["record_path"].name
            )
            for destination in (image_destination, record_destination):
                key = str(destination).casefold()
                if key in used_destinations:
                    raise PilotDatasetError(f"snapshot 目標檔名衝突：{destination}")
                used_destinations.add(key)

            image_destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(entry["image_path"], image_destination)
            if _sha256(image_destination) != entry["image_sha256"]:
                raise PilotDatasetError(f"圖片複製後內容不一致：{page_id}")

            snapshot_record = copy.deepcopy(record)
            snapshot_record["split"] = new_split
            snapshot_record["image_path"] = image_destination.relative_to(
                snapshot_root
            ).as_posix()
            write_page_record(record_destination, snapshot_record)
            manifest_pages.append(
                {
                    "page_id": page_id,
                    "document_id": entry["document_id"],
                    "source_group": _source_group(record),
                    "source_record_path": entry["record_path"].relative_to(
                        annotation_root
                    ).as_posix(),
                    "source_image_path": Path(record["image_path"]).as_posix(),
                    "snapshot_record_path": record_destination.relative_to(
                        snapshot_root
                    ).as_posix(),
                    "snapshot_image_path": snapshot_record["image_path"],
                    "original_split": original_split,
                    "new_split": new_split,
                    "content_sha256": entry["image_sha256"],
                    "source_annotation_sha256": _sha256(entry["record_path"]),
                    "snapshot_annotation_sha256": _sha256(record_destination),
                }
            )

        manifest = {
            "version": 1,
            "seed": int(seed),
            "source_annotation_root": str(annotation_root),
            "output_root": str(output_root),
            "reviewed_pages": len(entries),
            "excluded_unreviewed_pages": excluded_unreviewed,
            "target_validation_pages": int(target_validation_pages),
            "validation_page_range": [
                int(minimum_validation_pages),
                int(maximum_validation_pages),
            ],
            "minimum_validation_documents": int(minimum_validation_documents),
            "moved_documents": sorted(moved_documents),
            "distributions": _distribution(entries, split_by_page),
            "pages": manifest_pages,
        }
        manifest_path = output_root / "split_manifest.json"
        _write_json(manifest_path, manifest)

        dataset_report = None
        if build_yolo:
            dataset_report = build_dataset(
                snapshot_root,
                dataset_root,
                allow_small=True,
                allow_incomplete=True,
            )
    except Exception as error:
        # Keep the newly-created experiment directory for diagnosis.  The
        # source gold queue is outside output_root and is never removed.
        try:
            _write_json(
                output_root / "failure.json",
                {
                    "version": 1,
                    "source_annotation_root": str(annotation_root),
                    "output_root": str(output_root),
                    "error_type": type(error).__name__,
                    "error": str(error),
                },
            )
        except Exception:
            pass
        raise

    return {
        "output_root": str(output_root),
        "snapshot_root": str(snapshot_root),
        "dataset_root": str(dataset_root) if build_yolo else None,
        "manifest_path": str(manifest_path),
        "manifest": manifest,
        "dataset_report": dataset_report,
    }


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--annotations", type=Path, default=DEFAULT_ANNOTATIONS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--target-validation-pages", type=int, default=40)
    parser.add_argument("--minimum-validation-pages", type=int, default=35)
    parser.add_argument("--maximum-validation-pages", type=int, default=40)
    parser.add_argument("--minimum-validation-documents", type=int, default=10)
    parser.add_argument("--selection-attempts", type=int, default=10000)
    return parser.parse_args()


def main():
    args = parse_args()
    result = prepare_pilot_dataset(
        args.annotations,
        args.output,
        seed=args.seed,
        target_validation_pages=args.target_validation_pages,
        minimum_validation_pages=args.minimum_validation_pages,
        maximum_validation_pages=args.maximum_validation_pages,
        minimum_validation_documents=args.minimum_validation_documents,
        selection_attempts=args.selection_attempts,
    )
    print(json.dumps(result["manifest"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
