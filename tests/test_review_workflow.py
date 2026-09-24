import json
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

import cv2
import numpy as np

from features.patent_ocr.image_io import write_image
from features.patent_ocr.review_exporter import (
    ReviewPackageTransferError,
    export_review_package,
)
from features.patent_ocr.review_tools import (
    detection_confidence,
    is_manually_oriented,
    is_manual_rotation_output,
    recognition_quality_score,
    should_use_rotated_result,
)


class ReviewConfidenceTests(unittest.TestCase):
    def test_uses_conservative_ocr_and_yolo_confidence(self):
        self.assertAlmostEqual(
            detection_confidence({"ocr_conf": 0.92, "yolo_conf": 0.71}),
            0.71,
        )

    def test_prefers_materially_better_rotated_result(self):
        original = {"detections": [{"label": "1", "confidence": 0.40}]}
        rotated = {"detections": [{"label": "1", "confidence": 0.90}]}
        self.assertGreater(
            recognition_quality_score(rotated),
            recognition_quality_score(original),
        )
        self.assertTrue(should_use_rotated_result(original, rotated))

    def test_keeps_original_when_scores_are_close(self):
        original = {"detections": [{"label": "1", "confidence": 0.90}]}
        rotated = {"detections": [{"label": "1", "confidence": 0.91}]}
        self.assertFalse(should_use_rotated_result(original, rotated))

    def test_manual_orientation_path_is_normalized(self):
        image_path = Path("manual_orientation.png")
        self.assertTrue(
            is_manually_oriented(image_path, [str(image_path.resolve())])
        )

    def test_manual_rotation_output_stays_locked_after_restart(self):
        image_path = Path("outputs") / "rotated_images" / "page_rot90.png"
        self.assertTrue(is_manual_rotation_output(image_path))
        self.assertTrue(is_manually_oriented(image_path, []))

class ReviewExportTests(unittest.TestCase):
    def test_exports_low_confidence_crop_and_correction_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            image_path = temp_path / "中文圖片.png"
            image = np.full((80, 120, 3), 255, dtype=np.uint8)
            cv2.rectangle(image, (10, 15), (50, 55), (0, 0, 0), 2)
            self.assertTrue(write_image(image_path, image))
            results = [{
                "image_name": "Pic_01",
                "image_path": str(image_path),
                "original_image_path": str(image_path),
                "rotation_degrees": 0,
                "numbers": ["8"],
                "detections": [{
                    "label": "8",
                    "original_label": "B",
                    "manual_edited": True,
                    "confidence": 0.62,
                    "x1": 10,
                    "y1": 15,
                    "x2": 50,
                    "y2": 55,
                }],
            }]

            package_path = export_review_package(
                results,
                confidence_threshold=0.80,
                output_dir=temp_path,
            )

            with ZipFile(package_path) as archive:
                self.assertIsNone(archive.testzip())
                names = set(archive.namelist())
                self.assertIn("review.json", names)
                self.assertIn("images/Pic_01.png", names)
                self.assertIn("crops/Pic_01_001.png", names)
                manifest = json.loads(archive.read("review.json"))
            issue = manifest["images"][0]["issues"][0]
            self.assertEqual(issue["original_label"], "B")
            self.assertEqual(issue["corrected_label"], "8")
            self.assertEqual(
                issue["issue_types"],
                ["low_confidence", "manual_edit"],
            )

    def test_exports_automatically_filtered_false_j(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            image_path = temp_path / "false_j.png"
            image = np.full((80, 120, 3), 255, dtype=np.uint8)
            self.assertTrue(write_image(image_path, image))
            results = [{
                "image_name": "Pic_01",
                "image_path": str(image_path),
                "original_image_path": str(image_path),
                "numbers": [],
                "detections": [],
                "rejected_detections": [{
                    "label": "J",
                    "original_label": "J",
                    "auto_filtered": True,
                    "rejection_reason": "high_risk_character_confidence",
                    "ocr_conf": 0.31,
                    "yolo_conf": 0.84,
                    "x1": 10,
                    "y1": 15,
                    "x2": 50,
                    "y2": 55,
                }],
            }]

            package_path = export_review_package(results, output_dir=temp_path)

            self.assertTrue(
                package_path.name.startswith(
                    "Saint-Island_Patent_MDS_review_"
                )
            )
            with ZipFile(package_path) as archive:
                manifest = json.loads(archive.read("review.json"))
            issue = manifest["images"][0]["issues"][0]
            self.assertIn("auto_filtered_false_positive", issue["issue_types"])
            self.assertEqual(issue["corrected_label"], "")
            self.assertEqual(
                issue["rejection_reason"],
                "high_risk_character_confidence",
            )

    def test_network_exports_have_unique_names_and_leave_no_local_copy(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            image_path = temp_path / "review.png"
            image = np.full((40, 60, 3), 255, dtype=np.uint8)
            self.assertTrue(write_image(image_path, image))
            results = [{
                "image_name": "Pic_01",
                "image_path": str(image_path),
                "numbers": ["8"],
                "detections": [{
                    "label": "8",
                    "manual_edited": True,
                    "original_label": "B",
                    "confidence": 0.60,
                    "x1": 5,
                    "y1": 5,
                    "x2": 25,
                    "y2": 30,
                }],
            }]
            network_dir = temp_path / "network" / "錯誤回報"
            staging_dir = temp_path / "pending"

            first_path = export_review_package(
                results,
                network_dir=network_dir,
                staging_dir=staging_dir,
            )
            second_path = export_review_package(
                results,
                network_dir=network_dir,
                staging_dir=staging_dir,
            )

            self.assertEqual(first_path.parent, network_dir)
            self.assertNotEqual(first_path.name, second_path.name)
            self.assertEqual(list(staging_dir.glob("*.zip")), [])
            for package_path in (first_path, second_path):
                with ZipFile(package_path) as archive:
                    self.assertIsNone(archive.testzip())

    def test_network_failure_retains_valid_local_backup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            image_path = temp_path / "review.png"
            image = np.full((40, 60, 3), 255, dtype=np.uint8)
            self.assertTrue(write_image(image_path, image))
            results = [{
                "image_name": "Pic_01",
                "image_path": str(image_path),
                "numbers": ["8"],
                "detections": [{
                    "label": "8",
                    "manual_edited": True,
                    "original_label": "B",
                    "confidence": 0.60,
                    "x1": 5,
                    "y1": 5,
                    "x2": 25,
                    "y2": 30,
                }],
            }]
            blocked_network_dir = temp_path / "not_a_directory"
            blocked_network_dir.write_text("blocked", encoding="utf-8")
            staging_dir = temp_path / "pending"

            with self.assertRaises(ReviewPackageTransferError) as raised:
                export_review_package(
                    results,
                    network_dir=blocked_network_dir,
                    staging_dir=staging_dir,
                )

            backup_path = raised.exception.local_backup_path
            self.assertTrue(backup_path.is_file())
            with ZipFile(backup_path) as archive:
                self.assertIsNone(archive.testzip())


if __name__ == "__main__":
    unittest.main()
