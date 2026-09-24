"""Source disclosure is retained, followed by claims 2 onward, then benefits."""

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from xml.etree import ElementTree as ET

from features.taiwan_china_spec import converter
from features.taiwan_china_spec.article_review import find_article_occurrences


SOURCE = Path(__file__).parent / "fixtures" / "TW_方向校正專利圖式標號檢核系統_發明說明書.docx"


def source_spec(kind="invention"):
    owner = "本發明" if kind == "invention" else "本新型"
    return converter.ParsedSpecification(
        kind=kind, title="感測装置",
        abstract=["一種感測裝置。"], technology_field=["涉及感測裝置。"],
        background=["現有裝置須改善。"],
        invention_content=[
            f"因此，{owner}的目的，即在提供一種節省空間的感測裝置。",
            f"於是，{owner}的感測裝置並包含一殼體及一處理器。",
            "該殼體具有一個來源專有的模組，及另一個卡槽；位置A B保留。",
            f"{owner}的感測裝置，該處理器儲存來源專有資料。後續句子保留。",
            f"{owner}的功效在於：節省空間。並降低功耗。",
        ],
        drawing_description=["圖1為一個裝置。"],
        embodiments=["所述裝置具有殼體。"],
        claims=[
            "一種感測裝置，並包含：一殼體，包括僅存在請求項的雷射。",
            "根據權利要求1所述的感測裝置，其中包含僅存在附屬項的天線。",
        ],
    )


def preview_text(preview):
    return "\n".join(item.prefix + item.converted_text for item in preview.paragraphs)


def content_texts(parsed):
    return [
        converter._adapt_existing_dependent_narrative(item.text) if item.dependent else item.text
        for item in converter._build_source_content(parsed)
    ]


def output_content(path):
    with zipfile.ZipFile(path) as package:
        root = ET.fromstring(package.read("word/document.xml"))
    lines = [
        converter._paragraph_text(item)
        for item in root.findall("w:body/w:p", converter.NS)
    ]
    start = next(i for i, line in enumerate(lines) if line.strip() in ("发明内容", "实用新型内容"))
    end = next(i for i in range(start + 1, len(lines)) if lines[i].strip() == "附图说明")
    return [line for line in lines[start + 1:end] if line]


class SourceContentTests(unittest.TestCase):
    def test_fixed_intro_original_middle_and_benefit_keep_order_for_both_kinds(self):
        for kind, owner in (("invention", "本發明"), ("utility_model", "本實用新型")):
            with self.subTest(kind=kind):
                parsed = source_spec(kind)
                items = converter._build_source_content(parsed)
                self.assertEqual(content_texts(parsed), [
                    f"{owner}的目的在於提供一種節省空間的感測裝置。",
                    f"{owner}的感測裝置並包含一殼體及一處理器。",
                    *parsed.invention_content[2:4],
                    f"{owner}的感測裝置，包含僅存在附屬項的天線。",
                    f"{owner}的有益效果在於：節省空間。並降低功耗。",
                ])
                self.assertEqual(items[2].source_text, parsed.invention_content[2])
                self.assertEqual(items[3].source_text, parsed.invention_content[3])
                self.assertFalse(any("僅存在請求項的雷射" in item.text for item in items))
                self.assertEqual(items[-2].source_text, parsed.claims[1])
                altered_claims = replace(parsed, claims=["完全不同的第一請求項。", *parsed.claims[1:]])
                self.assertEqual(converter._build_source_content(altered_claims), items)

    def test_shared_word_paragraph_excludes_only_the_consumed_sentences(self):
        parsed = source_spec()
        parsed.claims = parsed.claims[:1]
        parsed.invention_content = [
            parsed.invention_content[0] + "目的後方仍屬來源內文。",
            parsed.invention_content[1] + "第二句後方內文保留。仍有下一句。",
            "功效前方內文保留。" + parsed.invention_content[-1],
        ]
        self.assertEqual([item.text for item in converter._build_source_content(parsed)], [
            "本發明的目的在於提供一種節省空間的感測裝置。",
            "本發明的感測裝置並包含一殼體及一處理器。",
            "目的後方仍屬來源內文。",
            "第二句後方內文保留。仍有下一句。",
            "功效前方內文保留。",
            "本發明的有益效果在於：節省空間。並降低功耗。",
        ])

    def test_numbered_inline_source_preserves_punctuation_and_nonmatching_phrases(self):
        parsed = source_spec()
        parsed.claims = parsed.claims[:1]
        parsed.invention_content = [
            "【0005】" + parsed.invention_content[0] + parsed.invention_content[1]
            + "因此，所述殼體設有空間。於是，所述處理器執行檢查！"
            + parsed.invention_content[-1],
        ]
        text = [item.text for item in converter._build_source_content(parsed)]
        self.assertEqual(len(text), 4)
        self.assertEqual(text[2], "因此，所述殼體設有空間。於是，所述處理器執行檢查！")

    def test_nonstandard_source_keeps_body_and_appends_later_claims_without_fabricated_benefit(self):
        parsed = source_spec()
        parsed.invention_content = ["另一種寫法的原始內文。", "所述元件包括另一個介面。"]
        self.assertEqual(
            content_texts(parsed),
            parsed.invention_content + ["本發明的感測裝置，包含僅存在附屬項的天線。"],
        )
        parsed.invention_content = []
        self.assertEqual(content_texts(parsed), ["本發明的感測裝置，包含僅存在附屬項的天線。"])
        parsed.claims = []
        self.assertEqual(converter._build_source_content(parsed), [])

    def test_only_claim_one_adds_no_extra_paragraph_or_benefit(self):
        parsed = source_spec()
        parsed.claims = parsed.claims[:1]
        parsed.invention_content = parsed.invention_content[:2]
        items = converter._build_source_content(parsed)
        self.assertEqual(len(items), 2)
        self.assertFalse(any("有益效果" in item.text or "僅存在" in item.text for item in items))

    def test_every_claim_after_first_uses_narrative_rules_in_order_before_benefit(self):
        for kind, owner in (("invention", "本發明"), ("utility_model", "本實用新型")):
            with self.subTest(kind=kind):
                parsed = source_spec(kind)
                parsed.claims = [
                    parsed.claims[0],
                    "根據權利要求1所述的感測裝置，其特徵在於：，所述處理器具有一個記憶體。",
                    "如請求項1或2所述之感測裝置，其中，所述記憶體連接所述處理器。",
                    "一種傳輸方法，其特徵在於：所述處理器傳送資料。",
                    "根據權利要求4所述的傳輸方法，其特徵在於，所述資料包含校驗碼。",
                ]
                rules = [("記憶體", "模組"), ("模組", "控制模組")]
                with patch.object(converter, "parse_taiwan_specification", return_value=parsed):
                    preview = converter.build_conversion_preview(
                        SOURCE, terminology_pairs=rules, traditional_characters=True,
                    )
                text = preview_text(preview)
                content = converter.parse_edited_preview_text(text, kind).invention_content
                appended = content[4:-1]
                self.assertEqual(appended, [
                    f"{owner}的感測裝置，所述處理器具有一個控制模組。",
                    f"{owner}的感測裝置，所述控制模組連接所述處理器。",
                    f"{owner}的傳輸方法，所述處理器傳送資料。",
                    f"{owner}的傳輸方法，所述資料包含校驗碼。",
                ])
                self.assertTrue(content[-1].startswith(f"{owner}的有益效果在於"))
                self.assertEqual(len(content), 9)
                self.assertNotIn("，，", "".join(appended))
                self.assertNotIn("其特徵在於", "".join(appended))
                self.assertNotIn("僅存在請求項的雷射", "".join(content))
                self.assertFalse(preview.warnings)
                hits = [
                    hit for hit in find_article_occurrences(text)
                    if hit.sentence == appended[0]
                ]
                self.assertEqual(len(hits), 1)
                self.assertEqual(hits[0].section, "invention_content")

    def test_unrecognized_dependency_is_retained_and_reported_in_preview_and_export(self):
        parsed = source_spec()
        unknown = "依前述請求項1之裝置，其中所述資料不得移除。"
        parsed.claims[1] = unknown
        with patch.object(converter, "parse_taiwan_specification", return_value=parsed):
            preview = converter.build_conversion_preview(
                SOURCE, terminology_pairs=[], traditional_characters=True,
            )
            content = converter.parse_edited_preview_text(
                preview_text(preview), parsed.kind,
            ).invention_content
            self.assertEqual(content[-2], unknown)
            self.assertTrue(any("權利要求 2 的依附開頭" in item for item in preview.warnings))
            with tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "unrecognized.docx"
                report = converter.convert_document(SOURCE, output, terminology_pairs=[])
                self.assertEqual(output_content(output)[-2], converter._windows_simplified_chinese(unknown))
                self.assertTrue(any("權利要求 2 的依附開頭" in item for item in report.warnings))

    def test_preview_direct_and_edited_word_use_source_and_ordered_dictionary_once(self):
        rules = [("模組", "模塊"), ("模塊", "控制模塊"), ("該", "所述")]
        for kind in ("invention", "utility_model"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                parsed = source_spec(kind)
                with patch.object(converter, "parse_taiwan_specification", return_value=parsed):
                    preview = converter.build_conversion_preview(
                        SOURCE, terminology_pairs=rules, traditional_characters=True,
                    )
                    expected = converter.parse_edited_preview_text(preview_text(preview), kind)
                    middle = expected.invention_content[2:4]
                    self.assertIn("所述殼體具有一個來源專有的控制模塊", middle[0])
                    self.assertNotIn("僅存在請求項的雷射", "".join(expected.invention_content))
                    owner = "本發明" if kind == "invention" else "本實用新型"
                    self.assertEqual(
                        expected.invention_content[-2],
                        f"{owner}的感測裝置，包含僅存在附屬項的天線。",
                    )
                    self.assertEqual(len(expected.invention_content), 6)
                    self.assertTrue(expected.invention_content[-1].startswith(f"{owner}的有益效果"))
                    self.assertIn("僅存在", "".join(expected.claims))
                    for template in converter.available_templates():
                        for edited in (None, preview_text(preview)):
                            with self.subTest(template=template, edited=edited is not None):
                                output = Path(directory) / f"{template}_{edited is not None}.docx"
                                converter.convert_document(
                                    SOURCE, output, template_key=template,
                                    terminology_pairs=rules, edited_preview_text=edited,
                                )
                                self.assertEqual(output_content(output), [
                                    converter._windows_simplified_chinese(line)
                                    for line in expected.invention_content
                                ])

                    # User edits in the new content group must reach the export.
                    edited = preview_text(preview).replace("一個來源專有", "人工修訂的來源專有")
                    output = Path(directory) / "edited.docx"
                    converter.convert_document(SOURCE, output, terminology_pairs=rules, edited_preview_text=edited)
                    self.assertIn("人工修订的来源专有", "".join(output_content(output)))


if __name__ == "__main__":
    unittest.main()
