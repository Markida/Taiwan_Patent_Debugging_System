import unittest

from features.patent_ocr.label_matcher import (
    IMAGE_RESULT_SEPARATOR,
    build_reference_comparison_text,
)


class LabelMatcherTests(unittest.TestCase):
    def setUp(self):
        self.reference_items = [
            {"number": "1", "name": "第一零件"},
            {"number": "2", "name": "第二零件"},
        ]

    def test_lists_labels_found_in_image_but_not_in_checklist(self):
        results = [
            {
                "image_name": "page_1.png",
                "numbers": ["1", "3", "3", "A"],
            }
        ]

        text = build_reference_comparison_text(results, self.reference_items)

        self.assertIn("有出現的標號：1（第一零件）", text)
        self.assertIn("未出現的標號：2（第二零件）", text)
        self.assertIn(
            "圖片中有出現，但是清單裡沒有出現的標號：3, A",
            text,
        )

    def test_shows_none_when_image_has_no_unlisted_labels(self):
        results = [
            {
                "image_name": "page_1.png",
                "numbers": ["1", "2"],
            }
        ]

        text = build_reference_comparison_text(results, self.reference_items)

        self.assertIn(
            "圖片中有出現，但是清單裡沒有出現的標號：無",
            text,
        )

    def test_unlisted_labels_are_calculated_per_image(self):
        results = [
            {"image_name": "page_1.png", "numbers": ["1", "8"]},
            {"image_name": "page_2.png", "numbers": ["2", "9"]},
        ]

        text = build_reference_comparison_text(results, self.reference_items)

        self.assertEqual(
            text.count("圖片中有出現，但是清單裡沒有出現的標號："),
            2,
        )
        self.assertIn("圖片中有出現，但是清單裡沒有出現的標號：8", text)
        self.assertIn("圖片中有出現，但是清單裡沒有出現的標號：9", text)
        self.assertEqual(text.count(IMAGE_RESULT_SEPARATOR), 1)

    def test_single_image_does_not_add_separator(self):
        results = [
            {"image_name": "page_1.png", "numbers": ["1"]},
        ]

        text = build_reference_comparison_text(results, self.reference_items)

        self.assertNotIn(IMAGE_RESULT_SEPARATOR, text)


if __name__ == "__main__":
    unittest.main()
