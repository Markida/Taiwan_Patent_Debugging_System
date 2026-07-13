"""用固定 holdout 圖驗證字元模型與應用程式的組合邏輯。"""

import argparse
import json
import random
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[1]
WORKSPACE_ROOT = PROJECT_ROOT.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from train_char_yolo import CONDA_DLL_HANDLES  # noqa: E402, F401
from ultralytics import YOLO  # noqa: E402

from features.patent_ocr.class_map import load_class_map  # noqa: E402
from features.patent_ocr.ocr_engine import (  # noqa: E402
    get_compute_device,
    recognize_one_image,
    should_use_yolo_class_as_char,
)
from prepare_dataset import (  # noqa: E402
    build_group_glyphs,
    draw_patent_style_background,
)
from PIL import Image, ImageDraw  # noqa: E402


EXPECTED_LABELS = ("A", "B", "10A", "7'", "8'", "9'")


def parse_args():
    parser = argparse.ArgumentParser(
        description="驗證 A/B/10A/7'/8'/9' 的偵測與組合結果。"
    )
    parser.add_argument(
        "--model",
        type=Path,
        default=PROJECT_ROOT / "models" / "patent_char_v1.pt",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "outputs" / "char_model_verification",
    )
    return parser.parse_args()


def create_holdout_image(output_path):
    rng = random.Random(2026071302)
    image = Image.new("RGB", (1280, 1280), "white")
    draw_patent_style_background(image, rng)

    placements = [
        ("A", 150, 170),
        ("B", 520, 170),
        ("10A", 850, 170),
        ("7'", 170, 680),
        ("8'", 540, 680),
        ("9'", 910, 680),
    ]
    font_path = Path("C:/Windows/Fonts/times.ttf")

    if not font_path.exists():
        raise FileNotFoundError(f"找不到驗證字型：{font_path}")

    for text, x, y in placements:
        glyphs, _, _ = build_group_glyphs(
            text=text,
            font_path=font_path,
            font_size=76,
            rng=rng,
        )
        cursor_x = x

        for glyph in glyphs:
            mask = glyph["mask"]
            glyph_y = y + glyph["top"]
            image.paste((5, 5, 5), (cursor_x, glyph_y), mask)
            cursor_x += mask.width + glyph["spacing"]

    image.save(output_path, format="PNG")


def create_prediction_preview(model, image_path, output_path):
    image = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(image)
    prediction = model.predict(
        source=str(image_path),
        imgsz=1280,
        conf=0.20,
        device=get_compute_device(),
        verbose=False,
    )[0]

    for box in prediction.boxes:
        x1, y1, x2, y2 = map(float, box.xyxy[0])
        class_id = int(box.cls[0])
        confidence = float(box.conf[0])
        class_name = str(model.names[class_id])
        draw.rectangle((x1, y1, x2, y2), outline=(220, 20, 60), width=3)
        draw.text(
            (x1, max(0, y1 - 16)),
            f"{class_name} {confidence:.2f}",
            fill=(20, 90, 200),
        )

    image.save(output_path, format="PNG")


def main():
    args = parse_args()
    model_path = args.model.resolve()
    output_dir = args.output.resolve()

    if not model_path.exists():
        raise FileNotFoundError(f"找不到字元模型：{model_path}")

    output_dir.mkdir(parents=True, exist_ok=True)
    holdout_path = output_dir / "holdout_targets.png"
    preview_path = output_dir / "holdout_predictions.png"
    report_path = output_dir / "verification_report.json"
    create_holdout_image(holdout_path)

    model = YOLO(str(model_path))
    class_map = load_class_map(model_path)

    if not should_use_yolo_class_as_char(model, class_map):
        raise AssertionError("模型未被應用程式判定為 YOLO 字元模型。")

    result = recognize_one_image(
        image_path=holdout_path,
        model=model,
        reader=None,
        model_name=model_path.name,
        class_map=class_map,
        use_yolo_class_as_char=True,
        imgsz=1280,
        yolo_conf=0.20,
        ocr_conf=0.20,
    )
    detected = result["labels"]
    missing = [label for label in EXPECTED_LABELS if label not in detected]
    unexpected = [label for label in detected if label not in EXPECTED_LABELS]

    create_prediction_preview(model, holdout_path, preview_path)

    report = {
        "model": str(model_path),
        "expected": list(EXPECTED_LABELS),
        "detected": detected,
        "missing": missing,
        "unexpected": unexpected,
        "passed": not missing,
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"預測圖：{preview_path}")

    if missing:
        raise AssertionError(f"缺少預期標號：{', '.join(missing)}")


if __name__ == "__main__":
    main()
