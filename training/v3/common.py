from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import cv2
import numpy as np
import yaml


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[1]
DEFAULT_CONFIG = SCRIPT_DIR / "config.yaml"
BASE_CLASS_NAMES = tuple("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ") + ("prime",)
CLASS_NAMES = BASE_CLASS_NAMES + tuple("abcdefghijklmnopqrstuvwxyz")
CLASS_TO_ID = {name: index for index, name in enumerate(CLASS_NAMES)}


def configure_local_runtime_dirs() -> None:
    for name, relative in (
        ("YOLO_CONFIG_DIR", ".ultralytics_train_v3"),
        ("MPLCONFIGDIR", ".matplotlib_train_v3"),
    ):
        path = PROJECT_ROOT / relative
        path.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault(name, str(path))
    os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")


def load_config(path: Path = DEFAULT_CONFIG) -> dict:
    path = path.resolve()
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["_config_path"] = str(path)
    for key, value in list(config.get("paths", {}).items()):
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = PROJECT_ROOT / candidate
        config["paths"][key] = candidate.resolve()
    for key in ("model", "optional_larger_model"):
        if key not in config["training"]:
            continue
        model = Path(config["training"][key])
        if not model.is_absolute():
            model = PROJECT_ROOT / model
        config["training"][key] = model.resolve()
    return config


def read_image(path: Path) -> np.ndarray | None:
    try:
        data = np.fromfile(str(path), dtype=np.uint8)
        return cv2.imdecode(data, cv2.IMREAD_COLOR)
    except (OSError, ValueError, cv2.error):
        return None


def write_png(path: Path, image: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise OSError(f"PNG encoding failed: {path}")
    encoded.tofile(str(path))


def link_or_copy(source: Path, target: Path) -> str:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        return "existing"
    try:
        os.link(source, target)
        return "hardlink"
    except OSError:
        shutil.copy2(source, target)
        return "copy"


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def read_jsonl(path: Path):
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def write_data_yaml(dataset_root: Path) -> Path:
    lines = [
        f'path: "{dataset_root.resolve().as_posix()}"',
        "train: images/train",
        "val: images/val",
        "names:",
    ]
    lines.extend(f'  {index}: "{name}"' for index, name in enumerate(CLASS_NAMES))
    target = dataset_root / "data.yaml"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (dataset_root / "class_map.json").write_text(
        json.dumps({str(i): name for i, name in enumerate(CLASS_NAMES)}, indent=2) + "\n",
        encoding="utf-8",
    )
    return target


def model_names_as_tuple(model) -> tuple[str, ...]:
    names = model.names
    if isinstance(names, dict):
        return tuple(str(names[index]) for index in sorted(names))
    return tuple(str(value) for value in names)


configure_local_runtime_dirs()
