import threading
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PySide6.QtGui import QColor, QImage

from features.patent_ocr.image_tools import (
    create_auto_oriented_image,
    rotate_image_clockwise_90,
)


class ImageRotationBackgroundTests(unittest.TestCase):
    def test_clockwise_rotation_is_safe_off_gui_thread_and_pixel_exact(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.png"
            image = QImage(2, 3, QImage.Format_ARGB32)
            colors = [
                QColor("#ff0000"), QColor("#00ff00"),
                QColor("#0000ff"), QColor("#ffff00"),
                QColor("#ff00ff"), QColor("#00ffff"),
            ]
            for index, color in enumerate(colors):
                image.setPixelColor(index % 2, index // 2, color)
            self.assertTrue(image.save(str(source), "PNG"))

            result = []
            errors = []

            def rotate():
                try:
                    result.append(rotate_image_clockwise_90(source))
                except BaseException as exc:
                    errors.append(exc)

            with patch(
                "features.patent_ocr.image_tools.get_output_base_dir",
                return_value=root,
            ):
                worker = threading.Thread(target=rotate)
                worker.start()
                worker.join(2)

            self.assertFalse(worker.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(len(result), 1)
            rotated = QImage(result[0])
            self.assertEqual((rotated.width(), rotated.height()), (3, 2))
            expected = [
                [colors[4], colors[2], colors[0]],
                [colors[5], colors[3], colors[1]],
            ]
            for y, row in enumerate(expected):
                for x, color in enumerate(row):
                    self.assertEqual(rotated.pixelColor(x, y), color)

    def test_auto_orientation_reuses_content_addressed_output(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.png"
            image = QImage(2, 3, QImage.Format_ARGB32)
            image.fill(QColor("white"))
            self.assertTrue(image.save(str(source), "PNG"))

            with patch(
                "features.patent_ocr.image_tools.get_output_base_dir",
                return_value=root,
            ):
                first = create_auto_oriented_image(source, 90)
                second = create_auto_oriented_image(source, 90)

            self.assertEqual(first, second)
            outputs = list((root / "auto_oriented_images").glob("*.png"))
            self.assertEqual(outputs, [Path(first)])
            rotated = QImage(first)
            self.assertEqual((rotated.width(), rotated.height()), (3, 2))


if __name__ == "__main__":
    unittest.main()
