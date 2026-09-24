"""Compare character models on the same real-only manual validation split."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from train_domain_matched import CONDA_DLL_HANDLES  # noqa: F401
from ultralytics import YOLO


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[1]
DEFAULT_DATA = SCRIPT_DIR / "dataset_v2_domain_matched" / "data.yaml"
DEFAULT_BUILD_REPORT = SCRIPT_DIR / "dataset_v2_domain_matched" / "build_report.json"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Evaluate old and domain-matched character models on real images."
    )
    parser.add_argument(
        "--models",
        nargs="+",
        type=Path,
        default=[
            PROJECT_ROOT / "models" / "patent_char_v1.pt",
            PROJECT_ROOT / "models" / "patent_char_v2_domain.pt",
        ],
    )
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--imgsz", type=int, default=1536)
    parser.add_argument("--batch", type=int, default=4)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--device", default="0")
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "models" / "patent_char_v2_domain_comparison.json",
    )
    return parser.parse_args()


def sha256(path: Path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def as_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def evaluate(model_path: Path, args):
    model_path = model_path.resolve()
    if not model_path.exists():
        raise FileNotFoundError(model_path)

    model = YOLO(str(model_path))
    metrics = model.val(
        data=str(args.data.resolve()),
        split="val",
        imgsz=args.imgsz,
        batch=args.batch,
        device=args.device,
        workers=args.workers,
        plots=True,
        project=str((PROJECT_ROOT / "runs").resolve()),
        name=f"real_val_{model_path.stem}",
        exist_ok=True,
    )

    overall = {
        str(key): as_float(value)
        for key, value in getattr(metrics, "results_dict", {}).items()
    }
    class_ids = [int(value) for value in metrics.box.ap_class_index]
    per_class = {}
    for position, class_id in enumerate(class_ids):
        class_name = str(model.names[class_id])
        per_class[class_name] = {
            "precision": as_float(metrics.box.p[position]),
            "recall": as_float(metrics.box.r[position]),
            "mAP50": as_float(metrics.box.ap50[position]),
            "mAP50-95": as_float(metrics.box.ap[position]),
        }

    return {
        "path": str(model_path),
        "bytes": model_path.stat().st_size,
        "sha256": sha256(model_path),
        "overall": overall,
        "per_class_present_in_validation": per_class,
        "validation_output": str(Path(metrics.save_dir).resolve()),
    }


def main():
    args = parse_args()
    build_report = json.loads(DEFAULT_BUILD_REPORT.read_text(encoding="utf-8"))
    validation_counts = build_report["class_counts"]["val"]
    all_classes = list(build_report["class_counts"]["train"])
    absent_classes = [name for name in all_classes if validation_counts.get(name, 0) == 0]

    report = {
        "validation_policy": build_report["validation_policy"],
        "validation_image_count": sum(build_report["source_counts"]["val"].values()),
        "validation_class_counts": validation_counts,
        "classes_absent_from_validation": absent_classes,
        "imgsz": args.imgsz,
        "models": [evaluate(path, args) for path in args.models],
    }

    if len(report["models"]) >= 2:
        old = report["models"][0]["overall"]
        new = report["models"][1]["overall"]
        report["new_minus_old"] = {
            key: new[key] - old[key]
            for key in new.keys() & old.keys()
            if new[key] is not None and old[key] is not None
        }

    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
