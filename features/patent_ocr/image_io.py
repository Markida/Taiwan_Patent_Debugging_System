"""Image I/O helpers that support Unicode paths on Windows."""

from pathlib import Path

import cv2
import numpy as np


def read_image(image_path, flags=cv2.IMREAD_COLOR):
    """Read an image without relying on OpenCV's narrow Windows file path API."""

    image_path = Path(image_path)

    try:
        encoded = np.fromfile(str(image_path), dtype=np.uint8)
    except OSError:
        return None

    if encoded.size == 0:
        return None

    try:
        return cv2.imdecode(encoded, flags)
    except cv2.error:
        return None


def write_image(image_path, image):
    """Write an image through an encoded buffer so Unicode paths work."""

    image_path = Path(image_path)
    extension = image_path.suffix or ".png"

    try:
        success, encoded = cv2.imencode(extension, image)
        if not success:
            return False
        image_path.parent.mkdir(parents=True, exist_ok=True)
        encoded.tofile(str(image_path))
        return image_path.exists()
    except (OSError, cv2.error):
        return False
