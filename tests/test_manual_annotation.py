import tempfile
import unittest
from collections import Counter
from pathlib import Path


from training.manual_annotation.build_training_datasets import (
    evaluate_readiness,
    rotate_annotation,
)
from training.manual_annotation.build_group_locator_dataset import (
    group_character_annotations,
)
from training.manual_annotation.common import (
    Annotation,
    CLASS_NAMES,
    normalize_label,
    read_page_record,
    write_page_record,
)


class ManualAnnotationCommonTests(unittest.TestCase):
    def test_normalizes_prime_and_letters(self):
        self.assertEqual(normalize_label("′"), "prime")
        self.assertEqual(normalize_label("'"), "prime")
        self.assertEqual(normalize_label("v"), "v")
        self.assertEqual(normalize_label("V"), "V")
        self.assertEqual(normalize_label("VIII"), "")

    def test_yolo_round_trip_preserves_box(self):
        original = Annotation("V", 10, 20, 30, 60)
        line = original.to_yolo(100, 200)
        restored = Annotation.from_yolo(line, 100, 200, label="V")
        self.assertAlmostEqual(restored.x1, 10, places=3)
        self.assertAlmostEqual(restored.y1, 20, places=3)
        self.assertAlmostEqual(restored.x2, 30, places=3)
        self.assertAlmostEqual(restored.y2, 60, places=3)

    def test_page_record_is_written_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "page.json"
            record = {"reviewed": True, "annotations": []}
            write_page_record(path, record)
            self.assertEqual(read_page_record(path), record)

    def test_rotates_annotation_clockwise_with_page(self):
        original = Annotation("prime", 10, 20, 30, 60)
        rotated = rotate_annotation(original, image_width=100, image_height=200, rotation=90)
        self.assertEqual((rotated.x1, rotated.y1, rotated.x2, rotated.y2), (140, 10, 180, 30))

    def test_builds_complete_label_groups(self):
        annotations = [
            Annotation("1", 10, 20, 22, 50),
            Annotation("0", 24, 20, 38, 50),
            Annotation("A", 40, 20, 56, 50),
            Annotation("prime", 55, 18, 61, 32),
        ]

        groups = group_character_annotations(annotations)

        self.assertEqual([group.text for group in groups], ["10A'"])

    def test_group_builder_splits_labels_across_parenthesis_gap(self):
        annotations = [
            Annotation("3", 10, 20, 22, 50),
            Annotation("3", 24, 20, 36, 50),
            Annotation("1", 38, 20, 50, 50),
            Annotation("3", 72, 20, 84, 50),
            Annotation("3", 86, 20, 98, 50),
        ]

        groups = group_character_annotations(annotations)

        self.assertEqual([group.text for group in groups], ["331", "33"])

    def test_group_builder_keeps_roman_sequence(self):
        annotations = [
            Annotation("V", 10, 20, 30, 55),
            Annotation("I", 31, 20, 37, 55),
            Annotation("I", 39, 20, 45, 55),
            Annotation("I", 47, 20, 53, 55),
        ]

        groups = group_character_annotations(annotations)

        self.assertEqual([group.text for group in groups], ["VIII"])

    def test_group_builder_normalizes_double_prime_strokes(self):
        annotations = [
            Annotation("2", 10, 20, 24, 50),
            Annotation("0", 26, 20, 40, 50),
            Annotation("prime", 42, 18, 48, 32),
            Annotation("prime", 49, 18, 55, 32),
        ]

        groups = group_character_annotations(annotations)

        self.assertEqual([group.text for group in groups], ["20'"])


class DatasetReadinessTests(unittest.TestCase):
    def complete_counts(self, value):
        return {name: value for name in CLASS_NAMES}

    def add_critical_minimums(self, counts):
        for name in ("I", "V", "X", "prime"):
            counts["train"][name] = 50
            counts["val"][name] = 5
        return counts

    def test_blocks_unreviewed_pages(self):
        counts = self.add_critical_minimums({
            "train": Counter(self.complete_counts(20)),
            "val": Counter(self.complete_counts(3)),
        })
        result = evaluate_readiness(counts, reviewed_pages=9, total_pages=10)
        self.assertFalse(result["ready"])
        self.assertEqual(result["unreviewed_pages"], 1)

    def test_blocks_missing_prime_and_roman_validation(self):
        counts = self.add_critical_minimums({
            "train": Counter(self.complete_counts(20)),
            "val": Counter(self.complete_counts(3)),
        })
        counts["train"]["prime"] = 0
        counts["val"]["V"] = 0
        result = evaluate_readiness(counts, reviewed_pages=10, total_pages=10)
        self.assertIn("prime", result["insufficient_total"])
        self.assertIn("V", result["insufficient_val"])

    def test_accepts_complete_balanced_real_dataset(self):
        counts = self.add_critical_minimums({
            "train": Counter(self.complete_counts(20)),
            "val": Counter(self.complete_counts(3)),
        })
        result = evaluate_readiness(counts, reviewed_pages=10, total_pages=10)
        self.assertTrue(result["ready"])


if __name__ == "__main__":
    unittest.main()
