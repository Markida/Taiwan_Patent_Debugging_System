import os
import threading
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from features.patent_ocr.figure_orientation_batch import auto_orient_figure_images


class FigureOrientationBatchTests(unittest.TestCase):
    def test_rotates_only_confident_page_and_never_calls_component_ocr(self):
        model = Mock()
        analyses = [
            {"status": "ready", "orientation": {"status": "needs_rotation", "correction_degrees": 90}},
            {"status": "ready", "orientation": {"status": "ambiguous", "correction_degrees": None}},
        ]
        with (
            patch("features.patent_ocr.figure_orientation_batch.load_detection_model", return_value=model),
            patch("features.patent_ocr.figure_heading.is_figure_heading_model", return_value=True),
            patch("features.patent_ocr.easyocr_loader.create_easyocr_reader", return_value=Mock()),
            patch("features.patent_ocr.ocr_engine.get_easyocr_gpu_flag", return_value=False),
            patch("features.patent_ocr.ocr_engine.get_compute_device", return_value="cpu"),
            patch("features.patent_ocr.figure_heading.analyze_figure_headings", side_effect=analyses),
            patch("features.patent_ocr.image_tools.create_auto_oriented_image", return_value="one_rot90.png") as rotate,
            patch("features.patent_ocr.ocr_engine.recognize_one_image") as component_ocr,
        ):
            pages = auto_orient_figure_images(["one.png", "two.png"], "heading.onnx")
        self.assertEqual([page["image_path"] for page in pages], ["one_rot90.png", "two.png"])
        self.assertEqual([page["rotation_degrees"] for page in pages], [90, 0])
        rotate.assert_called_once()
        component_ocr.assert_not_called()

    def test_cancellation_does_not_return_partial_batch(self):
        cancel = threading.Event()

        def analyze(**_kwargs):
            cancel.set()
            return {"status": "ready", "orientation": {"status": "upright", "correction_degrees": 0}}

        with (
            patch("features.patent_ocr.figure_orientation_batch.load_detection_model", return_value=Mock()),
            patch("features.patent_ocr.figure_heading.is_figure_heading_model", return_value=True),
            patch("features.patent_ocr.easyocr_loader.create_easyocr_reader", return_value=Mock()),
            patch("features.patent_ocr.ocr_engine.get_easyocr_gpu_flag", return_value=False),
            patch("features.patent_ocr.ocr_engine.get_compute_device", return_value="cpu"),
            patch("features.patent_ocr.figure_heading.analyze_figure_headings", side_effect=analyze),
        ):
            self.assertIsNone(auto_orient_figure_images(["one.png", "two.png"], "heading.onnx", cancel_event=cancel))


if __name__ == "__main__":
    unittest.main()
