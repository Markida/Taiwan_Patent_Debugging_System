import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TRAINING_DIR = PROJECT_ROOT / "training" / "char_yolo"

for path in (PROJECT_ROOT, TRAINING_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from training.char_yolo.char_classes import (  # noqa: E402
    CLASS_NAMES,
    PRIME_CLASS_NAME,
    normalize_character,
)
from features.patent_ocr.ocr_engine import group_chars_to_labels  # noqa: E402
from features.patent_ocr.label_parser import (  # noqa: E402
    normalize_label_text,
    parse_reference_items,
)


def char_item(character, x1, y1, x2, y2):
    return {
        "char": character,
        "x1": x1,
        "y1": y1,
        "x2": x2,
        "y2": y2,
        "xc": (x1 + x2) / 2,
        "yc": (y1 + y2) / 2,
    }


class CharacterClassTests(unittest.TestCase):
    def test_has_expected_37_classes(self):
        self.assertEqual(len(CLASS_NAMES), 37)
        self.assertEqual(CLASS_NAMES[:10], tuple("0123456789"))
        self.assertEqual(CLASS_NAMES[-1], PRIME_CLASS_NAME)

    def test_normalizes_prime_variants(self):
        for mark in ("'", "’", "′", "`"):
            self.assertEqual(normalize_character(mark), PRIME_CLASS_NAME)


class CharacterGroupingTests(unittest.TestCase):
    def test_prime_is_kept_as_alphanumeric_suffix(self):
        self.assertEqual(normalize_label_text("55'"), "55'")
        self.assertEqual(normalize_label_text("'42"), "42")
        for label in ("A'", "B'", "C'", "S1'", "a'", "b'", "c'"):
            self.assertEqual(normalize_label_text(label), label)

    def test_reference_list_accepts_letter_and_mixed_prime_labels(self):
        items = parse_reference_items(
            "3':第三元件\n17′:第十七元件\nA’:大寫\nS1':混合\na':小寫"
        )
        self.assertEqual(
            [item["number"] for item in items],
            ["3'", "17'", "A'", "S1'", "a'"],
        )

    def test_preserves_uppercase_and_lowercase_as_distinct_labels(self):
        self.assertEqual(normalize_label_text("10A"), "10A")
        self.assertEqual(normalize_label_text("10a"), "10a")

    def test_groups_number_and_letter(self):
        items = [
            char_item("1", 10, 20, 20, 40),
            char_item("0", 22, 20, 34, 40),
            char_item("A", 36, 20, 50, 40),
        ]
        results = group_chars_to_labels(items)
        self.assertEqual([item["label"] for item in results], ["10A"])

    def test_groups_lowercase_without_promoting_it_to_uppercase(self):
        items = [
            char_item("1", 10, 20, 20, 40),
            char_item("0", 22, 20, 34, 40),
            char_item("a", 36, 20, 50, 40),
        ]
        results = group_chars_to_labels(items)
        self.assertEqual([item["label"] for item in results], ["10a"])

    def test_attaches_prime_to_nearest_digit(self):
        items = [
            char_item("7", 10, 20, 24, 44),
            char_item("'", 23, 10, 29, 22),
        ]
        results = group_chars_to_labels(items)
        self.assertEqual([item["label"] for item in results], ["7'"])

    def test_attaches_prime_to_letter_and_mixed_labels(self):
        letter_items = [
            char_item("A", 10, 20, 28, 44),
            char_item("'", 27, 10, 33, 22),
        ]
        mixed_items = [
            char_item("S", 10, 20, 28, 44),
            char_item("1", 30, 20, 42, 44),
            char_item("'", 41, 10, 47, 22),
        ]

        self.assertEqual(
            [item["label"] for item in group_chars_to_labels(letter_items)],
            ["A'"],
        )
        self.assertEqual(
            [item["label"] for item in group_chars_to_labels(mixed_items)],
            ["S1'"],
        )

    def test_supports_multiple_prime_labels(self):
        items = [
            char_item("8", 10, 20, 24, 44),
            char_item("'", 23, 10, 29, 22),
            char_item("9", 90, 20, 104, 44),
            char_item("'", 103, 10, 109, 22),
        ]
        results = group_chars_to_labels(items, max_x_gap=30)
        self.assertEqual([item["label"] for item in results], ["8'", "9'"])

    def test_splits_groups_separated_by_an_undetected_parenthesis(self):
        items = [
            char_item("3", 10, 21, 24, 45),
            char_item("3", 23, 21, 37, 45),
            char_item("1", 36, 21, 50, 45),
            char_item("3", 72, 20, 86, 44),
            char_item("3", 85, 20, 99, 44),
        ]
        results = group_chars_to_labels(items, max_x_gap=15)
        self.assertEqual([item["label"] for item in results], ["331", "33"])

    def test_adaptive_gap_keeps_tall_211_together(self):
        items = [
            char_item("2", 10, 20, 32, 82),
            char_item("1", 48, 21, 62, 83),
            char_item("1", 79, 19, 93, 81),
        ]
        results = group_chars_to_labels(items, max_x_gap=15)
        self.assertEqual([item["label"] for item in results], ["211"])

    def test_adaptive_gap_and_row_alignment_keep_151_together(self):
        items = [
            char_item("1", 10, 20, 24, 82),
            char_item("5", 41, 27, 65, 88),
            char_item("1", 82, 18, 96, 80),
        ]
        results = group_chars_to_labels(items, max_x_gap=15)
        self.assertEqual([item["label"] for item in results], ["151"])

    def test_adaptive_gap_does_not_cross_parentheses_in_211_21(self):
        items = [
            char_item("2", 10, 20, 32, 82),
            char_item("1", 34, 20, 48, 82),
            char_item("1", 50, 20, 64, 82),
            # The omitted parentheses leave a wider horizontal gap.
            char_item("2", 86, 20, 108, 82),
            char_item("1", 110, 20, 124, 82),
        ]
        results = group_chars_to_labels(items, max_x_gap=15)
        self.assertEqual([item["label"] for item in results], ["211", "21"])


if __name__ == "__main__":
    unittest.main()
