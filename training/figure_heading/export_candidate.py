"""Export a trained figure-heading PT candidate to audited CPU ONNX artifacts."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from features.patent_ocr.figure_heading_classes import FIGURE_HEADING_CLASS_NAMES


FORMAL_MODELS_DIR = PROJECT_ROOT / "models"
DEFAULT_IMGSZ = 1536
DEFAULT_OPSET = 17
DLL_HANDLES = []


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--imgsz", type=int, default=DEFAULT_IMGSZ)
    return parser.parse_args()


def configure_runtime_paths():
    """Expose Conda DLL locations before importing Torch or Ultralytics."""

    runtime_root = Path(sys.executable).resolve().parent
    candidates = [
        runtime_root,
        runtime_root / "Library" / "bin",
        runtime_root / "Scripts",
        runtime_root / "bin",
    ]
    existing = [str(path) for path in candidates if path.exists()]
    if existing:
        os.environ["PATH"] = os.pathsep.join(
            existing + [os.environ.get("PATH", "")]
        )
    handles = []
    if hasattr(os, "add_dll_directory"):
        for path in candidates:
            if path.exists():
                handles.append(os.add_dll_directory(str(path)))
    return handles


def _is_within(path, directory):
    try:
        Path(path).resolve().relative_to(Path(directory).resolve())
        return True
    except ValueError:
        return False


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_candidate_paths(model_path, *, formal_models_dir=FORMAL_MODELS_DIR):
    """Resolve all artifacts and fail before an export could overwrite anything."""

    model_path = Path(model_path).resolve()
    if model_path.suffix.lower() != ".pt":
        raise ValueError("候選模型必須是 .pt 檔案。")
    if not model_path.is_file():
        raise FileNotFoundError(f"找不到候選 PT 模型：{model_path}")

    onnx_path = model_path.with_suffix(".onnx")
    names_path = model_path.with_suffix(".names.json")
    metrics_path = model_path.with_name(f"{model_path.stem}_metrics.json")
    if any(
        _is_within(path, formal_models_dir)
        for path in (onnx_path, names_path, metrics_path)
    ):
        raise RuntimeError("候選匯出目標不得位於 models 正式模型目錄。")
    if onnx_path.exists():
        raise FileExistsError(f"ONNX 已存在，為避免覆寫不會繼續：{onnx_path}")
    if names_path.exists():
        raise FileExistsError(f"類別檔已存在，為避免覆寫不會繼續：{names_path}")
    if not metrics_path.is_file():
        raise FileNotFoundError(
            f"找不到既有訓練報告，無法保留訓練資料與 metrics：{metrics_path}"
        )
    return {
        "model": model_path,
        "onnx": onnx_path,
        "names": names_path,
        "metrics": metrics_path,
    }


def _read_metrics(path):
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"訓練報告不是有效 JSON：{path}") from error
    if not isinstance(payload, dict):
        raise ValueError(f"訓練報告必須是 JSON object：{path}")
    return payload


def _package_version(name):
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def export_environment():
    return {
        "python": platform.python_version(),
        "python_executable": str(Path(sys.executable).resolve()),
        "platform": platform.platform(),
        "torch": _package_version("torch"),
        "ultralytics": _package_version("ultralytics"),
        "onnx": _package_version("onnx"),
        "device": "cpu",
    }


def build_export_metadata(
    original_metrics,
    *,
    model_path,
    onnx_path,
    names_path,
    imgsz,
    environment,
    original_metrics_sha256=None,
):
    """Preserve the training report while replacing only export outcome fields."""

    payload = dict(original_metrics)
    payload.pop("onnx_export_error", None)
    payload.update(
        {
            "onnx_model": str(Path(onnx_path).resolve()),
            "onnx_names": str(Path(names_path).resolve()),
            "candidate_pt_sha256": sha256_file(model_path),
            "onnx_sha256": sha256_file(onnx_path),
            "onnx_names_sha256": sha256_file(names_path),
            "onnx_export": {
                "tool": "training.figure_heading.export_candidate",
                "exported_at_utc": datetime.now(timezone.utc).isoformat(),
                "format": "onnx",
                "imgsz": int(imgsz),
                "opset": DEFAULT_OPSET,
                "simplify": False,
                "dynamic": False,
                "environment": dict(environment),
            },
        }
    )
    if original_metrics_sha256:
        payload["pre_export_metrics_sha256"] = str(original_metrics_sha256)
    return payload


def _write_json_atomic(path, payload):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _load_yolo_model(model_path):
    # Keep all heavy native imports after the Windows DLL search path is ready.
    global DLL_HANDLES
    DLL_HANDLES = configure_runtime_paths()
    os.environ.setdefault("YOLO_AUTOINSTALL", "false")
    from ultralytics import YOLO

    return YOLO(str(model_path))


def _validate_model_classes(model):
    names = getattr(model, "names", {})
    if isinstance(names, dict):
        try:
            ordered = [str(names[index]) for index in range(len(names))]
        except (KeyError, TypeError):
            ordered = []
    else:
        ordered = [str(value) for value in names]
    if ordered != list(FIGURE_HEADING_CLASS_NAMES):
        raise RuntimeError(
            "候選模型類別必須依序為：" + "、".join(FIGURE_HEADING_CLASS_NAMES)
        )


def export_candidate(model_path, *, imgsz=DEFAULT_IMGSZ):
    if int(imgsz) <= 0:
        raise ValueError("--imgsz 必須是正整數。")
    paths = validate_candidate_paths(model_path)
    original_metrics_digest = sha256_file(paths["metrics"])
    original_metrics = _read_metrics(paths["metrics"])

    model = _load_yolo_model(paths["model"])
    _validate_model_classes(model)
    exported_path = Path(
        model.export(
            format="onnx",
            imgsz=int(imgsz),
            opset=DEFAULT_OPSET,
            simplify=False,
            dynamic=False,
            device="cpu",
        )
    ).resolve()
    if not exported_path.is_file() or exported_path.stat().st_size <= 0:
        raise RuntimeError(f"Ultralytics 未產生有效 ONNX：{exported_path}")
    if exported_path != paths["onnx"]:
        if paths["onnx"].exists():
            raise FileExistsError(
                f"ONNX 已存在，為避免覆寫不會繼續：{paths['onnx']}"
            )
        shutil.copy2(exported_path, paths["onnx"])

    with paths["names"].open("x", encoding="utf-8") as handle:
        json.dump(
            {"names": list(FIGURE_HEADING_CLASS_NAMES)},
            handle,
            ensure_ascii=False,
            indent=2,
        )
        handle.write("\n")

    updated_metrics = build_export_metadata(
        original_metrics,
        model_path=paths["model"],
        onnx_path=paths["onnx"],
        names_path=paths["names"],
        imgsz=imgsz,
        environment=export_environment(),
        original_metrics_sha256=original_metrics_digest,
    )
    _write_json_atomic(paths["metrics"], updated_metrics)
    return updated_metrics


def main():
    args = parse_args()
    report = export_candidate(args.model, imgsz=args.imgsz)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
