import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from training.figure_heading.export_candidate import (
    build_export_metadata,
    sha256_file,
    validate_candidate_paths,
)


class FigureHeadingCandidateExportTests(unittest.TestCase):
    def _candidate(self, root):
        model = root / "pilot.pt"
        model.write_bytes(b"pilot-pt")
        metrics = root / "pilot_metrics.json"
        metrics.write_text(
            json.dumps(
                {
                    "data": "pilot-dataset/data.yaml",
                    "dataset_report": {"reviewed_pages": 250},
                    "validation_metrics": {"metrics/mAP50(B)": 0.75},
                    "test_metrics": {"metrics/mAP50(B)": 0.70},
                    "onnx_export_error": "ModuleNotFoundError: onnx",
                }
            ),
            encoding="utf-8",
        )
        return model, metrics

    def test_validation_refuses_formal_models_directory_and_existing_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            formal_models = root / "models"
            formal_models.mkdir()
            formal_model, _metrics = self._candidate(formal_models)
            with self.assertRaisesRegex(RuntimeError, "models 正式模型目錄"):
                validate_candidate_paths(
                    formal_model,
                    formal_models_dir=formal_models,
                )

            candidate_dir = root / "candidates"
            candidate_dir.mkdir()
            model, _metrics = self._candidate(candidate_dir)
            model.with_suffix(".onnx").write_bytes(b"existing")
            with self.assertRaisesRegex(FileExistsError, "避免覆寫"):
                validate_candidate_paths(
                    model,
                    formal_models_dir=formal_models,
                )

    def test_metadata_preserves_training_report_and_replaces_export_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            model, metrics_path = self._candidate(root)
            onnx_path = model.with_suffix(".onnx")
            names_path = model.with_suffix(".names.json")
            onnx_path.write_bytes(b"onnx-artifact")
            names_path.write_text(
                json.dumps(
                    {
                        "names": [
                            "figure_prefix",
                            "figure_identifier",
                            "figure_prefix_rotate_right",
                        ]
                    }
                ),
                encoding="utf-8",
            )
            original = json.loads(metrics_path.read_text(encoding="utf-8"))
            original_copy = json.loads(json.dumps(original))
            original_digest = hashlib.sha256(metrics_path.read_bytes()).hexdigest()

            updated = build_export_metadata(
                original,
                model_path=model,
                onnx_path=onnx_path,
                names_path=names_path,
                imgsz=1536,
                environment={
                    "python": "test",
                    "ultralytics": "8.4.90",
                    "onnx": "1.22.0",
                    "device": "cpu",
                },
                original_metrics_sha256=original_digest,
            )

            self.assertEqual(original, original_copy)
            self.assertEqual(updated["dataset_report"], {"reviewed_pages": 250})
            self.assertEqual(
                updated["validation_metrics"],
                {"metrics/mAP50(B)": 0.75},
            )
            self.assertEqual(updated["test_metrics"], {"metrics/mAP50(B)": 0.70})
            self.assertNotIn("onnx_export_error", updated)
            self.assertEqual(updated["candidate_pt_sha256"], sha256_file(model))
            self.assertEqual(updated["onnx_sha256"], sha256_file(onnx_path))
            self.assertEqual(
                updated["onnx_names_sha256"],
                sha256_file(names_path),
            )
            self.assertEqual(updated["pre_export_metrics_sha256"], original_digest)
            self.assertEqual(updated["onnx_export"]["imgsz"], 1536)
            self.assertEqual(updated["onnx_export"]["environment"]["device"], "cpu")


if __name__ == "__main__":
    unittest.main()
