import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from features.patent_ocr.easyocr_loader import (
    clear_easyocr_reader_cache,
    create_easyocr_reader,
)
from features.patent_ocr.model_loader import (
    clear_detection_model_cache,
    load_detection_model,
)


class RuntimeCacheTests(unittest.TestCase):
    def tearDown(self):
        clear_detection_model_cache()
        clear_easyocr_reader_cache()

    def test_detection_model_reuses_only_the_same_file_version(self):
        with TemporaryDirectory() as temporary_directory:
            model_path = Path(temporary_directory) / "model.onnx"
            model_path.write_bytes(b"first")
            with patch(
                "features.patent_ocr.onnx_detector.OnnxDetector",
                side_effect=lambda path: object(),
            ) as detector:
                first = load_detection_model(model_path)
                second = load_detection_model(model_path)
                model_path.write_bytes(b"second-version")
                third = load_detection_model(model_path)

            self.assertIs(first, second)
            self.assertIsNot(first, third)
            self.assertEqual(detector.call_count, 2)

    def test_compact_reader_reuses_only_the_same_file_version(self):
        with TemporaryDirectory() as temporary_directory:
            model_dir = Path(temporary_directory)
            model_path = model_dir / "english_g2.pth"
            model_path.write_bytes(b"first")
            with (
                patch(
                    "features.patent_ocr.easyocr_loader.get_easyocr_model_dir",
                    return_value=model_dir,
                ),
                patch(
                    "features.patent_ocr.easyocr_loader.CompactEnglishReader",
                    side_effect=lambda *args, **kwargs: object(),
                ) as reader,
            ):
                first = create_easyocr_reader(False)
                second = create_easyocr_reader(False)
                model_path.write_bytes(b"second-version")
                third = create_easyocr_reader(False)

            self.assertIs(first, second)
            self.assertIsNot(first, third)
            self.assertEqual(reader.call_count, 2)


if __name__ == "__main__":
    unittest.main()
