import copy
import tempfile
import unittest
from pathlib import Path

from features.patent_ocr.figure_heading_classes import FIGURE_HEADING_CLASS_NAMES
from training.figure_heading.training_policy import validate_training_policy


class FigureHeadingTrainingPolicyTests(unittest.TestCase):
    def report(self):
        return {"ready_for_training": False, "document_level_split_preserved": True,
                "cross_split_duplicate_images": 0,
                "classes": {name: i for i, name in enumerate(FIGURE_HEADING_CLASS_NAMES)},
                "counts": {"train_images": 600, "val_images": 160, "test_images": 180},
                "dataset_content_sha256": "frozen", "dataset_inventory_sha256": "frozen"}

    def test_small_set_requires_explicit_pilot_flag_without_mutating_readiness(self):
        report = self.report()
        with self.assertRaises(RuntimeError):
            validate_training_policy(report)
        validate_training_policy(report, experimental=True)
        self.assertFalse(report["ready_for_training"])

    def test_pilot_still_requires_integrity_and_all_three_splits(self):
        for field, value in (("document_level_split_preserved", False),
                             ("cross_split_duplicate_images", 1),
                             ("dataset_content_sha256", None), ("classes", {}),
                             ("counts", {"train_images": 500, "test_images": 100})):
            with self.subTest(field=field):
                report = copy.deepcopy(self.report())
                report[field] = value
                with self.assertRaises(RuntimeError):
                    validate_training_policy(report, experimental=True)

    def test_pilot_cannot_overwrite_production_models(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaises(RuntimeError):
                validate_training_policy(self.report(), experimental=True,
                                         export_model=root / "models" / "pilot.pt",
                                         production_root=root / "models")
            validate_training_policy(self.report(), experimental=True,
                                     export_model=root / "candidates" / "pilot.pt",
                                     production_root=root / "models")


if __name__ == "__main__":
    unittest.main()
