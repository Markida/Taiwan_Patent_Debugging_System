"""Compare v1, v2 and v3 on the unchanged manually-labelled validation set."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import torch

try:
    from .common import DEFAULT_CONFIG, load_config
except ImportError:
    from common import DEFAULT_CONFIG, load_config

from ultralytics import YOLO


def parse_args():
    parser = argparse.ArgumentParser(description="Compare character models on real validation labels.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--models", nargs="+", type=Path, default=None)
    parser.add_argument("--imgsz", type=int, default=None)
    parser.add_argument("--batch", type=float, default=None)
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--device", default=None)
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalized_batch(value):
    number_value = float(value)
    return int(number_value) if number_value >= 1 and number_value.is_integer() else number_value


def evaluate(path: Path, data: Path, args, project: Path) -> dict:
    path = path.resolve()
    if not path.exists():
        raise FileNotFoundError(path)
    model = YOLO(str(path))
    metrics = model.val(
        data=str(data),
        split="val",
        imgsz=args.imgsz,
        batch=args.batch,
        workers=args.workers,
        device=args.device,
        project=str(project),
        name=f"compare_{path.stem}",
        exist_ok=True,
        plots=True,
    )
    per_class = {}
    for position, class_id in enumerate(int(value) for value in metrics.box.ap_class_index):
        per_class[str(model.names[class_id])] = {
            "precision": number(metrics.box.p[position]),
            "recall": number(metrics.box.r[position]),
            "mAP50": number(metrics.box.ap50[position]),
            "mAP50-95": number(metrics.box.ap[position]),
        }
    return {
        "path": str(path),
        "sha256": sha256(path),
        "overall": {str(key): number(value) for key, value in metrics.results_dict.items()},
        "per_class_present_in_validation": per_class,
        "output": str(metrics.save_dir),
    }


def main():
    args = parse_args()
    config = load_config(args.config)
    paths, training = config["paths"], config["training"]
    args.imgsz = args.imgsz or int(training["imgsz"])
    args.batch = normalized_batch(args.batch if args.batch is not None else training["batch"])
    args.workers = args.workers if args.workers is not None else int(training["workers"])
    args.device = args.device if args.device is not None else (0 if torch.cuda.is_available() else "cpu")
    models = args.models or [paths["teacher_v1"], paths["teacher_v2"], paths["export_model"]]
    data = paths["output_dataset"] / "data.yaml"
    if not data.exists():
        # Before a full v3 build, the unchanged v2 data YAML is equivalent for
        # the manual validation split and lets the evaluator itself be tested.
        data = paths["base_dataset"] / "data.yaml"
    results = [evaluate(path, data, args, paths["runs"] / "evaluation") for path in models]
    baseline = results[1]["overall"] if len(results) > 1 else {}
    report = {
        "validation_policy": "real manually-labelled validation pages only; the 500 raw TIPO val pages have no boxes",
        "data": str(data),
        "models": results,
    }
    if len(results) >= 3:
        report["v3_minus_v2"] = {
            key: results[2]["overall"][key] - baseline[key]
            for key in results[2]["overall"].keys() & baseline.keys()
            if results[2]["overall"][key] is not None and baseline[key] is not None
        }
    output = (args.output or paths["export_model"].with_name("patent_char_v3_comparison.json")).resolve()
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
