import sys
import unittest
from pathlib import Path

import numpy as np
import cv2


PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from features.patent_ocr.ocr_engine import (  # noqa: E402
    meets_character_confidence,
    count_roman_i_strokes,
    preprocess_roi_variants,
    recognize_easyocr_character,
    recognize_easyocr_label,
    select_easyocr_label_candidate,
)


class FakeReader:
    def __init__(self, recognized=None, detected=None):
        self.recognized = recognized or []
        self.detected = detected or []
        self.readtext_calls = 0

    def recognize(self, *_args, **_kwargs):
        return self.recognized

    def readtext(self, *_args, **_kwargs):
        self.readtext_calls += 1
        return self.detected


class EasyOcrCharacterTests(unittest.TestCase):
    def setUp(self):
        self.roi = np.full((40, 30), 255, dtype=np.uint8)

    def test_uses_full_roi_recognition_without_fallback(self):
        reader = FakeReader(recognized=[(None, "7", 0.91)])

        text, confidence = recognize_easyocr_character(
            reader,
            self.roi,
            min_confidence=0.20,
        )

        self.assertEqual(text, "7")
        self.assertAlmostEqual(confidence, 0.91)
        self.assertEqual(reader.readtext_calls, 0)

    def test_falls_back_when_full_roi_confidence_is_low(self):
        reader = FakeReader(
            recognized=[(None, "3", 0.10)],
            detected=[(None, "8", 0.82)],
        )

        text, confidence = recognize_easyocr_character(
            reader,
            self.roi,
            min_confidence=0.20,
        )

        self.assertEqual(text, "8")
        self.assertAlmostEqual(confidence, 0.82)
        self.assertEqual(reader.readtext_calls, 1)

    def test_applies_existing_digit_normalization(self):
        reader = FakeReader(recognized=[(None, "O", 0.88)])

        text, _confidence = recognize_easyocr_character(
            reader,
            self.roi,
            allowlist="0123456789",
        )

        self.assertEqual(text, "0")

    def test_preserves_letters_in_alphanumeric_mode(self):
        reader = FakeReader(recognized=[(None, "A", 0.94)])

        text, confidence = recognize_easyocr_character(
            reader,
            self.roi,
            allowlist="0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'",
        )

        self.assertEqual(text, "A")
        self.assertAlmostEqual(confidence, 0.94)

    def test_normalizes_prime_variants(self):
        reader = FakeReader(recognized=[(None, "′", 0.90)])

        text, _confidence = recognize_easyocr_character(
            reader,
            self.roi,
            allowlist="0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'",
        )

        self.assertEqual(text, "'")

    def test_recognizes_complete_group_label(self):
        reader = FakeReader(recognized=[(None, "55'", 0.97)])

        text, confidence = recognize_easyocr_label(reader, self.roi)

        self.assertEqual(text, "55'")
        self.assertAlmostEqual(confidence, 0.97)
        self.assertEqual(reader.readtext_calls, 0)

    def test_recognizes_roman_group(self):
        reader = FakeReader(recognized=[(None, "VIII", 0.91)])

        text, _confidence = recognize_easyocr_label(reader, self.roi)

        self.assertEqual(text, "VIII")

    def test_prefers_longer_pure_roman_candidate(self):
        text, _confidence = select_easyocr_label_candidate(
            [("VII", 0.62), ("VI", 0.70)]
        )

        self.assertEqual(text, "VII")

    def test_prefers_confident_roman_over_t_confusion(self):
        text, _confidence = select_easyocr_label_candidate(
            [("TII", 0.45), ("III", 0.84)]
        )

        self.assertEqual(text, "III")

    def test_counts_vertical_i_strokes_after_v(self):
        roi = np.full((60, 75, 3), 255, dtype=np.uint8)
        cv2.line(roi, (5, 5), (20, 52), (0, 0, 0), 3)
        cv2.line(roi, (35, 5), (20, 52), (0, 0, 0), 3)
        cv2.line(roi, (48, 5), (48, 52), (0, 0, 0), 4)
        cv2.line(roi, (62, 5), (62, 52), (0, 0, 0), 4)

        self.assertEqual(count_roman_i_strokes(roi), 2)

    def test_builds_raw_border_and_threshold_fallback(self):
        color_roi = np.full((20, 10, 3), 255, dtype=np.uint8)

        raw_border, thresholded = preprocess_roi_variants(color_roi)

        self.assertEqual(raw_border.shape, (140, 100))
        self.assertEqual(thresholded.shape, (80, 40))

    def test_rejects_low_confidence_parenthesis_like_digits(self):
        self.assertFalse(meets_character_confidence("6", 0.55))
        self.assertFalse(meets_character_confidence("7", 0.44))
        self.assertTrue(meets_character_confidence("6", 0.99))

    def test_preserves_normal_threshold_for_other_characters(self):
        self.assertTrue(meets_character_confidence("C", 0.44))
        self.assertFalse(meets_character_confidence("C", 0.19))


if __name__ == "__main__":
    unittest.main()
