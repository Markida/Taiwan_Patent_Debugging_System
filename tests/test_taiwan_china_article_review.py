from dataclasses import FrozenInstanceError
import unittest

from features.taiwan_china_spec.article_review import (
    ARTICLE_SECTIONS,
    ArticleOccurrence,
    find_article_occurrences,
    rebase_excluded_starts,
)


def utf16_slice(text, start, end):
    return text.encode("utf-16-le")[start * 2:end * 2].decode("utf-16-le")


class ArticleOccurrenceTests(unittest.TestCase):
    def test_section_labels_follow_preview_document_order(self):
        self.assertEqual(ARTICLE_SECTIONS, (
            ("abstract", "說明書摘要"),
            ("claims", "權利要求書"),
            ("specification", "說明書"),
            ("invention_content", "發明/實用新型內容"),
            ("drawing_description", "附圖說明"),
        ))

    def test_existing_positional_occurrence_arguments_keep_defaults(self):
        item = ArticleOccurrence(0, 2, 0, 5, 1, "一個元件。", "每一個")
        self.assertEqual(item.priority_reason, "每一個")
        self.assertEqual(item.section, "specification")

    def test_main_headings_group_repeated_occurrences_without_reordering(self):
        text = (
            "一個前言。\n說明書摘要\n一個元件和一個元件。\n"
            "權利要求書\n每一個元件和一個元件。\n"
            "說明書\n一個元件和一個元件。"
        )
        items = find_article_occurrences(text)
        self.assertEqual([item.section for item in items], [
            "specification", "abstract", "abstract", "claims", "claims",
            "specification", "specification",
        ])
        self.assertEqual([item.paragraph_index for item in items], [1, 3, 3, 5, 5, 7, 7])
        self.assertEqual([item.start for item in items], sorted(item.start for item in items))
        self.assertEqual(len({item.start for item in items}), 7)
        self.assertEqual(items[3].priority_reason, "每一個")
        for item in items:
            self.assertEqual(utf16_slice(text, item.start, item.end), "一個")

    def test_simplified_bracketed_spaced_headings_are_recognized(self):
        text = (
            "　【 说 明 书 摘 要 】　\r\n一個摘要。\r\n"
            "\t权 利 要 求 书\t\r\n一個請求。\r\n"
            "【說 明 書】\r\n一個說明。"
        )
        self.assertEqual(
            [item.section for item in find_article_occurrences(text)],
            ["abstract", "claims", "specification"],
        )
        for heading, section in (
            ("【說明書摘要】", "abstract"), ("【權利要求書】", "claims"),
            ("【说明书】", "specification"),
        ):
            with self.subTest(heading=heading):
                self.assertEqual(
                    find_article_occurrences(heading + "\n一個元件。")[0].section,
                    section,
                )

    def test_main_heading_mentions_in_prose_do_not_switch_sections(self):
        text = (
            "權利要求書\n一個元件記載於說明書摘要。\n"
            "【說明書】中的一個段落。\n"
            "附錄說明書\n一個附錄。\n"
            "說明書摘要：一個摘要。\n"
            "【說明書\n一個未閉合標題。\n"
            "說明書】\n一個未開始標題。"
        )
        items = find_article_occurrences(text)
        self.assertEqual(len(items), 6)
        self.assertEqual([item.section for item in items], ["claims"] * 6)

    def test_drawing_description_has_its_own_section_then_returns_to_specification(self):
        headings = (
            "技術領域", "背景技術", "發明內容", "實用新型內容",
            "附圖說明", "具體實施方式", "【符號說明】", "实施例",
        )
        text = "說明書\n" + "\n".join(heading + "\n一個元件。" for heading in headings)
        self.assertEqual(
            [item.section for item in find_article_occurrences(text)],
            ["specification"] * 2 + ["invention_content"] * 2
            + ["drawing_description"]
            + ["specification"] * 3,
        )

    def test_simplified_and_bracketed_drawing_headings_are_recognized(self):
        for heading in ("附圖說明", "附图说明", "【 附 圖 說 明 】"):
            with self.subTest(heading=heading):
                text = f"說明書\n{heading}\n一個圖式。\n具體實施方式\n一個實施例。"
                self.assertEqual(
                    [item.section for item in find_article_occurrences(text)],
                    ["drawing_description", "specification"],
                )

    def test_content_heading_variants_partition_only_the_content_section(self):
        for heading in (
            "發明內容", "发明内容", "新型內容", "新型内容",
            "實用新型內容", "实用新型内容", "【 實 用 新 型 內 容 】",
        ):
            with self.subTest(heading=heading):
                text = (
                    f"背景技術\n一個背景。\n{heading}\n"
                    "😀每一個元件。\n一個說明發明內容的元件。\n"
                    "附圖說明\n一個附圖。\n具體實施方式\n一個實施例。"
                )
                hits = find_article_occurrences(text)
                self.assertEqual([hit.section for hit in hits], [
                    "specification", "invention_content", "invention_content",
                    "drawing_description", "specification",
                ])
                self.assertEqual(hits[1].priority_reason, "每一個")
                for hit in hits:
                    self.assertEqual(utf16_slice(text, hit.start, hit.end), "一個")

    def test_drawing_heading_reference_in_prose_does_not_change_section(self):
        text = "說明書\n附圖說明：一個圖式。\n一個實施例。"
        self.assertEqual(
            [item.section for item in find_article_occurrences(text)],
            ["specification", "specification"],
        )

    def test_no_heading_preserves_every_occurrence_in_default_section(self):
        items = find_article_occurrences("一個元件。\n【背景技術】\n每一個模組。")
        self.assertEqual([item.section for item in items], ["specification"] * 2)

    def test_grouped_headings_keep_emoji_utf16_positions_and_priority(self):
        text = (
            "😀一個前言。\n【說明書摘要】\n𠮷其中另一個元件。\n"
            "权利要求书\n😀至少一個模組。\n"
            "說明書\n一個" + "😀" * 10 + "空間。"
        )
        items = find_article_occurrences(text)
        self.assertEqual([item.section for item in items], [
            "specification", "abstract", "claims", "specification",
        ])
        self.assertEqual([item.priority_reason for item in items], [
            "", "其中另一個", "至少一個", "10字內接「空間」",
        ])
        self.assertEqual(items[0].start, 2)
        for item in items:
            self.assertEqual(utf16_slice(text, item.start, item.end), "一個")
            self.assertEqual(
                utf16_slice(text, item.sentence_start, item.sentence_end), item.sentence,
            )

    def test_empty_and_nonmatching_text(self):
        self.assertEqual(find_article_occurrences(""), [])
        self.assertEqual(find_article_occurrences("一个模組及一件元件"), [])

    def test_multiple_occurrences_share_sentence_but_keep_distinct_positions(self):
        text = "  一個模組，一個元件；以及一個外殼。另一個裝置！"
        items = find_article_occurrences(text)
        self.assertEqual(len(items), 4)
        self.assertEqual([item.start for item in items], [2, 7, 14, 20])
        self.assertEqual(items[0].sentence, "一個模組，一個元件；以及一個外殼。")
        self.assertEqual(items[0].sentence, items[1].sentence)
        self.assertEqual(items[1].sentence, items[2].sentence)
        self.assertEqual(items[3].sentence, "另一個裝置！")
        self.assertEqual([item.paragraph_index for item in items], [1] * 4)
        for item in items:
            self.assertEqual(utf16_slice(text, item.start, item.end), "一個")
            self.assertEqual(
                utf16_slice(text, item.sentence_start, item.sentence_end),
                item.sentence,
            )

    def test_sentence_boundaries_and_trailing_whitespace(self):
        text = "前言。 一個甲?一個乙!一個丙？一個丁！\n\t一個戊  "
        items = find_article_occurrences(text)
        self.assertEqual(
            [item.sentence for item in items],
            ["一個甲?", "一個乙!", "一個丙？", "一個丁！", "一個戊"],
        )
        self.assertEqual([item.paragraph_index for item in items], [1, 1, 1, 1, 2])

    def test_paragraph_numbering_includes_empty_and_windows_line_endings(self):
        text = "\n一個甲\r\n\r\n一個乙\r一個丙"
        items = find_article_occurrences(text)
        self.assertEqual([item.paragraph_index for item in items], [2, 4, 5])
        self.assertEqual([item.sentence for item in items], ["一個甲", "一個乙", "一個丙"])

    def test_supplementary_characters_use_qt_utf16_offsets(self):
        text = "😀一個𠮷零件。\n𠀀一個模組"
        first, second = find_article_occurrences(text)
        self.assertEqual((first.start, first.end), (2, 4))
        self.assertEqual((first.sentence_start, first.sentence_end), (0, 9))
        self.assertEqual((second.start, second.end), (12, 14))
        self.assertEqual((second.sentence_start, second.sentence_end), (10, 16))
        for item in (first, second):
            self.assertEqual(utf16_slice(text, item.start, item.end), "一個")
            self.assertEqual(
                utf16_slice(text, item.sentence_start, item.sentence_end), item.sentence
            )

    def test_occurrences_are_immutable(self):
        item = find_article_occurrences("一個元件")[0]
        with self.assertRaises(FrozenInstanceError):
            item.start = 99

    def test_priority_prefixes_do_not_reorder_source_items(self):
        items = find_article_occurrences("一個甲。每一個乙。各一個丙。上一個丁。下一個戊。")
        self.assertEqual(
            [item.priority_reason for item in items],
            ["", "每一個", "各一個", "上一個", "下一個"],
        )
        self.assertEqual([item.is_priority for item in items], [False, True, True, True, True])
        self.assertEqual([item.start for item in items], sorted(item.start for item in items))

    def test_space_priority_counts_zero_through_ten_intervening_characters(self):
        for count in (0, 1, 9, 10):
            with self.subTest(count=count):
                item = find_article_occurrences("一個" + "甲" * count + "空間")[0]
                self.assertTrue(item.is_priority)
        item = find_article_occurrences("一個" + "甲" * 11 + "空間")[0]
        self.assertFalse(item.is_priority)

    def test_additional_priority_phrases_keep_full_labels_and_source_order(self):
        text = "一個甲。其中一個乙。另一個丙。其中另一個丁。每一個戊。各一個己。上一個庚。下一個辛。至少一個壬。第一個癸。"
        items = find_article_occurrences(text)
        self.assertEqual(
            [item.priority_reason for item in items],
            [
                "", "其中一個", "另一個", "其中另一個", "每一個", "各一個",
                "上一個", "下一個", "至少一個", "第一個",
            ],
        )
        self.assertEqual([item.is_priority for item in items], [False] + [True] * 9)
        self.assertEqual([item.start for item in items], sorted(item.start for item in items))
        for item in items:
            self.assertEqual(utf16_slice(text, item.start, item.end), "一個")

    def test_longest_phrase_wins_over_short_suffix_and_space_priority(self):
        for phrase in ("其中另一個", "其中一個", "另一個", "至少一個", "第一個"):
            with self.subTest(phrase=phrase):
                item = find_article_occurrences(phrase + "小空間")[0]
                self.assertEqual(item.priority_reason, phrase)

    def test_additional_priority_phrases_require_contiguous_text(self):
        for text in (
            "其中 一個甲", "另 一個甲", "其中另 一個甲", "其中\n一個甲",
            "至少 一個甲", "第 一個甲",
        ):
            with self.subTest(text=text):
                item = find_article_occurrences(text)[0]
                self.assertFalse(item.is_priority)
        item = find_article_occurrences("其中\n另一個甲")[0]
        self.assertEqual(item.priority_reason, "另一個")

    def test_additional_priority_phrases_preserve_utf16_positions_after_emoji(self):
        text = "😀其中另一個甲。𠮷其中一個乙。😀另一個丙。"
        items = find_article_occurrences(text)
        self.assertEqual(
            [item.priority_reason for item in items],
            ["其中另一個", "其中一個", "另一個"],
        )
        self.assertEqual(items[0].start, 5)
        for item in items:
            self.assertEqual(utf16_slice(text, item.start, item.end), "一個")

    def test_at_least_and_first_are_priority_without_any_following_space(self):
        text = "至少一個感測元件與第一個處理模組，以及一個普通元件。"
        items = find_article_occurrences(text)
        self.assertEqual(
            [item.priority_reason for item in items], ["至少一個", "第一個", ""]
        )
        self.assertEqual([item.is_priority for item in items], [True, True, False])

    def test_space_priority_counts_supplementary_characters_once(self):
        item = find_article_occurrences("一個" + "😀" * 10 + "空間")[0]
        self.assertTrue(item.is_priority)

    def test_space_priority_accepts_simplified_target_but_not_article(self):
        item = find_article_occurrences("一個小空间")[0]
        self.assertTrue(item.is_priority)
        self.assertIn("空间", item.priority_reason)
        self.assertEqual(find_article_occurrences("一个空间"), [])

    def test_space_priority_does_not_cross_paragraph_boundary(self):
        for separator in ("\n", "\r\n", "\r"):
            with self.subTest(separator=separator):
                item = find_article_occurrences("一個" + separator + "空間")[0]
                self.assertFalse(item.is_priority)

    def test_space_priority_can_cross_sentence_within_same_paragraph(self):
        item = find_article_occurrences("一個元件。空間配置如下。")[0]
        self.assertTrue(item.is_priority)

    def test_prefix_priority_wins_over_space_priority(self):
        item = find_article_occurrences("每一個空間")[0]
        self.assertEqual(item.priority_reason, "每一個")


class RebaseExcludedStartsTests(unittest.TestCase):
    def test_no_op_preserves_every_exclusion(self):
        self.assertEqual(rebase_excluded_starts({2, 8}, 3, 0, 0), {2, 8})

    def test_insertion_before_and_exactly_at_start_shifts(self):
        self.assertEqual(rebase_excluded_starts({2, 8}, 0, 0, 3), {5, 11})
        self.assertEqual(rebase_excluded_starts({2, 8}, 2, 0, 3), {5, 11})

    def test_insertion_inside_drops_but_end_leaves_token_unchanged(self):
        self.assertEqual(rebase_excluded_starts({2, 8}, 3, 0, 1), {9})
        self.assertEqual(rebase_excluded_starts({2, 8}, 4, 0, 1), {2, 9})

    def test_deletion_before_shifts_and_after_does_not(self):
        self.assertEqual(rebase_excluded_starts({2, 8}, 0, 2, 0), {0, 6})
        self.assertEqual(rebase_excluded_starts({2, 8}, 10, 5, 0), {2, 8})

    def test_deletion_touching_start_or_end_is_not_overlap(self):
        self.assertEqual(rebase_excluded_starts({2, 8}, 0, 2, 0), {0, 6})
        self.assertEqual(rebase_excluded_starts({2, 8}, 4, 4, 0), {2, 4})

    def test_deletion_overlaps_either_character_drops_exclusion(self):
        for position, length in ((1, 2), (2, 1), (3, 1), (2, 2), (0, 5)):
            with self.subTest(position=position, length=length):
                self.assertEqual(
                    rebase_excluded_starts({2, 8}, position, length, 0),
                    {8 - length},
                )

    def test_replacement_before_applies_net_shift(self):
        self.assertEqual(rebase_excluded_starts({2, 8}, 0, 2, 5), {5, 11})

    def test_replacement_overlap_drops_even_when_length_unchanged(self):
        self.assertEqual(rebase_excluded_starts({2, 8}, 2, 2, 2), {8})

    def test_replacement_spanning_all_tokens_clears_exclusions(self):
        self.assertEqual(rebase_excluded_starts({2, 8}, 0, 10, 6), set())

    def test_utf16_insertions_and_deletions_of_emoji(self):
        self.assertEqual(rebase_excluded_starts({2, 8}, 0, 0, 2), {4, 10})
        self.assertEqual(rebase_excluded_starts({2, 8}, 0, 2, 0), {0, 6})

    def test_iterable_input_and_invalid_edits(self):
        self.assertEqual(rebase_excluded_starts(iter([2, 8]), 0, 0, 1), {3, 9})
        for edit in ((-1, 0, 0), (0, -1, 0), (0, 0, -1)):
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                rebase_excluded_starts({2}, *edit)


if __name__ == "__main__":
    unittest.main()
