"""Train, validate, checkpoint and export the v3 character detector."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import torch

try:
    from .common import BASE_CLASS_NAMES, CLASS_NAMES, DEFAULT_CONFIG, load_config, model_names_as_tuple
except ImportError:
    from common import BASE_CLASS_NAMES, CLASS_NAMES, DEFAULT_CONFIG, load_config, model_names_as_tuple

from ultralytics import YOLO


def parse_args():
    parser = argparse.ArgumentParser(description="Train patent character model v3.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--start", action="store_true", help="Required safety switch for a new formal training run.")
    parser.add_argument("--resume", type=Path, default=None, help="Resume from an Ultralytics last.pt checkpoint.")
    parser.add_argument("--model", type=Path, default=None, help="Override initial weights (advanced experiment).")
    parser.add_argument("--allow-head-reset", action="store_true", help="Allow weights whose detection head will be reset to the 63-class vocabulary.")
    parser.add_argument("--export-model", type=Path, default=None)
    parser.add_argument("--allow-partial-review", action="store_true", help="Advanced override: train before all selected manual crops are reviewed.")
    parser.add_argument("--epochs", type=int, default=None)
    parser.add_argument("--batch", type=float, default=None)
    parser.add_argument("--imgsz", type=int, default=None)
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--device", default=None)
    parser.add_argument("--name", default="patent_char_v3_consensus")
    parser.add_argument("--export-onnx", action=argparse.BooleanOptionalAction, default=True)
    return parser.parse_args()


def serializable_metrics(metrics) -> dict:
    output = {}
    for key, value in getattr(metrics, "results_dict", {}).items():
        try:
            output[str(key)] = float(value)
        except (TypeError, ValueError):
            output[str(key)] = str(value)
    return output


def normalized_batch(value):
    number = float(value)
    return int(number) if number >= 1 and number.is_integer() else number


def cap_resume_checkpoint_epochs(checkpoint: Path, total_epochs: int) -> tuple[int, int]:
    """Atomically lower a trusted local checkpoint's total epoch target."""
    try:
        state = torch.load(checkpoint, map_location="cpu", weights_only=False)
    except TypeError:  # Compatibility with older torch releases.
        state = torch.load(checkpoint, map_location="cpu")
    completed_epochs = int(state.get("epoch", -1)) + 1
    if total_epochs <= completed_epochs:
        raise RuntimeError(
            f"Requested {total_epochs} total epochs, but the checkpoint already "
            f"completed {completed_epochs}."
        )
    train_args = state.get("train_args")
    if not isinstance(train_args, dict):
        raise RuntimeError("Resume checkpoint is missing train_args metadata.")
    previous_epochs = int(train_args.get("epochs", total_epochs))
    if previous_epochs == total_epochs:
        return completed_epochs, previous_epochs

    train_args["epochs"] = int(total_epochs)
    temporary = checkpoint.with_name(f".{checkpoint.name}.epoch-cap.tmp")
    try:
        torch.save(state, temporary)
        try:
            verified = torch.load(temporary, map_location="cpu", weights_only=False)
        except TypeError:
            verified = torch.load(temporary, map_location="cpu")
        if int(verified.get("train_args", {}).get("epochs", -1)) != total_epochs:
            raise RuntimeError("Failed to verify the capped resume checkpoint.")
        temporary.replace(checkpoint)
    finally:
        temporary.unlink(missing_ok=True)
    return completed_epochs, previous_epochs


def validate_dataset(
    dataset: Path,
    allow_partial_review: bool = False,
    review_root: Path | None = None,
) -> dict:
    report_path = dataset / "build_report.json"
    if not report_path.exists():
        raise FileNotFoundError(f"Dataset build report missing: {report_path}")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if not report.get("complete"):
        raise RuntimeError("The dataset is a limited/smoke build, not a complete formal dataset.")
    train_images = list((dataset / "images" / "train").glob("*"))
    train_labels = list((dataset / "labels" / "train").glob("*.txt"))
    val_images = list((dataset / "images" / "val").glob("*"))
    val_labels = list((dataset / "labels" / "val").glob("*.txt"))
    if len(train_images) != len(train_labels) or len(val_images) != len(val_labels):
        raise RuntimeError("Image/label counts do not match.")
    if len(val_images) != 18:
        raise RuntimeError(f"Expected 18 manually-labelled validation pages, found {len(val_images)}.")
    if len(train_images) <= 667:
        raise RuntimeError("No clean pseudo-labelled crops were added; refusing a base-only v3 run.")
    review_selection = (
        (review_root or Path(__file__).resolve().parent / "manual_review")
        / "selection_report.json"
    )
    if review_selection.exists():
        import_report_path = dataset / "manual_review_import.json"
        if not import_report_path.exists():
            raise RuntimeError("Targeted manual crops were selected but not imported. Run Annotate_V3_Manual.bat and Import_V3_Manual.bat first.")
        import_report = json.loads(import_report_path.read_text(encoding="utf-8"))
        if int(import_report.get("reviewed", 0)) == 0:
            raise RuntimeError("No targeted manual reviews were completed/imported.")
        if int(import_report.get("remaining", 0)) > 0 and not allow_partial_review:
            raise RuntimeError(
                f"Manual review is incomplete: {import_report['remaining']} crops remain. "
                "Finish and re-import them before formal training."
            )
    return report


def main():
    args = parse_args()
    config = load_config(args.config)
    paths, cfg = config["paths"], config["training"]
    dataset = paths["output_dataset"]
    data_yaml = dataset / "data.yaml"
    plan = {
        "dataset": str(dataset),
        "base_model": str(args.model or cfg["model"]),
        "resume": str(args.resume.resolve()) if args.resume else None,
        "epochs": args.epochs or int(cfg["epochs"]),
        "imgsz": args.imgsz or int(cfg["imgsz"]),
        "batch": normalized_batch(args.batch if args.batch is not None else cfg["batch"]),
        "workers": args.workers if args.workers is not None else int(cfg["workers"]),
        "rotation_augmentation": False,
        "target_classes": len(CLASS_NAMES),
        "lowercase_classes": True,
        "validation": "18 manually-labelled real pages",
    }
    if not args.start and args.resume is None:
        print(json.dumps({"training_started": False, "reason": "Add --start after reviewing this plan.", "plan": plan}, ensure_ascii=False, indent=2))
        return
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable. Formal v3 training is intentionally blocked on CPU.")
    build_report = validate_dataset(
        dataset,
        allow_partial_review=args.allow_partial_review,
        review_root=paths.get("manual_review"),
    )
    if not data_yaml.exists():
        raise FileNotFoundError(data_yaml)
    device = args.device if args.device is not None else 0
    project = paths["runs"]
    project.mkdir(parents=True, exist_ok=True)

    if args.resume:
        checkpoint = args.resume.resolve()
        if not checkpoint.exists() or checkpoint.name.lower() != "last.pt":
            raise FileNotFoundError(f"Valid last.pt checkpoint required: {checkpoint}")
        completed_epochs, previous_epochs = cap_resume_checkpoint_epochs(
            checkpoint,
            plan["epochs"],
        )
        model = YOLO(str(checkpoint))
        print(f"Resuming checkpoint: {checkpoint}")
        print(
            f"Epoch target: {previous_epochs} -> {plan['epochs']} total; "
            f"already completed: {completed_epochs}"
        )
        train_results = model.train(
            resume=True,
            device=device,
            patience=int(cfg["patience"]),
            save_period=int(cfg["save_period"]),
            workers=plan["workers"],
        )
    else:
        model_path = (args.model or cfg["model"]).resolve()
        if not model_path.exists():
            raise FileNotFoundError(model_path)
        model = YOLO(str(model_path))
        initial_names = model_names_as_tuple(model)
        if initial_names not in {CLASS_NAMES, BASE_CLASS_NAMES} and not args.allow_head_reset:
            raise RuntimeError(
                "Initial model classes are neither the 37-class base vocabulary "
                "nor the 63-class v3 vocabulary. Use --allow-head-reset only "
                "for an intentional pretrained architecture experiment."
            )
        train_results = model.train(
            data=str(data_yaml),
            epochs=plan["epochs"],
            imgsz=plan["imgsz"],
            batch=plan["batch"],
            device=device,
            workers=plan["workers"],
            project=str(project),
            name=args.name,
            patience=int(cfg["patience"]),
            optimizer=str(cfg["optimizer"]),
            lr0=float(cfg["lr0"]),
            weight_decay=float(cfg["weight_decay"]),
            cos_lr=True,
            warmup_epochs=3.0,
            degrees=0.0,
            translate=0.02,
            scale=0.10,
            shear=0.0,
            perspective=0.0,
            flipud=0.0,
            fliplr=0.0,
            mosaic=0.05,
            mixup=0.0,
            close_mosaic=int(cfg["close_mosaic"]),
            hsv_h=0.003,
            hsv_s=0.03,
            hsv_v=0.06,
            cache=False,
            amp=True,
            seed=int(config["seed"]),
            deterministic=True,
            save=True,
            save_period=int(cfg["save_period"]),
            plots=True,
        )

    trainer = model.trainer
    best = Path(trainer.best).resolve()
    last = Path(trainer.last).resolve()
    if not best.exists():
        raise FileNotFoundError(f"Training ended without best.pt: {best}")
    export_model = (args.export_model or paths["export_model"]).resolve()
    export_model.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(best, export_model)

    validation_model = YOLO(str(export_model))
    metrics = validation_model.val(
        data=str(data_yaml),
        split="val",
        imgsz=plan["imgsz"],
        batch=plan["batch"],
        device=device,
        workers=plan["workers"],
        project=str(project),
        name=f"{args.name}_manual_val",
        plots=True,
    )
    report = {
        "training_started": True,
        "training_complete": True,
        "model": str(export_model),
        "best_checkpoint": str(best),
        "resume_checkpoint": str(last),
        "run_directory": str(trainer.save_dir),
        "plan": plan,
        "dataset_report": build_report,
        "metrics": serializable_metrics(metrics),
        "train_results_type": type(train_results).__name__,
    }
    report_path = export_model.with_name(f"{export_model.stem}_metrics.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if args.export_onnx:
        try:
            exported = Path(validation_model.export(format="onnx", imgsz=plan["imgsz"], opset=17, simplify=False, dynamic=False, device="cpu"))
            target = export_model.with_suffix(".onnx")
            if exported.resolve() != target.resolve():
                shutil.copy2(exported, target)
            report["onnx_model"] = str(target)
        except Exception as exc:  # A valid PT model must survive optional export failure.
            report["onnx_export_error"] = f"{type(exc).__name__}: {exc}"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
