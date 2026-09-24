"""Freeze the untouched company holdout and seed exhaustive character review.

The generated dataset is evaluation-only.  Images are hard-linked when possible,
and every page starts as unreviewed even when multiple existing models agree.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

from PIL import Image

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from training.manual_annotation.common import (
    Annotation,
    link_or_copy,
    make_page_record,
    normalize_label,
    write_page_record,
)


DEFAULT_CORPUS = (
    PROJECT_ROOT
    / "training"
    / "data_collection"
    / "saint_island_latest_300_20260727"
    / "preprocessed_corpus"
)
DEFAULT_OUTPUT = PROJECT_ROOT / "training" / "gold_validation_v1"
DEFAULT_MODELS = (
    PROJECT_ROOT / "models" / "patent_char_v1.pt",
    PROJECT_ROOT / "models" / "patent_char_v3_consensus.pt",
    PROJECT_ROOT / "models" / "patent_char_v4_company_approved_recall.pt",
)
DEFAULT_GROUP_MODEL = PROJECT_ROOT / "models" / "patent_label_group_v1.pt"


def configure_runtime_paths() -> list[object]:
    environment_root = Path(sys.executable).resolve().parent
    candidates = (
        environment_root,
        environment_root / "Library" / "mingw-w64" / "bin",
        environment_root / "Library" / "usr" / "bin",
        environment_root / "Library" / "bin",
        environment_root / "Scripts",
        environment_root / "bin",
    )
    existing = [str(path) for path in candidates if path.exists()]
    if existing:
        os.environ["PATH"] = os.pathsep.join(existing + [os.environ.get("PATH", "")])
    handles = []
    if hasattr(os, "add_dll_directory"):
        handles = [
            os.add_dll_directory(str(path))
            for path in candidates
            if path.exists()
        ]
    os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
    os.environ.setdefault("YOLO_CONFIG_DIR", os.environ.get("TEMP", str(Path.cwd())))
    return handles


RUNTIME_DLL_HANDLES = configure_runtime_paths()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Freeze and pre-annotate the independent gold validation set."
    )
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--models", nargs="+", type=Path, default=list(DEFAULT_MODELS))
    parser.add_argument("--group-model", type=Path, default=DEFAULT_GROUP_MODEL)
    parser.add_argument("--imgsz", type=int, default=1536)
    parser.add_argument("--confidence", type=float, default=0.03)
    parser.add_argument("--nms-iou", type=float, default=0.70)
    parser.add_argument("--merge-iou", type=float, default=0.65)
    parser.add_argument("--group-confidence", type=float, default=0.08)
    parser.add_argument("--group-nms-iou", type=float, default=0.45)
    parser.add_argument("--group-padding-ratio", type=float, default=0.15)
    parser.add_argument("--device", default=None)
    parser.add_argument("--force-records", action="store_true")
    parser.add_argument(
        "--without-model-seeds",
        action="store_true",
        help="Create empty review records without running existing models.",
    )
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_holdout_rows(corpus: Path) -> list[dict]:
    manifest = corpus / "manifest.jsonl"
    if not manifest.exists():
        raise FileNotFoundError(manifest)
    rows = [
        json.loads(line)
        for line in manifest.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    rows = [row for row in rows if row.get("split") == "val"]
    rows.sort(key=lambda row: (int(row.get("rank", 0)), row["filename"]))
    if not rows:
        raise RuntimeError("The source corpus has no untouched validation pages.")
    return rows


def build_freeze_payload(corpus: Path, rows: list[dict]) -> dict:
    publications = sorted({str(row["publication_number"]) for row in rows})
    pages = []
    for row in rows:
        source = corpus / row["path"]
        if not source.exists():
            raise FileNotFoundError(source)
        actual_hash = sha256(source)
        expected_hash = str(row.get("file_sha256") or "")
        if expected_hash and actual_hash != expected_hash:
            raise RuntimeError(f"Source hash changed: {source}")
        pages.append(
            {
                "page_id": Path(row["filename"]).stem,
                "filename": row["filename"],
                "source_path": str(source.resolve()),
                "file_sha256": actual_hash,
                "pixel_sha256": row.get("pixel_sha256"),
                "publication_number": row.get("publication_number"),
                "application_number": row.get("application_number"),
                "source_pdf_page": row.get("source_pdf_page"),
                "title": row.get("title"),
                "width": int(row["width"]),
                "height": int(row["height"]),
                "dpi": int(row.get("dpi", 300)),
            }
        )
    return {
        "format_version": 1,
        "purpose": "frozen independent gold validation; must never be used for training",
        "source_corpus": str(corpus.resolve()),
        "split": "val",
        "page_count": len(pages),
        "publication_count": len(publications),
        "publications": publications,
        "pages": pages,
    }


def write_or_verify_freeze(path: Path, payload: dict) -> None:
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing != payload:
            raise RuntimeError(
                "The frozen gold manifest differs from the current corpus. "
                "Create a new version instead of mutating gold_validation_v1."
            )
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def box_iou(first: dict, second: dict) -> float:
    left = max(float(first["x1"]), float(second["x1"]))
    top = max(float(first["y1"]), float(second["y1"]))
    right = min(float(first["x2"]), float(second["x2"]))
    bottom = min(float(first["y2"]), float(second["y2"]))
    intersection = max(0.0, right - left) * max(0.0, bottom - top)
    first_area = max(0.0, float(first["x2"]) - float(first["x1"])) * max(
        0.0, float(first["y2"]) - float(first["y1"])
    )
    second_area = max(0.0, float(second["x2"]) - float(second["x1"])) * max(
        0.0, float(second["y2"]) - float(second["y1"])
    )
    union = first_area + second_area - intersection
    return intersection / union if union > 0 else 0.0


def merge_model_suggestions(items: list[dict], threshold: float) -> tuple[list[dict], list[dict]]:
    """Collapse same-location model votes without merging adjacent repeated digits."""

    clusters: list[list[dict]] = []
    for item in sorted(items, key=lambda value: float(value["confidence"]), reverse=True):
        matching = next(
            (
                cluster
                for cluster in clusters
                if any(box_iou(item, existing) >= threshold for existing in cluster)
            ),
            None,
        )
        if matching is None:
            clusters.append([item])
        else:
            matching.append(item)

    selected = []
    diagnostics = []
    for cluster in clusters:
        votes: dict[str, set[str]] = defaultdict(set)
        scores: Counter = Counter()
        for item in cluster:
            votes[item["label"]].add(item["model"])
            scores[item["label"]] += float(item["confidence"])
        winner = max(
            votes,
            key=lambda label: (len(votes[label]), scores[label], max(
                float(item["confidence"])
                for item in cluster
                if item["label"] == label
            )),
        )
        candidates = [item for item in cluster if item["label"] == winner]
        representative = max(candidates, key=lambda item: float(item["confidence"]))
        selected.append(representative)
        diagnostics.append(
            {
                "selected_label": winner,
                "selected_box": [
                    representative["x1"],
                    representative["y1"],
                    representative["x2"],
                    representative["y2"],
                ],
                "model_votes": {
                    label: sorted(model_names) for label, model_names in votes.items()
                },
                "candidates": cluster,
            }
        )
    selected.sort(key=lambda item: (item["y1"], item["x1"]))
    diagnostics.sort(key=lambda item: (item["selected_box"][1], item["selected_box"][0]))
    return selected, diagnostics


def predict_model(model_path: Path, image_paths: list[Path], args) -> dict[str, list[dict]]:
    from ultralytics import YOLO
    import torch

    model_path = model_path.resolve()
    if not model_path.exists():
        raise FileNotFoundError(model_path)
    model = YOLO(str(model_path))
    device = args.device if args.device is not None else (0 if torch.cuda.is_available() else "cpu")
    predictions: dict[str, list[dict]] = defaultdict(list)
    results = model.predict(
        source=[str(path) for path in image_paths],
        imgsz=int(args.imgsz),
        conf=float(args.confidence),
        iou=float(args.nms_iou),
        max_det=500,
        batch=1,
        device=device,
        verbose=False,
        stream=True,
    )
    # Ultralytics replaces paths with image0.jpg/image1.jpg when `source` is a
    # Python list.  The generator preserves source order, so bind each result
    # back to the frozen path explicitly instead of trusting result.path.
    for image_path, result in zip(image_paths, results, strict=True):
        filename = image_path.name
        if result.boxes is None:
            continue
        for box in result.boxes:
            label = normalize_label(str(model.names[int(box.cls[0])]))
            if not label:
                continue
            x1, y1, x2, y2 = (float(value) for value in box.xyxy[0])
            predictions[filename].append(
                {
                    "label": label,
                    "x1": x1,
                    "y1": y1,
                    "x2": x2,
                    "y2": y2,
                    "confidence": float(box.conf[0]),
                    "model": model_path.name,
                }
            )
    return predictions


def predict_group_regions(model_path: Path, image_paths: list[Path], args) -> dict[str, list[dict]]:
    """Find complete-label regions used to reject Chinese body-text false seeds."""

    from ultralytics import YOLO
    import torch

    model_path = model_path.resolve()
    if not model_path.exists():
        raise FileNotFoundError(model_path)
    model = YOLO(str(model_path))
    device = args.device if args.device is not None else (0 if torch.cuda.is_available() else "cpu")
    output: dict[str, list[dict]] = defaultdict(list)
    results = model.predict(
        source=[str(path) for path in image_paths],
        imgsz=int(args.imgsz),
        conf=float(args.group_confidence),
        iou=float(args.group_nms_iou),
        max_det=500,
        batch=1,
        device=device,
        verbose=False,
        stream=True,
    )
    for image_path, result in zip(image_paths, results, strict=True):
        if result.boxes is None:
            continue
        for box in result.boxes:
            x1, y1, x2, y2 = (float(value) for value in box.xyxy[0])
            output[image_path.name].append(
                {
                    "x1": x1,
                    "y1": y1,
                    "x2": x2,
                    "y2": y2,
                    "confidence": float(box.conf[0]),
                }
            )
    return output


def inside_group_region(item: dict, region: dict, padding_ratio: float) -> bool:
    width = float(region["x2"]) - float(region["x1"])
    height = float(region["y2"]) - float(region["y1"])
    padding_x = max(4.0, width * float(padding_ratio))
    padding_y = max(4.0, height * float(padding_ratio))
    center_x = (float(item["x1"]) + float(item["x2"])) / 2.0
    center_y = (float(item["y1"]) + float(item["y2"])) / 2.0
    return (
        float(region["x1"]) - padding_x <= center_x <= float(region["x2"]) + padding_x
        and float(region["y1"]) - padding_y <= center_y <= float(region["y2"]) + padding_y
    )


def filter_suggestions_to_groups(
    suggestions: dict[str, list[dict]],
    regions: dict[str, list[dict]],
    padding_ratio: float,
) -> tuple[dict[str, list[dict]], int]:
    filtered: dict[str, list[dict]] = defaultdict(list)
    rejected = 0
    for filename, items in suggestions.items():
        page_regions = regions.get(filename, [])
        for item in items:
            if any(
                inside_group_region(item, region, padding_ratio)
                for region in page_regions
            ):
                filtered[filename].append(item)
            else:
                rejected += 1
    return filtered, rejected


def main() -> int:
    args = parse_args()
    corpus = args.corpus.resolve()
    output = args.output.resolve()
    rows = read_holdout_rows(corpus)
    payload = build_freeze_payload(corpus, rows)
    freeze_path = output / "freeze_manifest.json"
    write_or_verify_freeze(freeze_path, payload)

    image_output = output / "images" / "val"
    label_output = output / "labels" / "val"
    image_output.mkdir(parents=True, exist_ok=True)
    label_output.mkdir(parents=True, exist_ok=True)
    image_paths = []
    rows_by_filename = {}
    for row in rows:
        source = corpus / row["path"]
        destination = image_output / row["filename"]
        link_or_copy(source, destination)
        image_paths.append(destination)
        rows_by_filename[row["filename"]] = row

    suggestions: dict[str, list[dict]] = defaultdict(list)
    model_paths = [] if args.without_model_seeds else [path.resolve() for path in args.models]
    group_model_path = args.group_model.resolve()
    group_regions = {}
    if model_paths:
        print(f"Locating complete-label regions with {group_model_path.name}...", flush=True)
        group_regions = predict_group_regions(group_model_path, image_paths, args)
    for model_path in model_paths:
        print(f"Seeding gold validation with {model_path.name}...", flush=True)
        model_predictions = predict_model(model_path, image_paths, args)
        for filename, items in model_predictions.items():
            suggestions[filename].extend(items)

    raw_suggestion_count = sum(len(items) for items in suggestions.values())
    suggestions, outside_group_rejections = filter_suggestions_to_groups(
        suggestions,
        group_regions,
        float(args.group_padding_ratio),
    )

    stats = Counter()
    class_counts = Counter()
    for page in payload["pages"]:
        filename = page["filename"]
        label_path = label_output / f"{Path(filename).stem}.json"
        if label_path.exists() and not args.force_records:
            stats["preserved_records"] += 1
            continue
        merged, diagnostics = merge_model_suggestions(
            suggestions.get(filename, []), float(args.merge_iou)
        )
        annotations = [
            Annotation(
                label=item["label"],
                x1=item["x1"],
                y1=item["y1"],
                x2=item["x2"],
                y2=item["y2"],
                source=(
                    f"gold_seed:{item['model']}:{float(item['confidence']):.4f}"
                ),
            )
            for item in merged
        ]
        record = make_page_record(
            page_id=page["page_id"],
            split="val",
            image_path=Path("images") / "val" / filename,
            source_path=page["source_path"],
            image_width=page["width"],
            image_height=page["height"],
            annotations=annotations,
            reviewed=False,
        )
        record["gold_validation"] = {
            "evaluation_only": True,
            "publication_number": page["publication_number"],
            "application_number": page["application_number"],
            "source_pdf_page": page["source_pdf_page"],
            "file_sha256": page["file_sha256"],
            "seed_models": [path.name for path in model_paths],
            "seed_group_model": group_model_path.name if model_paths else None,
            "seed_group_regions": group_regions.get(filename, []),
            "seed_diagnostics": diagnostics,
        }
        write_page_record(label_path, record)
        class_counts.update(item.label for item in annotations)
        stats["created_records"] += 1
        stats["seed_boxes"] += len(annotations)

    report = {
        "format_version": 1,
        "ready_for_manual_review": True,
        "evaluation_only": True,
        "page_count": payload["page_count"],
        "publication_count": payload["publication_count"],
        "models": [str(path) for path in model_paths],
        "group_model": str(group_model_path) if model_paths else None,
        "imgsz": int(args.imgsz),
        "confidence": float(args.confidence),
        "nms_iou": float(args.nms_iou),
        "merge_iou": float(args.merge_iou),
        "group_confidence": float(args.group_confidence),
        "group_nms_iou": float(args.group_nms_iou),
        "group_padding_ratio": float(args.group_padding_ratio),
        "raw_character_suggestions": raw_suggestion_count,
        "outside_group_suggestions_rejected": outside_group_rejections,
        "stats": dict(stats),
        "seed_class_counts": dict(sorted(class_counts.items())),
        "manual_instruction": (
            "Every page remains unreviewed. Correct all proposed boxes, add every "
            "missed character, then mark the page complete. This folder must never "
            "be passed to a training command."
        ),
    }
    (output / "prepare_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
