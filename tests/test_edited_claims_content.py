"""Explicit claims-to-content replacement works from the edited preview only."""

import tempfile
import unittest
import zipfile
import os
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

from features.taiwan_china_spec import (
    ConversionError,
    EditedClaimsContentReplacement,
    replace_content_from_edited_claims,
)
from features.taiwan_china_spec import converter


SOURCE = Path(__file__).resolve().parent / "fixtures" / "TW_方向校正專利圖式標號檢核系統_發明說明書.docx"


def edited_preview(claims=None, content=None, kind="invention"):
    return "\n".join([
        "說明書摘要", "  人工摘要😀  A B。", "權利要求書",
        claims if claims is not None else (
            "1. 一種新感測裝置，包括所述感測器與處理器。\n"
            "2. 根據權利要求1所述的感測裝置，其特徵在於：所述處理器具有新記憶體。"
        ),
        "說明書", "人工案名😀", "技術領域", " 技術領域的  A B 手動編輯。",
        "背景技術", "背景的新文字。",
        "發明內容" if kind == "invention" else "實用新型內容",
        content if content is not None else (
            "本發明的目的在於提供舊裝置及已刪除的雷射。\n"
            "本發明的有益效果在於保留舊規格。"
        ),
        "附圖說明", "圖1：舊圖說保留，不改\t格式。", "具體實施方式",
        "實施方式的手動編輯維持  two  spaces。", "",
    ])


class EditedClaimsContentTests(unittest.TestCase):
    def test_button_updates_only_claim_block_between_source_intro_and_benefit(self):
        previous_claims = (
            "一種舊滑鼠組，並包含舊鼠標及舊台座，所述舊鼠標包括舊外殼。",
            "根據權利要求1所述的舊滑鼠組，其特徵在於：所述舊外殼具有舊電池。",
        )
        purpose = "本發明的目的在於提供一種可調回報率的輕量化無線滑鼠組。"
        mechanism = "本發明的無線滑鼠組並包含鼠標、台座、兩電池盒及電池。"
        benefit = "本發明的有益效果在於：降低耗電。"
        original = edited_preview(
            claims=(
                "1. 一種新滑鼠組，並包含鼠標、台座、兩電池盒及電池，"
                "所述鼠標包括新外殼。\n"
                "2. 根據權利要求1所述的新滑鼠組，其特徵在於："
                "所述新外殼具有新電池。"
            ),
            content="\n".join([
                purpose,
                mechanism,
                "本發明的舊滑鼠組，並包含舊鼠標及舊台座，"
                "所述舊鼠標包括人工修訂過的舊外殼。",
                "本發明的舊滑鼠組，所述舊外殼具有舊電池。",
                benefit,
            ]),
        )

        result = replace_content_from_edited_claims(
            original, "invention", previous_claims=previous_claims,
            fixed_content_layout=True,
        )
        content = converter.parse_edited_preview_text(
            result.text, "invention"
        ).invention_content
        self.assertEqual(content, [
            purpose,
            mechanism,
            "所述鼠標包括新外殼。",
            "本發明的新滑鼠組，所述新外殼具有新電池。",
            benefit,
        ])
        self.assertNotIn("舊鼠標", result.text)
        self.assertNotIn("人工修訂過的舊外殼", result.text)
        self.assertNotIn("有益效果", result.replacement_text)

    def test_revised_claims_replace_only_previous_claim_copies_and_preserve_other_content(self):
        previous_claims = (
            "一種舊裝置，其特徵在於：包括舊感測器。",
            "根據權利要求1所述的舊裝置，其特徵在於：，所述舊感測器具有舊記憶體。",
        )
        content = (
            "人工補充的前言，必須原樣保留。\n"
            "本發明的目的在於提供一種舊裝置，其特徵在於：包括舊感測器。\n"
            "本發明的舊裝置，，所述舊感測器具有舊記憶體。\n"
            "本發明的有益效果在於：降低人工操作時間，且保留  A B  空白。\n"
            "人工補充的尾段也不得刪除。"
        )
        current_claims = (
            "1. 一種新裝置，其特徵在於：包括新感測器。\n"
            "2. 根據權利要求1所述的新裝置，其特徵在於：，所述新感測器具有新記憶體。"
        )
        original = edited_preview(current_claims, content)

        result = replace_content_from_edited_claims(
            original, "invention", previous_claims=previous_claims,
        )

        self.assertNotIn("舊感測器", result.text)
        self.assertIn("本發明的目的在於提供一種新裝置，包括新感測器。", result.text)
        self.assertIn("本發明的新裝置，所述新感測器具有新記憶體。", result.text)
        self.assertNotIn("其特徵在於：，", result.replacement_text)
        self.assertNotIn("，，", result.replacement_text)
        self.assertIn("人工補充的前言，必須原樣保留。", result.text)
        self.assertIn("本發明的有益效果在於：降低人工操作時間，且保留  A B  空白。", result.text)
        self.assertIn("人工補充的尾段也不得刪除。", result.text)

    def test_claim_update_with_matching_count_does_not_edit_preserved_lines(self):
        previous = (
            "一種舊裝置，包括舊元件。",
            "如請求項1所述之舊裝置，其中所述舊元件可移動。",
        )
        content = (
            "本發明的目的在於提供一種舊裝置，包括舊元件。\n"
            "本發明的舊裝置，所述舊元件可移動。\n"
            "本發明的有益效果在於：這一行不得被替換。"
        )
        original = edited_preview(
            "1. 一種新裝置，包括新元件。\n"
            "2. 如請求項1所述之新裝置，其中所述新元件可移動。",
            content,
        )
        result = replace_content_from_edited_claims(
            original, "invention", previous_claims=previous,
        )
        benefit = "本發明的有益效果在於：這一行不得被替換。\n"
        benefit_start = original.index(benefit)
        self.assertFalse(any(start <= benefit_start < end for start, end, _value in result.edits))
        self.assertEqual(original[benefit_start:benefit_start + len(benefit)], benefit)
        self.assertIn(benefit, result.text)

    def test_adds_current_claims_without_replacing_unrelated_content_or_benefit(self):
        original = edited_preview()
        result = replace_content_from_edited_claims(original, "invention")
        self.assertIsInstance(result, EditedClaimsContentReplacement)
        self.assertEqual(result.claim_count, 2)
        self.assertEqual(result.warnings, ())
        self.assertEqual(result.text[:result.content_start], original[:result.content_start])
        self.assertEqual(result.text[result.content_start + len(result.replacement_text):], original[result.content_end:])
        self.assertEqual(result.edits, ((result.content_start, result.content_end, result.replacement_text),))
        self.assertEqual(result.replacement_text, (
            "本發明的新感測裝置，包括所述感測器與處理器。\n"
            "本發明的感測裝置，所述處理器具有新記憶體。\n"
        ))
        self.assertIn("已刪除的雷射", result.text)
        self.assertIn("本發明的有益效果在於保留舊規格。", result.text)
        self.assertNotIn("有益效果", result.replacement_text)
        self.assertNotIn("1. ", result.replacement_text)
        self.assertNotIn("2. ", result.replacement_text)

    def test_deleting_claim_removes_only_old_claim_narratives_from_baseline(self):
        original = edited_preview(
            claims="1. 一種修改後裝置，包括新插槽。",
            content="本發明的裝置，包括舊插槽。\n本發明的方法，具有已刪除步驟。",
        )
        result = replace_content_from_edited_claims(
            original, "invention", previous_claims=(
                "一種裝置，包括舊插槽。",
                "一種方法，具有已刪除步驟。",
            ),
        )
        self.assertEqual(result.claim_count, 1)
        self.assertNotIn("舊插槽", result.text)
        self.assertNotIn("已刪除步驟", result.text)
        self.assertIn("新插槽", result.replacement_text)

    def test_multiple_independent_subjects_use_existing_narrative_rules(self):
        claims = (
            "1. 一種裝置，其特徵在於：包括電池。\n"
            "2. 如請求項1所述之裝置，其中電池可拆卸。\n"
            "3. 一種測量方法，包括取得資料。\n"
            "4. 根據權利要求3所述的測量方法，其特徵在於：資料經濾波。"
        )
        result = replace_content_from_edited_claims(edited_preview(claims), "invention")
        self.assertEqual(result.replacement_text.splitlines(), [
            "本發明的裝置，包括電池。",
            "本發明的裝置，電池可拆卸。",
            "本發明的測量方法，包括取得資料。",
            "本發明的測量方法，資料經濾波。",
        ])

    def test_utility_model_uses_correct_owner_and_preserves_heading(self):
        result = replace_content_from_edited_claims(edited_preview(kind="utility_model"), "utility_model")
        self.assertIn("實用新型內容\n", result.text)
        self.assertTrue(result.replacement_text.startswith("本實用新型的新感測裝置"))
        self.assertIn("本實用新型的感測裝置，", result.replacement_text)
        self.assertNotIn("本發明", result.replacement_text)

    def test_multiline_claims_preserve_decimal_tokens_and_technical_spacing(self):
        claims = (
            "1. 一種裝置，包括：\n"
            "1.5 V電源；\nA  B端、1,000次計數及C:D介面。\n"
            "2. 根據權利要求1所述的裝置，其中所述電源\n具有備援電池。"
        )
        original = edited_preview(claims)
        parsed = converter.parse_edited_preview_text(original, "invention")
        self.assertEqual(len(parsed.claims), 2)
        self.assertIn("1.5 V", parsed.claims[0])
        result = replace_content_from_edited_claims(original, "invention")
        self.assertEqual(result.claim_count, 2)
        self.assertIn(claims, result.text)
        self.assertIn("1.5 V電源", result.replacement_text)
        self.assertIn("A  B端、1,000次計數及C:D介面", result.replacement_text)
        self.assertNotIn("其特徵在於", result.replacement_text)

    def test_fullwidth_numbering_is_removed_once_from_content_not_claims(self):
        claims = "１． 一種裝置，包括元件。\n２、 如請求項1所述之裝置，其中元件具有外殼。"
        result = replace_content_from_edited_claims(edited_preview(claims), "invention")
        self.assertEqual(result.claim_count, 2)
        self.assertIn(claims, result.text)
        self.assertNotIn("１．", result.replacement_text)
        self.assertNotIn("２、", result.replacement_text)

    def test_number_on_own_line_is_grouped_with_its_body(self):
        claims = "1.\n一種裝置，包括元件。\n2.\n如請求項1所述之裝置，其中元件具有外殼。"
        result = replace_content_from_edited_claims(edited_preview(claims), "invention")
        self.assertEqual(result.claim_count, 2)
        self.assertIn("元件具有外殼。", result.replacement_text)

    def test_no_terminology_or_character_conversion_is_rerun(self):
        original = edited_preview("1. 一種甲裝置，包括乙乙元件及所述处理器。")
        with patch.object(converter, "_convert_text", side_effect=AssertionError("no dictionary")), \
                patch.object(converter, "_windows_simplified_chinese", side_effect=AssertionError("no remapping")), \
                patch.object(converter, "_windows_traditional_chinese", side_effect=AssertionError("no remapping")):
            result = replace_content_from_edited_claims(original, "invention")
        self.assertIn("乙乙元件及所述处理器。", result.replacement_text)

    @unittest.skipUnless(os.name == "nt", "LCMapStringEx is Windows-only")
    def test_windows_character_mapping_preserves_supplementary_characters(self):
        for original in ("發明😀及𠀀裝置", "😀發明𠀀", "發明😀", "𠀀😀"):
            with self.subTest(original=original):
                simplified = converter._windows_simplified_chinese(original)
                traditional = converter._windows_traditional_chinese(simplified)
                self.assertEqual(traditional, original)
                self.assertEqual(len(simplified), len(original))
                self.assertEqual(simplified.count("😀"), original.count("😀"))
                self.assertEqual(simplified.count("𠀀"), original.count("𠀀"))
                simplified.encode("utf-16-le")
                traditional.encode("utf-16-le")

    def test_empty_original_content_can_be_replaced(self):
        result = replace_content_from_edited_claims(edited_preview(content=""), "invention")
        self.assertEqual(result.claim_count, 2)
        self.assertIn("\n附圖說明\n", result.text)

    def test_crlf_and_supplementary_characters_keep_exact_original_offsets(self):
        original = edited_preview().replace("\n", "\r\n")
        result = replace_content_from_edited_claims(original, "invention")
        self.assertEqual(original[result.content_start:result.content_end], "")
        self.assertIn("本發明的目的在於提供舊裝置及已刪除的雷射。\r\n", result.text)
        self.assertIn("本發明的有益效果在於保留舊規格。\r\n", result.text)
        self.assertEqual(result.replacement_text.count("\r\n"), 2)
        self.assertEqual(result.text[:result.content_start], original[:result.content_start])

    def test_repeating_same_update_is_idempotent(self):
        first = replace_content_from_edited_claims(edited_preview(), "invention")
        second = replace_content_from_edited_claims(first.text, "invention")
        self.assertEqual(first.text, second.text)

    def test_missing_duplicate_or_reordered_sections_fail_without_result(self):
        original = edited_preview()
        cases = (
            original.replace("權利要求書\n", ""),
            original.replace("發明內容\n", ""),
            original.replace("附圖說明\n", ""),
            original.replace("背景技術\n", "發明內容\n背景技術\n"),
            original.replace("技術領域", "ZZZ").replace("背景技術", "技術領域").replace("ZZZ", "背景技術"),
        )
        for invalid in cases:
            with self.subTest(preview=invalid):
                snapshot = invalid
                with self.assertRaises(ConversionError):
                    replace_content_from_edited_claims(invalid, "invention")
                self.assertEqual(invalid, snapshot)

    def test_malformed_claim_numbers_and_ambiguous_numeric_lists_fail(self):
        cases = (
            "", "一種未編號裝置，包括元件。", "1) 一種裝置，包括元件。",
            "2. 一種裝置，包括元件。",
            "1. 一種裝置，包括元件。\n1. 一種裝置，包括另一元件。",
            "1. 一種裝置，包括元件。\n3. 一種裝置，包括另一元件。",
            "1.\n2. 一種裝置，包括元件。",
            "1. 一種裝置，包括元件。\n2.",
            "1. 一種裝置，包括元件。\n未編號的另一項，包括另一元件。",
            "1. 一種裝置，包括：\n2. V通道；\n3. 電池。",
            "1. 一種尚未完成裝置，包括元件",
        )
        for claims in cases:
            with self.subTest(claims=claims):
                with self.assertRaises(ConversionError):
                    replace_content_from_edited_claims(edited_preview(claims), "invention")

    def test_unrecognized_claim_openings_and_kind_fail_safely(self):
        for claims in (
            "1. 沒有標的分隔的文字。",
            "1. 一種裝置，包括元件。\n2. 依前述請求項一之裝置，其中元件不可移除。",
        ):
            with self.subTest(claims=claims):
                with self.assertRaises(ConversionError):
                    replace_content_from_edited_claims(edited_preview(claims), "invention")
        with self.assertRaises(ConversionError):
            replace_content_from_edited_claims(edited_preview(), "unknown")

    def test_legacy_unnumbered_preview_claims_remain_supported_by_export_parser(self):
        parsed = converter.parse_edited_preview_text(edited_preview(
            "一種裝置，包括元件。\n如請求項1所述之裝置，其中元件具有1.5 V電源。"
        ), "invention")
        self.assertEqual(len(parsed.claims), 2)
        self.assertIn("1.5 V", parsed.claims[1])

    def test_numbered_description_returns_separate_downstream_number_edits(self):
        for opening, closing in (("【", "】"), ("[", "]")):
            with self.subTest(style=opening):
                original = edited_preview(content="舊內容。")
                for number, body in enumerate((
                    " 技術領域的  A B 手動編輯。", "背景的新文字。", "舊內容。",
                    "圖1：舊圖說保留，不改\t格式。", "實施方式的手動編輯維持  two  spaces。",
                ), 1):
                    original = original.replace(body, f"{opening}{number:04d}{closing} " + body)
                result = replace_content_from_edited_claims(original, "invention")
                self.assertEqual(len(result.edits), 4)
                self.assertTrue(result.replacement_text.startswith(f"{opening}0003{closing} "))
                self.assertIn(f"{opening}0004{closing} 本發明的感測裝置", result.replacement_text)
                self.assertIn(f"{opening}0005{closing} 舊內容。", result.text)
                self.assertIn(f"{opening}0006{closing} 圖1：舊圖說保留，不改\t格式。", result.text)
                self.assertIn(f"{opening}0007{closing} 實施方式的手動編輯維持  two  spaces。", result.text)
                reconstructed = original
                for start, end, replacement in reversed(result.edits):
                    reconstructed = reconstructed[:start] + replacement + reconstructed[end:]
                self.assertEqual(reconstructed, result.text)
                self.assertEqual(
                    replace_content_from_edited_claims(result.text, "invention").text,
                    result.text,
                )

    def test_short_description_numbers_preserve_brackets_and_zero_padding(self):
        for opening, closing in (("【", "】"), ("[", "]")):
            for width in (1, 2, 4):
                for fullwidth in (False, True):
                    with self.subTest(style=opening, width=width, fullwidth=fullwidth):
                        to_digits = (
                            lambda value: value.translate(str.maketrans("0123456789", "０１２３４５６７８９"))
                            if fullwidth else value
                        )
                        label_for = lambda number: f"{opening}{to_digits(f'{number:0{width}d}')}{closing}"
                        original = edited_preview(content="舊內容。")
                        bodies = (
                            " 技術領域的  A B 手動編輯。", "背景的新文字。", "舊內容。",
                            "圖1：舊圖說保留，不改\t格式。", "實施方式的手動編輯維持  two  spaces。",
                        )
                        for number, body in enumerate(bodies, 1):
                            original = original.replace(body, label_for(number) + " " + body)
                        result = replace_content_from_edited_claims(original, "invention")
                        self.assertTrue(result.replacement_text.startswith(label_for(3) + " "))
                        self.assertIn(label_for(4) + " 本發明的感測裝置", result.replacement_text)
                        self.assertIn(label_for(5) + " 舊內容。", result.text)
                        self.assertIn(label_for(6) + " 圖1：舊圖說保留，不改\t格式。", result.text)
                        self.assertIn(label_for(7) + " 實施方式的手動編輯維持  two  spaces。", result.text)
                        self.assertEqual(
                            replace_content_from_edited_claims(result.text, "invention").text,
                            result.text,
                        )

    def test_content_heading_must_match_the_loaded_document_kind(self):
        for kind, wrong_heading in (("invention", "實用新型內容"), ("utility_model", "發明內容")):
            original = edited_preview(kind=kind)
            correct = "發明內容" if kind == "invention" else "實用新型內容"
            with self.subTest(kind=kind), self.assertRaisesRegex(ConversionError, "文件類型.*標題不一致"):
                replace_content_from_edited_claims(original.replace(correct, wrong_heading), kind)

    def test_mixed_description_numbering_fails_instead_of_creating_duplicates(self):
        original = edited_preview(content="【0003】舊內容。")
        with self.assertRaises(ConversionError):
            replace_content_from_edited_claims(original, "invention")

    def test_edited_multiline_claims_export_as_two_claims_in_both_templates(self):
        claims = (
            "1. 一種裝置，包括：\n1.5 V電源；及\n一處理器。\n"
            "2. 如請求項1所述之裝置，其中處理器具有新記憶體。"
        )
        for kind in ("invention", "utility_model"):
            for template in converter.available_templates():
                with self.subTest(kind=kind, template=template), tempfile.TemporaryDirectory() as temporary:
                    result = replace_content_from_edited_claims(edited_preview(claims, kind=kind), kind)
                    source_parsed = converter.parse_taiwan_specification(SOURCE)
                    source_parsed.kind = kind
                    output = Path(temporary) / "edited.docx"
                    with patch.object(converter, "parse_taiwan_specification", return_value=source_parsed):
                        report = converter.convert_document(
                            SOURCE, output, template_key=template,
                            terminology_pairs=[("新記憶體", "不應二次替換")],
                            edited_preview_text=result.text,
                        )
                    self.assertTrue(report.ok, report.errors)
                    self.assertEqual(report.claim_count, 2)
                    with zipfile.ZipFile(output) as package:
                        document = ET.fromstring(package.read("word/document.xml"))
                    paragraphs = document.findall("w:body/w:p", converter.NS)
                    texts = [converter._paragraph_text(paragraph) for paragraph in paragraphs]
                    expected_claims = [
                        converter._windows_simplified_chinese(claim)
                        for claim in converter.parse_edited_preview_text(result.text, kind).claims
                    ]
                    for claim in expected_claims:
                        self.assertEqual(texts.count(claim), 1)
                    claim_paragraphs = [paragraphs[texts.index(claim)] for claim in expected_claims]
                    self.assertEqual(len(claim_paragraphs), 2)
                    self.assertIn("1.5 V", converter._paragraph_text(claim_paragraphs[0]))
                    for paragraph in claim_paragraphs:
                        self.assertIsNotNone(paragraph.find("w:pPr/w:numPr", converter.NS))
                        self.assertNotRegex(converter._paragraph_text(paragraph), r"^[12][.．、]")
                    self.assertNotIn("不應二次替換", "\n".join(texts))
                    self.assertIn("人工案名😀", texts)


if __name__ == "__main__":
    unittest.main()
