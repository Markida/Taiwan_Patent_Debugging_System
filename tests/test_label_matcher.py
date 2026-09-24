import unittest

from features.patent_ocr.label_matcher import build_result_summary_html


class LabelMatcherTests(unittest.TestCase):
    def setUp(self):
        self.reference_items = [
            {"number": "1", "name": "第一零件"},
            {"number": "2", "name": "第二零件"},
        ]

    def test_global_differences_are_shown_before_image_sections(self):
        results = [
            {"image_name": "page_1.png", "numbers": ["1", "3", "3"]},
            {"image_name": "page_2.png", "numbers": ["1", "A"]},
        ]

        html = build_result_summary_html(results, self.reference_items)

        self.assertIn("標號清單有，但全部圖片都沒有出現：2", html)
        self.assertIn("有任意一張圖片出現，但標號清單沒有輸入：3, A", html)
        self.assertIn("<strong>All Pictures</strong>", html)
        self.assertLess(html.index("All Pictures"), html.index("page_1.png"))

    def test_single_picture_mode_omits_all_pictures_summary(self):
        html = build_result_summary_html(
            [{"image_name": "Pic_02", "numbers": ["2"]}],
            [{"number": "1"}, {"number": "2"}],
            include_global_summary=False,
        )

        self.assertNotIn("All Pictures", html)
        self.assertIn("<strong>Pic_02</strong>", html)
        self.assertIn("標號清單有，圖片沒有：1", html)

    def test_each_image_only_shows_its_differences(self):
        results = [
            {"image_name": "page_1.png", "numbers": ["1", "8"]},
            {"image_name": "page_2.png", "numbers": ["2", "9"]},
        ]

        html = build_result_summary_html(results, self.reference_items)

        self.assertIn("<strong>page_1.png</strong>", html)
        self.assertIn("<strong>page_2.png</strong>", html)
        self.assertIn("標號清單有，圖片沒有：2", html)
        self.assertIn("圖片有，標號清單沒有：8", html)
        self.assertIn("標號清單有，圖片沒有：1", html)
        self.assertIn("圖片有，標號清單沒有：9", html)
        self.assertNotIn("輸入標號數量", html)
        self.assertNotIn("有出現的標號", html)

    def test_no_reference_list_only_shows_detected_labels_per_image(self):
        results = [
            {"image_name": "page_1.png", "numbers": ["1", "A", "A"]},
            {"image_name": "page_2.png", "numbers": []},
        ]

        html = build_result_summary_html(results, [])

        self.assertIn("<strong>page_1.png</strong>", html)
        self.assertIn("偵測到的標號：1, A", html)
        self.assertIn("<strong>page_2.png</strong>", html)
        self.assertIn("偵測到的標號：無", html)
        self.assertNotIn("All Pictures", html)
        self.assertNotIn("標號清單有", html)

    def test_image_names_are_html_escaped(self):
        html = build_result_summary_html(
            [{"image_name": "page<1>&.png", "numbers": ["1"]}],
            [],
        )

        self.assertIn("<strong>page&lt;1&gt;&amp;.png</strong>", html)

    def test_optional_sort_compares_each_character_from_left_to_right(self):
        results = [{
            "image_name": "Pic_01",
            "numbers": ["34", "3", "121", "21", "1", "33", "131", "2"],
        }]

        original_html = build_result_summary_html(results, [])
        sorted_html = build_result_summary_html(
            results,
            [],
            sort_numbers=True,
        )

        self.assertIn(
            "偵測到的標號：34, 3, 121, 21, 1, 33, 131, 2",
            original_html,
        )
        self.assertIn(
            "偵測到的標號：1, 121, 131, 2, 21, 3, 33, 34",
            sorted_html,
        )

    def test_optional_sort_supports_letters_and_prime_after_digits(self):
        html = build_result_summary_html(
            [{
                "image_name": "Pic_01",
                "numbers": ["A", "7'", "70", "7", "10A", "10"],
            }],
            [],
            sort_numbers=True,
        )

        self.assertIn(
            "偵測到的標號：10, 10A, 7, 70, 7&#x27;, A",
            html,
        )

    def test_comparison_keeps_uppercase_and_lowercase_distinct(self):
        html = build_result_summary_html(
            [{"image_name": "Pic_01", "numbers": ["A", "a"]}],
            [{"number": "A"}],
        )

        self.assertIn("圖片有，標號清單沒有：a", html)


if __name__ == "__main__":
    unittest.main()
