import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from features.patent_ocr.figure_heading_classes import FIGURE_HEADING_CLASS_NAMES
from training.figure_heading.dataset_integrity import write_dataset_inventory
from training.figure_heading.evaluate_candidate import (
    evaluate,
    release_report_passes,
)


class FigureHeadingPilotEvaluationTests(unittest.TestCase):
    def _make_dataset(self, root, *, ready=False):
        dataset = root / "dataset"
        dataset.mkdir()
        items = [
            {
                "item_id": "positive-0",
                "page_id": "page-0",
                "document_id": "doc-0",
                "split": "val",
                "image_path": "positive-0.png",
                "expected_figure_numbers": ["1"],
                "expected_orientation_status": "upright",
                "expected_correction_degrees": 0,
                "rotation_applied_clockwise": 0,
            },
            {
                "item_id": "positive-90",
                "page_id": "page-90",
                "document_id": "doc-90",
                "split": "val",
                "image_path": "positive-90.png",
                "expected_figure_numbers": ["2A"],
                "expected_orientation_status": "needs_rotation",
                "expected_correction_degrees": 90,
                "rotation_applied_clockwise": 0,
            },
            {
                "item_id": "analysis-error-180",
                "page_id": "page-180",
                "document_id": "doc-180",
                "split": "val",
                "image_path": "analysis-error-180.png",
                "expected_figure_numbers": ["3"],
                "expected_orientation_status": "needs_rotation",
                "expected_correction_degrees": 180,
                "rotation_applied_clockwise": 90,
            },
            {
                "item_id": "negative",
                "page_id": "page-negative",
                "document_id": "doc-negative",
                "split": "val",
                "image_path": "negative.png",
                "expected_figure_numbers": [],
                "expected_orientation_status": "no_evidence",
                "expected_correction_degrees": None,
                "rotation_applied_clockwise": 0,
            },
        ]
        (dataset / "evaluation_manifest.json").write_text(
            json.dumps(items),
            encoding="utf-8",
        )
        (dataset / "data.yaml").write_text("names: {}\n", encoding="utf-8")
        inventory = write_dataset_inventory(dataset)
        (dataset / "dataset_report.json").write_text(
            json.dumps(
                {
                    "ready_for_training": ready,
                    "dataset_content_sha256": inventory["content_sha256"],
                    "dataset_inventory_sha256": inventory["inventory_sha256"],
                }
            ),
            encoding="utf-8",
        )
        model_path = root / "candidate.onnx"
        model_path.write_bytes(b"candidate")
        return dataset, model_path

    def _patched_runtime(self, *, orientation_overrides=None):
        model = Mock()
        model.names = dict(enumerate(FIGURE_HEADING_CLASS_NAMES))
        orientation_overrides = orientation_overrides or {}

        def analyze(path, *_args, **_kwargs):
            name = Path(path).name
            if name == "analysis-error-180.png":
                raise RuntimeError("synthetic failure")
            if name == "negative.png":
                return {
                    "detected_figure_numbers": [],
                    "auto_figure_numbers": [],
                    "orientation": {
                        "status": "no_evidence",
                        "correction_degrees": None,
                    },
                }
            correction = 90 if name == "positive-90.png" else 0
            figure_number = "2A" if correction == 90 else "1"
            orientation = orientation_overrides.get(
                name,
                {
                    "status": "needs_rotation" if correction else "upright",
                    "correction_degrees": correction,
                },
            )
            return {
                "detected_figure_numbers": [figure_number],
                "auto_figure_numbers": [figure_number],
                "mapping": {"status": "accepted"},
                "figure_caption_detections": [
                    {
                        "figure_number": figure_number,
                        "prefix_box": {"x1": 10, "y1": 10, "x2": 20, "y2": 20},
                        "identifier_box": {
                            "x1": 22,
                            "y1": 10,
                            "x2": 32,
                            "y2": 20,
                        },
                    }
                ],
                "prefix_detection_count": 1,
                "identifier_detection_count": 1,
                "strong_prefix_count": 1,
                "orientation": orientation,
            }

        return (
            patch(
                "training.figure_heading.evaluate_candidate.load_detection_model",
                return_value=model,
            ),
            patch(
                "training.figure_heading.evaluate_candidate.create_easyocr_reader",
                return_value=Mock(),
            ),
            patch(
                "training.figure_heading.evaluate_candidate.get_compute_device",
                return_value="cpu",
            ),
            patch(
                "training.figure_heading.evaluate_candidate.analyze_figure_headings",
                side_effect=analyze,
            ),
        )

    def test_experimental_val_evaluates_unready_dataset_but_cannot_pass(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset, model_path = self._make_dataset(root, ready=False)
            loaders = self._patched_runtime()
            with loaders[0], loaders[1], loaders[2], loaders[3]:
                report = evaluate(
                    dataset,
                    model_path,
                    experimental=True,
                    split="val",
                )

            self.assertTrue(report["experimental"])
            self.assertEqual(report["evaluation_mode"], "experimental")
            self.assertFalse(report["dataset_ready_for_training"])
            self.assertFalse(report["passes_release_thresholds"])
            self.assertFalse(report["approved_for_production"])
            self.assertFalse(release_report_passes(report))
            self.assertEqual(report["evaluated_split"], "val")
            self.assertEqual(report["evaluated_items"], 4)
            self.assertEqual(report["test_items"], 0)
            self.assertEqual(report["orientation_eligible_items"], 3)
            self.assertEqual(report["orientation_accuracy"], 0.666667)
            self.assertEqual(report["auto_decision_count"], 2)
            self.assertEqual(report["correct_auto_decisions"], 2)
            self.assertEqual(report["wrong_auto_decisions"], 0)
            self.assertEqual(report["abstentions"], 1)
            self.assertEqual(report["auto_decision_precision"], 1.0)
            self.assertEqual(len(report["predictions"]), 4)
            self.assertEqual(
                {item["item_id"] for item in report["predictions"]},
                {
                    "positive-0",
                    "positive-90",
                    "analysis-error-180",
                    "negative",
                },
            )
            failed = next(
                item
                for item in report["predictions"]
                if item["item_id"] == "analysis-error-180"
            )
            self.assertIn("synthetic failure", failed["error"])
            by_angle = report["metrics_by_expected_correction"]
            self.assertEqual(by_angle["0"]["orientation_accuracy"], 1.0)
            self.assertEqual(by_angle["0"]["auto_decision_count"], 1)
            self.assertEqual(by_angle["0"]["auto_decision_precision"], 1.0)
            self.assertEqual(by_angle["90"]["auto_mapping_exact_accuracy"], 1.0)
            self.assertEqual(by_angle["180"]["analysis_error_count"], 1)
            self.assertEqual(by_angle["180"]["abstentions"], 1)
            self.assertEqual(by_angle["180"]["auto_decision_precision"], 0.0)
            self.assertEqual(by_angle["none"]["negative_false_positive_rate"], 0.0)
            by_source_angle = report["metrics_by_source_correction"]
            self.assertEqual(by_source_angle["0"]["items"], 1)
            self.assertEqual(by_source_angle["90"]["items"], 1)
            self.assertEqual(by_source_angle["270"]["analysis_error_count"], 1)
            source_90 = next(
                item
                for item in report["predictions"]
                if item["item_id"] == "positive-90"
            )
            self.assertEqual(source_90["source_correction_degrees"], 90)
            self.assertEqual(source_90["prefix_detection_count"], 1)
            self.assertEqual(
                source_90["figure_caption_detections"][0]["figure_number"],
                "2A",
            )

    def test_orientation_metrics_separate_wrong_decisions_from_abstentions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset, model_path = self._make_dataset(root, ready=False)
            loaders = self._patched_runtime(
                orientation_overrides={
                    "positive-0.png": {
                        "status": "needs_rotation",
                        "correction_degrees": 90,
                    },
                    "positive-90.png": {
                        "status": "ambiguous",
                        "correction_degrees": None,
                    },
                }
            )
            with loaders[0], loaders[1], loaders[2], loaders[3]:
                report = evaluate(
                    dataset,
                    model_path,
                    experimental=True,
                    split="val",
                )

            self.assertEqual(report["orientation_eligible_items"], 3)
            self.assertEqual(report["orientation_accuracy"], 0.0)
            self.assertEqual(report["auto_decision_count"], 1)
            self.assertEqual(report["correct_auto_decisions"], 0)
            self.assertEqual(report["wrong_auto_decisions"], 1)
            self.assertEqual(report["abstentions"], 2)
            self.assertEqual(report["auto_decision_precision"], 0.0)

            by_angle = report["metrics_by_expected_correction"]
            self.assertEqual(by_angle["0"]["wrong_auto_decisions"], 1)
            self.assertEqual(by_angle["0"]["abstentions"], 0)
            self.assertEqual(by_angle["90"]["auto_decision_count"], 0)
            self.assertEqual(by_angle["90"]["abstentions"], 1)
            self.assertEqual(by_angle["180"]["abstentions"], 1)

    def test_unready_dataset_and_val_split_remain_blocked_for_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset, model_path = self._make_dataset(root, ready=False)
            with self.assertRaisesRegex(RuntimeError, "未通過正式門檻"):
                evaluate(dataset, model_path)
            with self.assertRaisesRegex(RuntimeError, "只允許使用 --experimental"):
                evaluate(dataset, model_path, split="val")

    def test_experimental_mode_does_not_allow_lower_release_thresholds(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset, model_path = self._make_dataset(root, ready=False)
            with self.assertRaisesRegex(RuntimeError, "不得低於正式安全線"):
                evaluate(
                    dataset,
                    model_path,
                    experimental=True,
                    minimum_positive_items=1,
                )


if __name__ == "__main__":
    unittest.main()
