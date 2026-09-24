import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from features.patent_ocr.image_io import read_image


class UnicodeImageIoTests(unittest.TestCase):
    def test_reads_png_from_chinese_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_dir = Path(temp_dir) / "專利完整圖式"
            image_dir.mkdir()
            image_path = image_dir / "測試頁面_001.png"
            expected = np.full((24, 32, 3), 173, dtype=np.uint8)
            success, encoded = cv2.imencode(".png", expected)
            self.assertTrue(success)
            encoded.tofile(str(image_path))

            actual = read_image(image_path)

            self.assertIsNotNone(actual)
            self.assertEqual(actual.shape, expected.shape)
            self.assertTrue(np.array_equal(actual, expected))

    def test_missing_image_returns_none(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "不存在的圖片.png"
            self.assertIsNone(read_image(image_path))


if __name__ == "__main__":
    unittest.main()
