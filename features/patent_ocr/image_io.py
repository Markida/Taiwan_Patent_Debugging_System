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
