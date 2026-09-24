"""Build a conservative v3 character dataset from unlabelled patent drawings.

The group locator first extracts likely reference-label regions.  The v1 and
v2 character models then act as independent teachers.  Low-confidence or
disagreeing predictions are never silently promoted to labels: they are drawn
into the review folder and recorded in JSONL.
"""

from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np
import torch

try:
    from .common import (
        BASE_CLASS_NAMES,
        CLASS_NAMES,
        CLASS_TO_ID,
        DEFAULT_CONFIG,
        append_jsonl,
        link_or_copy,
        load_config,
        model_names_as_tuple,
        read_image,
        read_jsonl,
        write_data_yaml,
        write_png,
    )
except ImportError:
    from common import (
        BASE_CLASS_NAMES,
        CLASS_NAMES,
        CLASS_TO_ID,
        DEFAULT_CONFIG,
        append_jsonl,
        link_or_copy,
        load_config,
        model_names_as_tuple,
        read_image,
        read_jsonl,
        write_data_yaml,
        write_png,
    )

from ultralytics import YOLO


@dataclass
class Prediction:
    label: str
    confidence: float
    x1: float
    y1: float
    x2: float
    y2: float
    teacher: str

    @property
    def height(self) -> float:
        return max(0.0, self.y2 - self.y1)

    @property
    def width(self) -> float:
        return max(0.0, self.x2 - self.x1)


def parse_args():
    parser = argparse.ArgumentParser(description="Build the v3 consensus dataset.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--work", type=Path, default=None)
    parser.add_argument("--max-pages", type=int, default=None)
    parser.add_argument("--device", default=None)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def box_iou(first: Prediction, second: Prediction) -> float:
    x1 = max(first.x1, second.x1)
    y1 = max(first.y1, second.y1)
    x2 = min(first.x2, second.x2)
    y2 = min(first.y2, second.y2)
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = first.width * first.height + second.width * second.height - intersection
    return intersection / union if union > 0 else 0.0


def averaged_box(first: Prediction, second: Prediction) -> Prediction:
    total = max(1e-9, first.confidence + second.confidence)
    return Prediction(
        label=first.label,
        confidence=min(first.confidence, second.confidence),
        x1=(first.x1 * first.confidence + second.x1 * second.confidence) / total,
        y1=(first.y1 * first.confidence + second.y1 * second.confidence) / total,
        x2=(first.x2 * first.confidence + second.x2 * second.confidence) / total,
        y2=(first.y2 * first.confidence + second.y2 * second.confidence) / total,
        teacher="consensus",
    )


def predictions_from_result(result, model, teacher: str) -> list[Prediction]:
    predictions = []
    if result.boxes is None:
        return predictions
    names = model.names
    for box in result.boxes:
        class_id = int(box.cls[0])
        label = str(names[class_id] if isinstance(names, dict) else names[class_id])
        if label == "'":
            label = "prime"
        if label not in CLASS_TO_ID:
            continue
        x1, y1, x2, y2 = (float(value) for value in box.xyxy[0])
        predictions.append(
            Prediction(
                label=label,
                confidence=float(box.conf[0]),
                x1=x1,
                y1=y1,
                x2=x2,
                y2=y2,
                teacher=teacher,
            )
        )
    return predictions


def predictions_in_crop(
    predictions: list[Prediction], x1: int, y1: int, x2: int, y2: int
) -> list[Prediction]:
    """Select page-scale predictions by centre and translate to crop coordinates."""

    selected = []
    for item in predictions:
        center_x = (item.x1 + item.x2) / 2
        center_y = (item.y1 + item.y2) / 2
        if not (x1 <= center_x <= x2 and y1 <= center_y <= y2):
            continue
        selected.append(
            Prediction(
                label=item.label,
                confidence=item.confidence,
                x1=max(0.0, item.x1 - x1),
                y1=max(0.0, item.y1 - y1),
                x2=min(float(x2 - x1), item.x2 - x1),
                y2=min(float(y2 - y1), item.y2 - y1),
                teacher=item.teacher,
            )
        )
    return selected


def is_large_enough(prediction: Prediction, reference_height: float | None, cfg: dict) -> bool:
    if reference_height is None:
        return True
    if prediction.label == "0":
        return prediction.height >= reference_height * float(cfg["zero_min_height_ratio"])
    if prediction.label == "J":
        return prediction.height >= reference_height * float(cfg["j_min_height_ratio"])
    return True


def standalone_threshold(label: str, teacher: str, cfg: dict) -> float:
    base = float(cfg["v2_only_minimum"] if teacher == "v2" else cfg["v1_only_minimum"])
    if label == "0":
        return max(base, float(cfg["zero_minimum"]))
    if label == "J":
        return max(base, float(cfg["j_minimum"]))
    if label == "prime":
        return max(base, float(cfg["prime_minimum"]))
    return base


def merge_teachers(v1: list[Prediction], v2: list[Prediction], cfg: dict):
    agreement_iou = float(cfg["agreement_iou"])
    candidates = sorted(
        (
            (box_iou(a, b), ai, bi)
            for ai, a in enumerate(v1)
            for bi, b in enumerate(v2)
            if a.label == b.label and box_iou(a, b) >= agreement_iou
        ),
        reverse=True,
    )
    used_v1: set[int] = set()
    used_v2: set[int] = set()
    matches = []
    for overlap, ai, bi in candidates:
        if ai not in used_v1 and bi not in used_v2:
            used_v1.add(ai)
            used_v2.add(bi)
            matches.append((ai, bi, overlap))

    conflict_v1: set[int] = set()
    conflict_v2: set[int] = set()
    for ai, first in enumerate(v1):
        for bi, second in enumerate(v2):
            if first.label != second.label and box_iou(first, second) >= agreement_iou:
                conflict_v1.add(ai)
                conflict_v2.add(bi)

    reliable_heights = [
        item.height
        for item in [*v1, *v2]
        if item.label not in {"0", "J", "prime"} and item.confidence >= 0.20
    ]
    reference_height = float(np.median(reliable_heights)) if reliable_heights else None
    accepted: list[Prediction] = []
    rejected: list[dict] = []

    for ai, bi, overlap in matches:
        first, second = v1[ai], v2[bi]
        merged = averaged_box(first, second)
        if first.confidence < float(cfg["consensus_min_v1"]) or second.confidence < float(cfg["consensus_min_v2"]):
            rejected.append({"reason": "consensus_confidence_too_low", "v1": asdict(first), "v2": asdict(second), "iou": overlap})
        elif not is_large_enough(merged, reference_height, cfg):
            rejected.append({"reason": f"small_{merged.label}_candidate", "v1": asdict(first), "v2": asdict(second), "iou": overlap})
        else:
            accepted.append(merged)

    for teacher, predictions, used, conflicts in (
        ("v2", v2, used_v2, conflict_v2),
        ("v1", v1, used_v1, conflict_v1),
    ):
        for index, prediction in enumerate(predictions):
            if index in used:
                continue
            if index in conflicts:
                rejected.append({"reason": "teacher_class_conflict", "prediction": asdict(prediction)})
                continue
            threshold = standalone_threshold(prediction.label, teacher, cfg)
            # A lone v1 prediction for the two known high-risk classes must be
            # reviewed; it is never automatically promoted to ground truth.
            if teacher == "v1" and prediction.label in {"0", "J"}:
                rejected.append({"reason": f"v1_only_high_risk_{prediction.label}", "prediction": asdict(prediction)})
            elif prediction.confidence < threshold:
                rejected.append({"reason": "single_teacher_below_threshold", "required": threshold, "prediction": asdict(prediction)})
            elif not is_large_enough(prediction, reference_height, cfg):
                rejected.append({"reason": f"small_{prediction.label}_candidate", "prediction": asdict(prediction)})
            else:
                accepted.append(prediction)

    # Remove duplicate boxes left by teacher matching and flag class conflicts.
    final: list[Prediction] = []
    for prediction in sorted(accepted, key=lambda item: item.confidence, reverse=True):
        overlaps = [(box_iou(prediction, known), known) for known in final]
        if any(overlap >= 0.60 and prediction.label == known.label for overlap, known in overlaps):
            continue
        if any(overlap >= 0.45 and prediction.label != known.label for overlap, known in overlaps):
            rejected.append({"reason": "accepted_class_overlap", "prediction": asdict(prediction)})
            continue
        final.append(prediction)
    final.sort(key=lambda item: (item.y1, item.x1))
    # These are the two user-reported high-risk classes.  Keep a seed box for
    # fast human correction, but do not let either enter pseudo ground truth
    # until a reviewer confirms it (or deletes it as a circle/stroke).
    for prediction in final:
        if prediction.label in {"0", "J"}:
            rejected.append(
                {
                    "reason": "high_risk_class_requires_manual_review",
                    "prediction": asdict(prediction),
                }
            )
    return final, rejected, reference_height


def yolo_lines(predictions: list[Prediction], width: int, height: int) -> list[str]:
    lines = []
    for item in predictions:
        x1, x2 = sorted((max(0.0, item.x1), min(float(width), item.x2)))
        y1, y2 = sorted((max(0.0, item.y1), min(float(height), item.y2)))
        if x2 <= x1 or y2 <= y1:
            continue
        xc = (x1 + x2) / 2 / width
        yc = (y1 + y2) / 2 / height
        w = (x2 - x1) / width
        h = (y2 - y1) / height
        lines.append(f"{CLASS_TO_ID[item.label]} {xc:.8f} {yc:.8f} {w:.8f} {h:.8f}")
    return lines


def rejected_prediction(record: dict) -> Prediction | None:
    payload = record.get("prediction") or record.get("v1") or record.get("v2")
    try:
        return Prediction(**payload) if payload else None
    except TypeError:
        return None


def draw_review(crop: np.ndarray, accepted: list[Prediction], rejected: list[dict]) -> np.ndarray:
    preview = crop.copy()
    for item in accepted:
        p1, p2 = (round(item.x1), round(item.y1)), (round(item.x2), round(item.y2))
        cv2.rectangle(preview, p1, p2, (0, 170, 0), 2)
        cv2.putText(preview, item.label, p1, cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 120, 0), 2, cv2.LINE_AA)
    for record in rejected:
        item = rejected_prediction(record)
        if item is None:
            continue
        p1, p2 = (round(item.x1), round(item.y1)), (round(item.x2), round(item.y2))
        cv2.rectangle(preview, p1, p2, (0, 0, 230), 2)
        cv2.putText(preview, f"?{item.label}", p1, cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 190), 2, cv2.LINE_AA)
    return preview


def prepare_base_dataset(base: Path, output: Path) -> Counter:
    counts = Counter()
    for split in ("train", "val"):
        image_dir = base / "images" / split
        label_dir = base / "labels" / split
        for image_path in sorted(image_dir.glob("*")):
            if not image_path.is_file():
                continue
            stem = f"base_{image_path.stem}"
            target_image = output / "images" / split / f"{stem}{image_path.suffix.lower()}"
            target_label = output / "labels" / split / f"{stem}.txt"
            source_label = label_dir / f"{image_path.stem}.txt"
            if not source_label.exists():
                raise FileNotFoundError(f"Base label missing: {source_label}")
            link_or_copy(image_path, target_image)
            link_or_copy(source_label, target_label)
            counts[f"base_{split}"] += 1
    return counts


def load_source_rows(corpus: Path, split: str) -> list[dict]:
    rows = [row for row in read_jsonl(corpus / "manifest.jsonl") if row.get("split") == split]
    for row in rows:
        row["absolute_path"] = str((corpus / Path(row["path"])).resolve())
    return rows


def validate_models(group_model, v1_model, v2_model):
    if len(model_names_as_tuple(group_model)) != 1:
        raise RuntimeError("Group locator must have exactly one class.")
    for name, model in (("v1", v1_model), ("v2", v2_model)):
        actual = model_names_as_tuple(model)
        if actual != BASE_CLASS_NAMES:
            raise RuntimeError(
                f"{name} class order mismatch. "
                f"expected={BASE_CLASS_NAMES}, actual={actual}"
            )


def final_report(output: Path, work: Path, config: dict, limited: bool) -> dict:
    reason_counts = Counter()
    accepted_counts = Counter()
    group_count = review_count = 0
    audit_path = work / "pseudo_audit.jsonl"
    if audit_path.exists():
        for row in read_jsonl(audit_path):
            group_count += 1
            review_count += int(bool(row.get("review_required")))
            if row.get("included_in_train"):
                accepted_counts.update(item["label"] for item in row.get("accepted", []))
            reason_counts.update(item["reason"] for item in row.get("rejected", []))
    image_counts = {
        split: len(list((output / "images" / split).glob("*")))
        for split in ("train", "val")
    }
    holdout_split = str(config["dataset"]["holdout_split"])
    holdout_count = sum(
        1
        for row in read_jsonl(config["paths"]["corpus"] / "manifest.jsonl")
        if row.get("split") == holdout_split
    )
    report = {
        "format_version": 1,
        "complete": not limited,
        "training_started": False,
        "target_class_count": len(CLASS_NAMES),
        "target_classes": list(CLASS_NAMES),
        "class_id_policy": "legacy 0-9/A-Z/prime IDs 0-36 unchanged; lowercase a-z appended as IDs 37-62",
        "source_corpus": str(config["paths"]["corpus"]),
        "validation_policy": "18 manually labelled real pages only",
        "unlabelled_holdout_policy": (
            f"{holdout_count} unlabelled {holdout_split} pages are untouched qualitative "
            "holdout; not mAP ground truth"
        ),
        "image_counts": image_counts,
        "source_pages_processed": len((work / "processed_pages.txt").read_text(encoding="utf-8").splitlines()) if (work / "processed_pages.txt").exists() else 0,
        "groups_audited": group_count,
        "review_required": review_count,
        "accepted_class_counts": dict(sorted(accepted_counts.items())),
        "rejection_reasons": dict(sorted(reason_counts.items())),
        "audit": str(audit_path),
        "review_folder": str(work / "review"),
    }
    (output / "build_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    args = parse_args()
    config = load_config(args.config)
    paths = config["paths"]
    cfg = config["dataset"]
    output = (args.output or paths["output_dataset"]).resolve()
    work = (args.work or paths["work"]).resolve()
    if args.resume and args.overwrite:
        raise ValueError("Choose either --resume or --overwrite, not both.")
    if args.overwrite:
        for target in (output, work):
            if target.exists():
                shutil.rmtree(target)
    if output.exists() and any(output.iterdir()) and not args.resume:
        raise FileExistsError(f"Output is not empty: {output}. Use --resume or --overwrite.")
    for split in ("train", "val"):
        (output / "images" / split).mkdir(parents=True, exist_ok=True)
        (output / "labels" / split).mkdir(parents=True, exist_ok=True)
    (work / "review").mkdir(parents=True, exist_ok=True)
    write_data_yaml(output)
    base_counts = prepare_base_dataset(paths["base_dataset"], output)

    source_rows = load_source_rows(paths["corpus"], cfg["source_split"])
    holdout_rows = load_source_rows(paths["corpus"], cfg["holdout_split"])
    (output / "unlabelled_holdout_manifest.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in holdout_rows),
        encoding="utf-8",
    )
    if args.max_pages is not None:
        source_rows = source_rows[: max(0, args.max_pages)]
    processed_path = work / "processed_pages.txt"
    processed = set(processed_path.read_text(encoding="utf-8").splitlines()) if processed_path.exists() else set()

    device = args.device if args.device is not None else (0 if torch.cuda.is_available() else "cpu")
    print(f"device={device}; source_pages={len(source_rows)}; already_processed={len(processed)}")
    group_model = YOLO(str(paths["group_model"]))
    v1_model = YOLO(str(paths["teacher_v1"]))
    v2_model = YOLO(str(paths["teacher_v2"]))
    validate_models(group_model, v1_model, v2_model)

    review_written = len(list((work / "review").glob("*.png")))
    for page_number, row in enumerate(source_rows, start=1):
        source = Path(row["absolute_path"])
        page_key = row["filename"]
        if page_key in processed:
            continue
        image = read_image(source)
        if image is None:
            append_jsonl(work / "errors.jsonl", {"page": page_key, "reason": "image_read_failed", "path": str(source)})
            continue
        height, width = image.shape[:2]
        group_result = group_model.predict(
            source=image,
            imgsz=int(cfg["group_imgsz"]),
            conf=float(cfg["group_confidence"]),
            iou=float(cfg["group_iou"]),
            device=device,
            verbose=False,
        )[0]
        # Character teachers must see the full page at the scale used during
        # their own training.  Upscaling a tiny group crop makes a 50 px glyph
        # hundreds of pixels tall and produces detections on individual strokes.
        page_result_v1 = v1_model.predict(
            source=image,
            imgsz=int(cfg["teacher_imgsz"]),
            conf=float(cfg["teacher_confidence_floor"]),
            iou=float(cfg["teacher_iou"]),
            device=device,
            verbose=False,
        )[0]
        page_result_v2 = v2_model.predict(
            source=image,
            imgsz=int(cfg["teacher_imgsz"]),
            conf=float(cfg["teacher_confidence_floor"]),
            iou=float(cfg["teacher_iou"]),
            device=device,
            verbose=False,
        )[0]
        page_predictions_v1 = predictions_from_result(page_result_v1, v1_model, "v1")
        page_predictions_v2 = predictions_from_result(page_result_v2, v2_model, "v2")
        groups = []
        if group_result.boxes is not None:
            groups = sorted(group_result.boxes, key=lambda box: (float(box.xyxy[0][1]), float(box.xyxy[0][0])))
        for group_index, box in enumerate(groups, start=1):
            gx1, gy1, gx2, gy2 = (float(value) for value in box.xyxy[0])
            group_center_y = (gy1 + gy2) / 2 / max(1, height)
            if group_center_y < float(cfg["page_margin_top_ratio"]) or group_center_y > 1.0 - float(cfg["page_margin_bottom_ratio"]):
                continue
            padding = max(int(cfg["crop_padding_min_px"]), round(max(gx2 - gx1, gy2 - gy1) * float(cfg["crop_padding_ratio"])))
            x1, y1 = max(0, int(gx1) - padding), max(0, int(gy1) - padding)
            x2, y2 = min(width, int(np.ceil(gx2)) + padding), min(height, int(np.ceil(gy2)) + padding)
            crop = image[y1:y2, x1:x2]
            if crop.size == 0:
                continue
            predictions_v1 = predictions_in_crop(page_predictions_v1, x1, y1, x2, y2)
            predictions_v2 = predictions_in_crop(page_predictions_v2, x1, y1, x2, y2)
            accepted, rejected, reference_height = merge_teachers(predictions_v1, predictions_v2, cfg)
            stem = f"pseudo_{source.stem}_g{group_index:03d}"
            # A crop with any unresolved teacher candidate may contain a real
            # character missing from its label file.  Such partial annotation
            # would teach YOLO that the omitted character is background, so
            # only completely clean consensus crops enter training by default.
            included_in_train = bool(accepted) and not rejected
            if included_in_train:
                target_image = output / "images" / "train" / f"{stem}.png"
                target_label = output / "labels" / "train" / f"{stem}.txt"
                write_png(target_image, crop)
                target_label.write_text("\n".join(yolo_lines(accepted, crop.shape[1], crop.shape[0])) + "\n", encoding="utf-8")
            review_required = bool(rejected)
            review_path = None
            if review_required and review_written < int(cfg["review_limit"]):
                review_path = work / "review" / f"{stem}.png"
                write_png(review_path, draw_review(crop, accepted, rejected))
                review_written += 1
            append_jsonl(
                work / "pseudo_audit.jsonl",
                {
                    "page": page_key,
                    "page_path": str(source),
                    "crop_stem": stem,
                    "group_confidence": float(box.conf[0]),
                    "group_box": [gx1, gy1, gx2, gy2],
                    "page_size": [width, height],
                    "crop_box": [x1, y1, x2, y2],
                    "reference_height": reference_height,
                    "accepted": [asdict(item) for item in accepted],
                    "rejected": rejected,
                    "included_in_train": included_in_train,
                    "review_required": review_required,
                    "review_path": str(review_path) if review_path else None,
                },
            )
        with processed_path.open("a", encoding="utf-8") as stream:
            stream.write(page_key + "\n")
        processed.add(page_key)
        if page_number % 25 == 0 or page_number == len(source_rows):
            print(f"processed {page_number}/{len(source_rows)} pages")

    report = final_report(output, work, config, limited=args.max_pages is not None)
    report["base_counts"] = dict(base_counts)
    (output / "build_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
