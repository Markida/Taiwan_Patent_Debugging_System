import hashlib
from pathlib import Path

from PySide6.QtGui import QImage, QTransform
from PySide6.QtCore import Qt

from app.paths import get_output_base_dir


def make_rotated_image_path(original_path, direction="right"):
    """
    產生旋轉後圖片的輸出路徑。
    不覆蓋原圖。
    """

    original_path = Path(original_path)

    output_dir = get_output_base_dir() / "rotated_images"
    output_dir.mkdir(parents=True, exist_ok=True)

    suffix = "rot_left90" if direction == "left" else "rot90"
    base_name = f"{original_path.stem}_{suffix}"
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

    image = QImage(str(image_path))

    if image.isNull():
        raise RuntimeError(f"圖片讀取失敗，無法旋轉：{image_path}")

    transform = QTransform().rotate(90)

    rotated_image = image.transformed(
        transform,
        Qt.SmoothTransformation
    )

    output_path = make_rotated_image_path(image_path)

    success = rotated_image.save(str(output_path), "PNG")

    if not success:
        raise RuntimeError(f"旋轉後圖片儲存失敗：{output_path}")

    return str(output_path)


def rotate_image_counterclockwise_90(image_path):
    """將圖片向左旋轉 90 度，另存新檔並回傳其路徑。"""

    image_path = Path(image_path)

    if not image_path.exists():
        raise FileNotFoundError(f"找不到圖片：{image_path}")

    # QImage is reentrant and can safely perform this file/CPU work in the
    # OCR page's background rotation thread. QPixmap is tied to the GUI
    # platform and may crash or stall when used outside the main Qt thread.
    image = QImage(str(image_path))

    if image.isNull():
        raise RuntimeError(f"圖片讀取失敗，無法旋轉：{image_path}")

    rotated_image = image.transformed(
        QTransform().rotate(-90),
        Qt.SmoothTransformation,
    )
    output_path = make_rotated_image_path(image_path, direction="left")

    if not rotated_image.save(str(output_path), "PNG"):
        raise RuntimeError(f"旋轉後圖片儲存失敗：{output_path}")

    return str(output_path)


def create_auto_oriented_image(image_path, correction_degrees=90):
    """Create a non-destructive 90/180/270-degree orientation correction."""

    import cv2
    from features.patent_ocr.image_io import read_image, write_image

    image_path = Path(image_path)
    image = read_image(image_path)
    if image is None:
        raise RuntimeError(f"圖片讀取失敗，無法自動轉向：{image_path}")

    correction_degrees = int(correction_degrees) % 360
    if correction_degrees not in {90, 180, 270}:
        raise ValueError("自動轉向只接受 90、180 或 270 度。")

    output_dir = get_output_base_dir() / "auto_oriented_images"
    output_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(image_path.read_bytes()).hexdigest()[:16]
    base_name = (
        f"{image_path.stem}_{digest}_auto_rot{correction_degrees}"
    )
    output_path = output_dir / f"{base_name}.png"
    if output_path.is_file():
        cached = read_image(output_path)
        expected_shape = (
            (image.shape[1], image.shape[0])
            if correction_degrees in {90, 270}
            else image.shape[:2]
        )
        if cached is not None and cached.shape[:2] == expected_shape:
            return str(output_path)

    rotation_code = {
        90: cv2.ROTATE_90_CLOCKWISE,
        180: cv2.ROTATE_180,
        270: cv2.ROTATE_90_COUNTERCLOCKWISE,
    }[correction_degrees]
    rotated = cv2.rotate(image, rotation_code)
    if not write_image(output_path, rotated):
        raise RuntimeError(f"自動轉向圖片儲存失敗：{output_path}")
    return str(output_path)
