import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from features.patent_ocr.figure_heading_classes import FIGURE_HEADING_CLASS_NAMES
from features.patent_ocr.ocr_worker import resolve_figure_heading_model_path
from training.figure_heading.evaluate_candidate import (
    OFFICIAL_RELEASE_FLOORS,
    evaluate,
)
from training.figure_heading.dataset_integrity import write_dataset_inventory
from training.figure_heading.promote_candidate import promote


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class FigureHeadingCandidateEvaluationTests(unittest.TestCase):
    def test_frozen_test_checks_mapping_orientation_and_negative_page(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset = root / "dataset"
            dataset.mkdir()
            model_path = root / "candidate.onnx"
            model_path.write_bytes(b"candidate")
            items = []
            for page_index in range(10):
                for rotation in (0, 90, 180, 270):
                    is_mixed_case = page_index == 0 and rotation == 0
                    items.append(
                        {
                            "item_id": f"positive-{page_index}-r{rotation}",
                            "page_id": f"positive-{page_index}",
                            "document_id": f"positive-doc-{page_index % 5}",
                            "split": "test",
                            "image_path": f"positive-{page_index}-r{rotation}.png",
                            "expected_figure_numbers": ["3A"],
                            "expected_orientation_status": (
                                "mixed_orientation"
                                if is_mixed_case
                                else "needs_rotation"
                            ),
                            "expected_correction_degrees": (
                                None if is_mixed_case else 270
                            ),
                        }
                    )
            for page_index in range(5):
                for rotation in (0, 90, 180, 270):
                    items.append(
                        {
                            "item_id": f"negative-{page_index}-r{rotation}",
                            "page_id": f"negative-{page_index}",
                            "document_id": f"negative-doc-{page_index}",
                            "split": "test",
                            "image_path": f"negative-{page_index}-r{rotation}.png",
                            "expected_figure_numbers": [],
                            "expected_orientation_status": "no_evidence",
                            "expected_correction_degrees": None,
                        }
                    )
            (dataset / "evaluation_manifest.json").write_text(
                json.dumps(items),
                encoding="utf-8",
            )
            (dataset / "data.yaml").write_text("names: {}\n", encoding="utf-8")
            inventory = write_dataset_inventory(dataset)
            (dataset / "dataset_report.json").write_text(
                json.dumps(
                    {
                        "ready_for_training": True,
                        "dataset_content_sha256": inventory["content_sha256"],
                        "dataset_inventory_sha256": inventory[
                            "inventory_sha256"
                        ],
                    }
                ),
                encoding="utf-8",
            )
            model = Mock()
            model.names = dict(enumerate(FIGURE_HEADING_CLASS_NAMES))
            good_positive = {
                    "detected_figure_numbers": ["3A"],
                    "auto_figure_numbers": ["3A"],
                    "orientation": {
                        "status": "needs_rotation",
                        "correction_degrees": 270,
                    },
                }
            good_negative = {
                    "detected_figure_numbers": [],
                    "auto_figure_numbers": [],
                    "orientation": {
                        "status": "no_evidence",
                        "correction_degrees": None,
                    },
                }
            good_mixed = {
                    "detected_figure_numbers": ["3A"],
                    "auto_figure_numbers": ["3A"],
                    "orientation": {
                        "status": "mixed_orientation",
                        "correction_degrees": None,
                    },
                }
            bad_negative = {
                    "detected_figure_numbers": [],
                    "auto_figure_numbers": [],
                    "orientation": {
                        "status": "needs_rotation",
                        "correction_degrees": 90,
                    },
                }
            state = {"bad_negative": False}

            def analyze(path, *_args, **_kwargs):
                if str(path).endswith("positive-0-r0.png"):
                    return good_mixed
                if "negative-" not in str(path):
                    return good_positive
                if state["bad_negative"]:
                    return bad_negative
                return good_negative

            with (
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
            ):
                report = evaluate(dataset, model_path)
                state["bad_negative"] = True
                bad_report = evaluate(
                    dataset,
                    model_path,
                    output_path=root / "bad-evaluation.json",
                )
            self.assertTrue(report["passes_release_thresholds"])
            self.assertEqual(report["orientation_accuracy"], 1.0)
            self.assertEqual(report["orientation_eligible_items"], 40)
            self.assertEqual(report["negative_false_positive_rate"], 0.0)
            self.assertEqual(report["positive_pages"], 10)
            self.assertEqual(report["negative_documents"], 5)
            self.assertFalse(bad_report["passes_release_thresholds"])
            self.assertEqual(bad_report["negative_false_positive_rate"], 1.0)

    def test_release_thresholds_cannot_be_lowered(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            model_path = root / "candidate.onnx"
            model_path.write_bytes(b"candidate")
            with self.assertRaisesRegex(RuntimeError, "不得低於"):
                evaluate(root, model_path, minimum_positive_items=1)


class FigureHeadingPromotionTests(unittest.TestCase):
    def test_only_explicit_passing_digest_can_create_approved_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = root / "candidate" / "figure_heading_locator_v1.onnx"
            candidate.parent.mkdir()
            candidate.write_bytes(b"candidate-model")
            candidate.with_suffix(".names.json").write_text(
                json.dumps({"names": list(FIGURE_HEADING_CLASS_NAMES)}),
                encoding="utf-8",
            )
            dataset = root / "dataset"
            dataset.mkdir()
            (dataset / "data.yaml").write_text("names: {}\n", encoding="utf-8")
            (dataset / "evaluation_manifest.json").write_text(
                "[]\n",
                encoding="utf-8",
            )
            inventory = write_dataset_inventory(dataset)
            dataset_report = dataset / "dataset_report.json"
            dataset_report.write_text(
                json.dumps(
                    {
                        "ready_for_training": True,
                        "dataset_content_sha256": inventory["content_sha256"],
                        "dataset_inventory_sha256": inventory[
                            "inventory_sha256"
                        ],
                    }
                ),
                encoding="utf-8",
            )
            evaluation = root / "evaluation.json"
            evaluation_payload = {
                        "version": 2,
                        "passes_release_thresholds": True,
                        "model": str(candidate.resolve()),
                        "model_sha256": sha256(candidate),
                        "dataset": str(dataset),
                        "dataset_report_sha256": sha256(dataset_report),
                        "dataset_content_sha256": inventory["content_sha256"],
                        "dataset_inventory_sha256": inventory[
                            "inventory_sha256"
                        ],
                        "detected_set_exact_accuracy": 1.0,
                        "auto_mapping_exact_accuracy": 1.0,
                        "orientation_accuracy": 1.0,
                        "negative_false_positive_rate": 0.0,
                        "evaluated_split": "test",
                        "test_items": 60,
                        "positive_items": 40,
                        "negative_items": 20,
                        "positive_pages": 10,
                        "negative_pages": 5,
                        "positive_documents": 5,
                        "negative_documents": 5,
                        "rare_identifier_items": 40,
                        "rare_identifier_pages": 10,
                        "rare_identifier_documents": 5,
                        "orientation_eligible_items": 40,
                        "analysis_error_count": 0,
                        "rare_detected_set_exact_accuracy": 1.0,
                        "rare_auto_mapping_exact_accuracy": 1.0,
                        "thresholds": dict(OFFICIAL_RELEASE_FLOORS),
                    }
            evaluation.write_text(
                json.dumps(evaluation_payload),
                encoding="utf-8",
            )
            models = root / "models"
            with self.assertRaisesRegex(RuntimeError, "--approve"):
                promote(candidate, evaluation, models)

            bad_payload = dict(evaluation_payload)
            bad_payload["thresholds"] = {}
            evaluation.write_text(json.dumps(bad_payload), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "報告無效"):
                promote(candidate, evaluation, models, approve=True)

            bad_payload = dict(evaluation_payload)
            bad_payload["rare_auto_mapping_exact_accuracy"] = 0.50
            evaluation.write_text(json.dumps(bad_payload), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "未通過全部發布門檻"):
                promote(candidate, evaluation, models, approve=True)
            evaluation.write_text(
                json.dumps(evaluation_payload),
                encoding="utf-8",
            )

            manifest = promote(candidate, evaluation, models, approve=True)
            production_model = models / "figure_heading_locator_v1.onnx"
            self.assertTrue(manifest["approved_for_production"])
            self.assertEqual(
                resolve_figure_heading_model_path(models / "main.onnx"),
                production_model,
            )
            production_model.write_bytes(b"tampered")
            self.assertIsNone(
                resolve_figure_heading_model_path(models / "main.onnx")
            )


if __name__ == "__main__":
    unittest.main()
