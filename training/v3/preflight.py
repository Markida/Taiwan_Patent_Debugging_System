"""Validate the v3 training environment without starting training."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
from collections import Counter
from pathlib import Path

import cv2
import numpy as np
import torch

try:
    from .build_pseudo_dataset import merge_teachers, predictions_from_result, predictions_in_crop
    from .common import (
        BASE_CLASS_NAMES,
        CLASS_NAMES,
        DEFAULT_CONFIG,
        load_config,
        model_names_as_tuple,
        read_image,
        read_jsonl,
        write_png,
    )
except ImportError:
    from build_pseudo_dataset import merge_teachers, predictions_from_result, predictions_in_crop
    from common import (
        BASE_CLASS_NAMES,
        CLASS_NAMES,
        DEFAULT_CONFIG,
        load_config,
        model_names_as_tuple,
        read_image,
        read_jsonl,
        write_png,
    )

import ultralytics
from ultralytics import YOLO


def parse_args():
    parser = argparse.ArgumentParser(description="Check everything required for v3 training.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--smoke", action="store_true", help="Run three models on one real page; no training.")
    parser.add_argument("--max-smoke-crops", type=int, default=3)
    parser.add_argument("--device", default=None)
    parser.add_argument("--verify-hashes", action="store_true", help="Re-hash all 3909 corpus images (slow).")
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def count_dataset(dataset: Path) -> dict:
    result = {}
    for split in ("train", "val"):
        images = [path for path in (dataset / "images" / split).glob("*") if path.is_file()]
        labels = [path for path in (dataset / "labels" / split).glob("*.txt") if path.is_file()]
        classes = Counter()
        invalid = []
        for label_path in labels:
            for number, line in enumerate(label_path.read_text(encoding="utf-8").splitlines(), start=1):
                if not line.strip():
                    continue
                fields = line.split()
                try:
                    class_id = int(fields[0])
                    values = [float(value) for value in fields[1:]]
                    if len(fields) != 5 or class_id not in range(len(CLASS_NAMES)) or any(value < 0 or value > 1 for value in values):
                        raise ValueError
                    classes[CLASS_NAMES[class_id]] += 1
                except (ValueError, IndexError):
                    invalid.append(f"{label_path.name}:{number}")
        result[split] = {
            "images": len(images),
            "labels": len(labels),
            "invalid_label_rows": invalid[:20],
            "class_counts": dict(classes),
        }
    return result


def corpus_status(corpus: Path, verify_hashes: bool) -> dict:
    rows = list(read_jsonl(corpus / "manifest.jsonl"))
    split_counts = Counter(row["split"] for row in rows)
    publications = {
        split: {row["publication_number"] for row in rows if row["split"] == split}
        for split in ("train", "val")
    }
    missing = []
    bad_hash = []
    for row in rows:
        path = corpus / Path(row["path"])
        if not path.exists():
            missing.append(str(path))
        elif verify_hashes and sha256(path) != row["file_sha256"]:
            bad_hash.append(str(path))
    return {
        "manifest_rows": len(rows),
        "split_counts": dict(split_counts),
        "train_publications": len(publications["train"]),
        "val_publications": len(publications["val"]),
        "publication_overlap": sorted(publications["train"] & publications["val"]),
        "missing_images": missing[:20],
        "hashes_checked": verify_hashes,
        "bad_hashes": bad_hash[:20],
    }


def model_status(path: Path, expected_names: tuple[str, ...] | None) -> dict:
    model = YOLO(str(path))
    names = model_names_as_tuple(model)
    return {
        "path": str(path),
        "exists": path.exists(),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
        "classes": list(names),
        "class_order_valid": expected_names is None or names == expected_names,
    }


def smoke_test(config: dict, device, max_crops: int) -> dict:
    paths, cfg = config["paths"], config["dataset"]
    output = Path(__file__).resolve().parent / "smoke_output"
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    row = next(row for row in read_jsonl(paths["corpus"] / "manifest.jsonl") if row["split"] == cfg["source_split"])
    source = paths["corpus"] / Path(row["path"])
    image = read_image(source)
    if image is None:
        raise RuntimeError(f"Smoke image read failed: {source}")
    group_model = YOLO(str(paths["group_model"]))
    v1_model = YOLO(str(paths["teacher_v1"]))
    v2_model = YOLO(str(paths["teacher_v2"]))
    group_result = group_model.predict(source=image, imgsz=int(cfg["group_imgsz"]), conf=float(cfg["group_confidence"]), iou=float(cfg["group_iou"]), device=device, verbose=False)[0]
    page_v1_result = v1_model.predict(source=image, imgsz=int(cfg["teacher_imgsz"]), conf=float(cfg["teacher_confidence_floor"]), iou=float(cfg["teacher_iou"]), device=device, verbose=False)[0]
    page_v2_result = v2_model.predict(source=image, imgsz=int(cfg["teacher_imgsz"]), conf=float(cfg["teacher_confidence_floor"]), iou=float(cfg["teacher_iou"]), device=device, verbose=False)[0]
    page_v1 = predictions_from_result(page_v1_result, v1_model, "v1")
    page_v2 = predictions_from_result(page_v2_result, v2_model, "v2")
    boxes = sorted(
        list(group_result.boxes) if group_result.boxes is not None else [],
        key=lambda box: (float(box.xyxy[0][1]), float(box.xyxy[0][0])),
    )
    boxes = [
        box
        for box in boxes
        if float(cfg["page_margin_top_ratio"])
        <= (float(box.xyxy[0][1]) + float(box.xyxy[0][3])) / 2 / max(1, image.shape[0])
        <= 1.0 - float(cfg["page_margin_bottom_ratio"])
    ]
    rows = []
    page_preview = image.copy()
    for index, box in enumerate(boxes[:max(0, max_crops)], start=1):
        gx1, gy1, gx2, gy2 = (float(value) for value in box.xyxy[0])
        pad = max(int(cfg["crop_padding_min_px"]), round(max(gx2 - gx1, gy2 - gy1) * float(cfg["crop_padding_ratio"])))
        x1, y1 = max(0, int(gx1) - pad), max(0, int(gy1) - pad)
        x2, y2 = min(image.shape[1], int(np.ceil(gx2)) + pad), min(image.shape[0], int(np.ceil(gy2)) + pad)
        crop = image[y1:y2, x1:x2]
        p1 = predictions_in_crop(page_v1, x1, y1, x2, y2)
        p2 = predictions_in_crop(page_v2, x1, y1, x2, y2)
        accepted, rejected, reference_height = merge_teachers(p1, p2, cfg)
        crop_preview = crop.copy()
        for prediction in accepted:
            cv2.rectangle(crop_preview, (round(prediction.x1), round(prediction.y1)), (round(prediction.x2), round(prediction.y2)), (0, 180, 0), 2)
            cv2.putText(crop_preview, prediction.label, (round(prediction.x1), max(12, round(prediction.y1))), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 130, 0), 2, cv2.LINE_AA)
        write_png(output / f"crop_{index:02d}.png", crop_preview)
        cv2.rectangle(page_preview, (x1, y1), (x2, y2), (0, 150, 0), 4)
        rows.append({
            "crop": index,
            "group_confidence": float(box.conf[0]),
            "v1_predictions": len(p1),
            "v2_predictions": len(p2),
            "accepted": [prediction.label for prediction in accepted],
            "rejected": [record["reason"] for record in rejected],
            "reference_height": reference_height,
        })
    write_png(output / "page_groups.png", page_preview)
    result = {"source": str(source), "group_detections": len(boxes), "crops_tested": len(rows), "results": rows, "output": str(output)}
    (output / "smoke_report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main():
    args = parse_args()
    config = load_config(args.config)
    paths = config["paths"]
    required = [paths[key] for key in ("corpus", "base_dataset", "group_model", "teacher_v1", "teacher_v2")]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError("Missing required paths:\n" + "\n".join(missing))
    device = args.device if args.device is not None else (0 if torch.cuda.is_available() else "cpu")
    disk = shutil.disk_usage(paths["output_dataset"].parent)
    report = {
        "ready": False,
        "training_started": False,
        "python": platform.python_version(),
        "torch": torch.__version__,
        "torch_cuda_runtime": torch.version.cuda,
        "ultralytics": ultralytics.__version__,
        "cuda_available": torch.cuda.is_available(),
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "gpu_capability": list(torch.cuda.get_device_capability(0)) if torch.cuda.is_available() else None,
        "device_selected": str(device),
        "disk_free_gib": round(disk.free / 1024**3, 2),
        "corpus": corpus_status(paths["corpus"], args.verify_hashes),
        "base_dataset": count_dataset(paths["base_dataset"]),
        "models": {
            "group": model_status(paths["group_model"], None),
            "v1": model_status(paths["teacher_v1"], BASE_CLASS_NAMES),
            "v2": model_status(paths["teacher_v2"], BASE_CLASS_NAMES),
            "optional_yolov8s": model_status(config["training"]["optional_larger_model"], None),
        },
    }
    expected_split_counts = config["dataset"].get(
        "expected_split_counts", {"train": 3409, "val": 500}
    )
    report["expected_split_counts"] = expected_split_counts
    report["ready"] = bool(
        report["cuda_available"]
        and report["corpus"]["split_counts"] == expected_split_counts
        and not report["corpus"]["missing_images"]
        and not report["corpus"]["publication_overlap"]
        and report["base_dataset"]["train"]["images"] == report["base_dataset"]["train"]["labels"]
        and report["base_dataset"]["val"]["images"] == report["base_dataset"]["val"]["labels"] == 18
        and not report["base_dataset"]["train"]["invalid_label_rows"]
        and not report["base_dataset"]["val"]["invalid_label_rows"]
        and all(model["class_order_valid"] for model in report["models"].values())
        and report["disk_free_gib"] >= 8
    )
    if args.smoke:
        report["smoke"] = smoke_test(config, device, args.max_smoke_crops)
        report["ready"] = report["ready"] and report["smoke"]["crops_tested"] > 0
    output = (args.output or Path(__file__).resolve().parent / "preflight_report.json").resolve()
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["ready"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
