from pathlib import Path

import cv2

from PySide6.QtGui import QPixmap, QTransform
from PySide6.QtCore import Qt

from app.paths import get_output_base_dir
from features.patent_ocr.image_io import read_image, write_image


def make_rotated_image_path(original_path):
    """
    產生旋轉後圖片的輸出路徑。
    不覆蓋原圖。
    """

    original_path = Path(original_path)

    output_dir = get_output_base_dir() / "rotated_images"
    output_dir.mkdir(parents=True, exist_ok=True)

    base_name = f"{original_path.stem}_rot90"
    candidate = output_dir / f"{base_name}.png"

    index = 1

    while candidate.exists():
        candidate = output_dir / f"{base_name}_{index:03d}.png"
        index += 1

    return candidate


def rotate_image_clockwise_90(image_path):
    """
    將圖片向右旋轉 90 度，並另存成新的 PNG。
    回傳旋轉後圖片路徑。
    """

    image_path = Path(image_path)

    if not image_path.exists():
        raise FileNotFoundError(f"找不到圖片：{image_path}")

    pixmap = QPixmap(str(image_path))

    if pixmap.isNull():
        raise RuntimeError(f"圖片讀取失敗，無法旋轉：{image_path}")

    transform = QTransform().rotate(90)

    rotated_pixmap = pixmap.transformed(
        transform,
        Qt.SmoothTransformation
    )

    output_path = make_rotated_image_path(image_path)

    success = rotated_pixmap.save(str(output_path), "PNG")

    if not success:
        raise RuntimeError(f"旋轉後圖片儲存失敗：{output_path}")

    return str(output_path)


def create_auto_oriented_image(image_path):
    """Create a clockwise 90-degree candidate for automatic orientation."""

    image_path = Path(image_path)
    image = read_image(image_path)
    if image is None:
        raise RuntimeError(f"圖片讀取失敗，無法自動轉向：{image_path}")

    output_dir = get_output_base_dir() / "auto_oriented_images"
    output_dir.mkdir(parents=True, exist_ok=True)
    base_name = f"{image_path.stem}_auto_rot90"
    output_path = output_dir / f"{base_name}.png"
    index = 1
    while output_path.exists():
        output_path = output_dir / f"{base_name}_{index:03d}.png"
        index += 1

    rotated = cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
    if not write_image(output_path, rotated):
        raise RuntimeError(f"自動轉向圖片儲存失敗：{output_path}")
    return str(output_path)
