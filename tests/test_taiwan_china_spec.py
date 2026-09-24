import os
import tempfile
import time
import unittest
import zipfile
from collections import Counter
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QLabel,
    QPushButton,
)
from PySide6.QtCore import QMimeData
from PySide6.QtGui import QColor, QPalette

from app.styles import APP_STYLE
from features.taiwan_china_spec.converter import (
    NS,
    available_templates,
    build_conversion_preview,
    convert_document,
    default_terminology_path,
    normalize_terminology_pairs,
    parse_taiwan_specification,
    parse_edited_preview_text,
    parse_terminology_text,
    _convert_text,
    _windows_simplified_chinese,
    _match_claims_heading,
)
from features.taiwan_china_spec.terminology_store import (
    TerminologyDictionaryStore,
    default_terminology_cache_path,
)
from ui.taiwan_china_spec_page import PlainOnlyPreviewEditor, TaiwanChinaSpecPage


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE = (
    PROJECT_ROOT
    / "tests"
    / "fixtures"
    / "TW_方向校正專利圖式標號檢核系統_發明說明書.docx"
)


def package_parts(path):
    with zipfile.ZipFile(path, "r") as package:
        return {
            name: package.read(name)
            for name in package.namelist()
            if name != "word/document.xml"
        }


def paragraph_text(paragraph):
    return "".join(
        node.text or "" for node in paragraph.findall(".//w:t", NS)
    )


def assert_specification_first_line_indents(test_case, root):
    body_headings = {
        "技术领域", "背景技术", "发明内容", "实用新型内容", "附图说明", "具体实施方式",
    }
    first_line_chars = f"{{{NS['w']}}}firstLineChars"
    hanging = f"{{{NS['w']}}}hanging"
    hanging_chars = f"{{{NS['w']}}}hangingChars"
    inside_specification_body = False
    checked = []
    for paragraph in root.findall(".//w:body/w:p", NS):
        text = "".join(paragraph_text(paragraph).split())
        if text in {"说明书附图", "說明書附圖"}:
            inside_specification_body = False
            continue
        if text in body_headings:
            inside_specification_body = True
            continue
        if not inside_specification_body or not text:
            continue

        indentation = paragraph.find("w:pPr/w:ind", NS)
        test_case.assertIsNotNone(indentation, text[:40])
        test_case.assertEqual(indentation.get(first_line_chars), "300")
        test_case.assertIsNone(indentation.get(hanging))
        test_case.assertIsNone(indentation.get(hanging_chars))
        checked.append(text)
    test_case.assertGreater(len(checked), 5)


def assert_decimal_claim_numbering(test_case, output, claim_paragraphs):
    with zipfile.ZipFile(output, "r") as package:
        numbering = ET.fromstring(package.read("word/numbering.xml"))
    word_value = f"{{{NS['w']}}}val"
    for paragraph in claim_paragraphs:
        paragraph_properties = paragraph.find("w:pPr", NS)
        test_case.assertIsNotNone(paragraph_properties)
        test_case.assertIsNone(paragraph_properties.find("w:keepLines", NS))

        number_properties = paragraph_properties.find("w:numPr", NS)
        test_case.assertIsNotNone(number_properties)
        level = number_properties.find("w:ilvl", NS)
        number_id = number_properties.find("w:numId", NS)
        test_case.assertIsNotNone(level)
        test_case.assertIsNotNone(number_id)
        test_case.assertEqual(level.get(word_value), "0")

        number = numbering.find(
            f".//w:num[@w:numId='{number_id.get(word_value)}']",
            NS,
        )
        test_case.assertIsNotNone(number)
        abstract_number_id = number.find("w:abstractNumId", NS)
        test_case.assertIsNotNone(abstract_number_id)
        abstract_number = numbering.find(
            ".//w:abstractNum[@w:abstractNumId='{}']".format(
                abstract_number_id.get(word_value)
            ),
            NS,
        )
        test_case.assertIsNotNone(abstract_number)
        first_level = abstract_number.find("w:lvl[@w:ilvl='0']", NS)
        test_case.assertIsNotNone(first_level)
        test_case.assertEqual(
            first_level.find("w:numFmt", NS).get(word_value),
            "decimal",
        )
        test_case.assertEqual(
            first_level.find("w:lvlText", NS).get(word_value),
            "%1.",
        )


def write_docx_with_embodiment_claims_phrase(source, target):
    """Add the real-world false marker to an embodiment paragraph."""

    with zipfile.ZipFile(source, "r") as original, zipfile.ZipFile(
        target, "w"
    ) as modified:
        for info in original.infolist():
            payload = original.read(info.filename)
            if info.filename == "word/document.xml":
                root = ET.fromstring(payload)
                paragraphs = root.findall(".//w:body/w:p", NS)
                symbol_index = next(
                    index
                    for index, paragraph in enumerate(paragraphs)
                    if "【符號說明】"
                    in "".join(
                        node.text or "" for node in paragraph.findall(".//w:t", NS)
                    )
                )
                text_nodes = paragraphs[symbol_index - 1].findall(".//w:t", NS)
                text_nodes[-1].text = (text_nodes[-1].text or "") + (
                    "惟以上所述，凡是依本發明申請專利範圍及專利說明書內容"
                    "所作之等效變化，仍屬保護範圍。"
                )
                payload = ET.tostring(root, encoding="utf-8", xml_declaration=True)
            modified.writestr(info, payload)


class TaiwanChinaSpecConverterTests(unittest.TestCase):
    def test_claims_heading_does_not_match_an_embodiment_sentence(self):
        self.assertIsNone(
            _match_claims_heading("凡是依本發明申請專利範圍及說明書所作之變化。")
        )
        self.assertEqual(_match_claims_heading("申請專利範圍"), "")
        self.assertEqual(
            _match_claims_heading("【發明申請專利範圍】1. 一種檢核系統。"),
            "1. 一種檢核系統。",
        )

    def test_embodiment_reference_to_claims_does_not_absorb_symbol_description(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "false-claims-marker.docx"
            write_docx_with_embodiment_claims_phrase(SOURCE, source)

            parsed = parse_taiwan_specification(source)

            self.assertEqual(len(parsed.claims), 10)
            self.assertTrue(parsed.claims[0].startswith("一種"))
            self.assertFalse(
                any(
                    marker in claim
                    for claim in parsed.claims
                    for marker in ("【符號說明】", "【發明申請專利範圍】")
                )
            )

    def test_both_legacy_template_choices_convert_without_mutating_template_parts(self):
        with tempfile.TemporaryDirectory() as temporary:
            for template_key, template_path in available_templates().items():
                with self.subTest(template=template_key):
                    output = Path(temporary) / f"{template_key}.docx"
                    report = convert_document(
                        SOURCE,
                        output,
                        template_key,
                        default_terminology_path(),
                    )
                    self.assertTrue(report.ok)
                    self.assertEqual(report.claim_count, 10)
                    self.assertEqual(
                        package_parts(output),
                        package_parts(template_path),
                    )
                    with zipfile.ZipFile(output, "r") as package:
                        root = ET.fromstring(package.read("word/document.xml"))
                        text_parts = [
                            node.text or ""
                            for node in root.findall(".//w:t", NS)
                        ]
                        for name in package.namelist():
                            if name.startswith("word/header") and name.endswith(".xml"):
                                header = ET.fromstring(package.read(name))
                                text_parts.extend(
                                    node.text or ""
                                    for node in header.findall(".//w:t", NS)
                                )
                        text = "".join(text_parts)
                    compact_text = "".join(text.split())
                    self.assertNotIn("...開始段落", text)
                    self.assertIn("权利要求书", compact_text)
                    self.assertIn("技术领域", compact_text)
                    self.assertGreaterEqual(text.count("其特征在于"), 10)
                    claim_paragraphs = [
                        paragraph
                        for paragraph in root.findall(".//w:body/w:p", NS)
                        if "其特征在于" in paragraph_text(paragraph)
                    ]
                    self.assertEqual(len(claim_paragraphs), 10)
                    for paragraph in claim_paragraphs:
                        claim_text = paragraph_text(paragraph)
                        self.assertNotRegex(claim_text, r"其特征在于[：:]?[，,]")
                        self.assertNotIn("，，", claim_text)
                    assert_decimal_claim_numbering(
                        self,
                        output,
                        claim_paragraphs,
                    )
                    assert_specification_first_line_indents(self, root)

    def test_text_dictionary_field_accepts_legacy_colon_format(self):
        with tempfile.TemporaryDirectory() as temporary:
            temporary = Path(temporary)
            dictionary = temporary / "dictionary.txt"
            dictionary.write_text("方向校正：方向修正\n", encoding="utf-8")
            output = temporary / "converted.docx"
            convert_document(
                SOURCE,
                output,
                terminology_path=dictionary,
            )
            with zipfile.ZipFile(output, "r") as package:
                root = ET.fromstring(package.read("word/document.xml"))
            text = "".join(
                node.text or "" for node in root.findall(".//w:t", NS)
            )
            self.assertIn("方向修正", text)

    def test_editable_dictionary_pairs_are_used_without_a_temporary_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "converted.docx"
            convert_document(
                SOURCE,
                output,
                terminology_pairs=[("方向校正", "方向調整")],
            )
            with zipfile.ZipFile(output, "r") as package:
                root = ET.fromstring(package.read("word/document.xml"))
            text = "".join(
                node.text or "" for node in root.findall(".//w:t", NS)
            )
            self.assertIn("方向调整", text)

    def test_text_preview_uses_mainland_order_and_retains_comparison_sources(self):
        preview = build_conversion_preview(
            SOURCE,
            terminology_pairs=[("方向校正", "方向修正")],
        )

        final_text = "\n".join(
            f"{paragraph.prefix}{paragraph.converted_text}"
            for paragraph in preview.paragraphs
        )
        self.assertIn("说明书摘要", final_text)
        self.assertIn("权利要求书", final_text)
        self.assertIn("说明书", final_text)
        self.assertIn("技术领域", final_text)
        self.assertIn("方向修正", final_text)
        self.assertNotIn("【技術領域】", final_text)
        claims = [
            paragraph
            for paragraph in preview.paragraphs
            if paragraph.role == "claim"
        ]
        self.assertEqual(len(claims), 10)
        self.assertEqual(claims[0].prefix, "1. ")
        self.assertTrue(
            any(
                paragraph.source_text != paragraph.converted_text
                for paragraph in preview.paragraphs
                if paragraph.role != "heading"
            )
        )

    def test_editable_preview_uses_mainland_structure_with_traditional_characters(self):
        preview = build_conversion_preview(
            SOURCE,
            terminology_pairs=[("方向校正", "方向修正")],
            traditional_characters=True,
        )
        final_text = "\n".join(
            f"{paragraph.prefix}{paragraph.converted_text}"
            for paragraph in preview.paragraphs
        )

        expected_headings = [
            "說明書摘要",
            "權利要求書",
            "說明書",
            "技術領域",
            "背景技術",
            "發明內容",
            "附圖說明",
            "具體實施方式",
        ]
        preview_lines = final_text.splitlines()
        positions = [preview_lines.index(heading) for heading in expected_headings]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("方向修正", final_text)
        self.assertNotIn("说明书摘要", final_text)

    def test_edited_preview_add_modify_delete_drives_simplified_word_output(self):
        preview = build_conversion_preview(
            SOURCE,
            terminology_pairs=[("方向校正", "方向修正")],
            traditional_characters=True,
        )
        lines = [
            f"{paragraph.prefix}{paragraph.converted_text}"
            for paragraph in preview.paragraphs
        ]
        parsed_before = parse_edited_preview_text(
            "\n".join(lines), preview.specification_kind
        )
        deleted_paragraph = parsed_before.background[0]
        lines.remove(deleted_paragraph)
        title_index = lines.index("說明書") + 1
        lines[title_index] = "人工修訂的方向校正檢核系統"
        embodiment_index = lines.index("具體實施方式") + 1
        lines.insert(embodiment_index, "使用者新增段落：額外測試資料。")

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "edited-preview.docx"
            report = convert_document(
                SOURCE,
                output,
                terminology_pairs=[("方向校正", "方向修正")],
                edited_preview_text="\n".join(lines),
            )
            self.assertTrue(report.ok)
            with zipfile.ZipFile(output, "r") as package:
                root = ET.fromstring(package.read("word/document.xml"))
            output_text = "".join(
                node.text or "" for node in root.findall(".//w:t", NS)
            )

        self.assertIn("人工修订的方向校正检核系统", output_text)
        self.assertIn("使用者新增段落：额外测试资料。", output_text)
        self.assertNotIn(
            _windows_simplified_chinese(deleted_paragraph),
            output_text,
        )

    def test_dictionary_preserves_order_repetitions_and_conflicting_sources(self):
        pairs = [
            ("甲", "乙"),
            ("甲乙", "丙"),
            ("甲", "丁"),
            ("甲", "丁"),
        ]
        self.assertEqual(normalize_terminology_pairs(pairs), pairs)
        self.assertEqual(
            _convert_text("甲乙", pairs, Counter(), apply_terminology=True),
            "乙乙",
        )
        self.assertEqual(
            parse_terminology_text("方向校正：方向修正\n"),
            [("方向校正", "方向修正")],
        )

    def test_bundled_company_dictionary_keeps_all_198_approved_rules_in_order(self):
        pairs = parse_terminology_text(
            default_terminology_path().read_text(encoding="utf-8")
        )

        self.assertEqual(len(pairs), 198)
        self.assertEqual(
            pairs[:3],
            [
                (
                    "本發明之其他的特徵及功效，將於參照圖式的實施方式中清楚地呈現，其中：",
                    "",
                ),
                (
                    "本新型之其他的特徵及功效，將於參照圖式的實施方式中清楚地呈現，其中：",
                    "",
                ),
                ("射出成型", "注射成型"),
            ],
        )
        self.assertEqual(pairs[154], ("次多個", "次數"))
        self.assertEqual(
            pairs[186:189],
            [
                ("應所述注意的是", "應該注意的是"),
                ("單多個", "單數"),
                ("雙多個", "雙數"),
            ],
        )
        self.assertEqual(pairs.count(("所述所述", "所述")), 2)
        self.assertEqual(pairs[-2], ("中具有通常知識者", "技術人員"))
        self.assertEqual(pairs[-1], ("兩個合一個", "二合一"))
        self.assertEqual(
            _convert_text(pairs[0][0], pairs, Counter(), apply_terminology=True),
            "",
        )
        self.assertEqual(
            _convert_text("兩個合一個", pairs, Counter(), apply_terminology=True),
            "二合一",
        )


class TerminologyDictionaryStoreTests(unittest.TestCase):
    def test_default_cache_is_versioned_for_the_approved_dictionary_release(self):
        self.assertEqual(
            default_terminology_cache_path().name,
            "taiwan_china_terminology.v4.txt",
        )

    def test_cloud_default_is_cached_after_validation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cloud = root / "shared" / "dictionary.tsv"
            cache = root / "cache" / "dictionary.tsv"
            cloud.parent.mkdir()
            cloud.write_text("圖式\t附圖\n", encoding="utf-8")
            store = TerminologyDictionaryStore(
                cloud_path=cloud,
                cache_path=cache,
                bundled_path=default_terminology_path(),
            )

            snapshot = store.load_preferred()

            self.assertEqual(snapshot.source_kind, "雲端")
            self.assertEqual(snapshot.pairs, (("圖式", "附圖"),))
            self.assertEqual(cache.read_text(encoding="utf-8"), "圖式\t附圖\n")

    def test_invalid_cloud_uses_last_known_good_cache_without_overwriting_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cloud = root / "cloud.tsv"
            cache = root / "cache.tsv"
            cloud.write_text("無效內容\n", encoding="utf-8")
            cache.write_text("圖式\t附圖\n", encoding="utf-8")
            store = TerminologyDictionaryStore(
                cloud_path=cloud,
                cache_path=cache,
                bundled_path=default_terminology_path(),
            )

            snapshot = store.load_preferred()

            self.assertEqual(snapshot.source_kind, "快取")
            self.assertEqual(snapshot.pairs, (("圖式", "附圖"),))
            self.assertIn("雲端辭典無法使用", snapshot.warning)
            self.assertEqual(cache.read_text(encoding="utf-8"), "圖式\t附圖\n")

    def test_missing_cloud_and_cache_fall_back_to_bundled_dictionary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = TerminologyDictionaryStore(
                cloud_path=root / "missing-cloud.tsv",
                cache_path=root / "missing-cache.tsv",
                bundled_path=default_terminology_path(),
            )

            snapshot = store.load_preferred()

            self.assertEqual(snapshot.source_kind, "內建")
            self.assertGreater(len(snapshot.pairs), 50)


class TaiwanChinaSpecPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyleSheet(APP_STYLE)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        temporary = Path(self.temporary.name)
        self.store = TerminologyDictionaryStore(
            cloud_path=temporary / "cloud.tsv",
            cache_path=temporary / "cache.tsv",
            bundled_path=default_terminology_path(),
        )
        self.page = TaiwanChinaSpecPage(
            lambda: None,
            dictionary_store=self.store,
            auto_load_dictionary=False,
        )

    def tearDown(self):
        self.page.close()
        self.page.deleteLater()
        self.temporary.cleanup()

    def wait_for_preview(self):
        for _attempt in range(400):
            QApplication.processEvents()
            if self.page._preview_thread is None and self.page._pending_preview is None:
                return
            time.sleep(0.005)
        self.fail("文件預覽未在時限內完成")

    def test_legacy_text_and_function_fields_are_all_present(self):
        labels = {label.text() for label in self.page.findChildren(QLabel)}
        button_texts = [
            button.text() for button in self.page.findChildren(QPushButton)
        ]
        buttons = set(button_texts)
        self.assertTrue(
            {
                "台灣專利說明書",
                "大陸專利說明書",
                "大陸用語辭典",
                "訊息欄...",
            }.issubset(labels)
        )
        self.assertTrue(
            {
                "Browse",
                "新增",
                "刪除",
                "同步",
                "轉換",
            }.issubset(buttons)
        )
        self.assertNotIn("使用說明", buttons)
        self.assertNotIn("關於...", buttons)
        self.assertEqual(button_texts.count("Browse"), 1)
        self.assertFalse(hasattr(self.page, "dictionary_line"))
        self.assertFalse(hasattr(self.page, "dictionary_button"))
        self.assertEqual(
            [self.page.template_combo.itemText(index) for index in range(2)],
            ["北京泰吉", "上海一品"],
        )
        for button in (
            self.page.source_button,
            self.page.add_dictionary_button,
            self.page.delete_dictionary_button,
            self.page.reload_dictionary_button,
            self.page.convert_button,
        ):
            self.assertFalse(button.icon().isNull())
        self.assertFalse(hasattr(self.page, "help_button"))
        self.assertFalse(hasattr(self.page, "about_button"))
        self.assertEqual(self.page.dictionary_table.columnCount(), 2)
        self.assertEqual(
            [
                self.page.dictionary_table.horizontalHeaderItem(index).text()
                for index in range(2)
            ],
            ["台灣用語", "大陸用語"],
        )
        self.assertEqual(
            self.page.dictionary_table.editTriggers(),
            QAbstractItemView.AllEditTriggers,
        )
        self.assertTrue(
            self.page.dictionary_manager_button.text().startswith("大陸用語辭典（")
        )
        self.assertIs(self.page.dictionary_table.window(), self.page.dictionary_dialog)

    def test_main_page_replaces_dictionary_table_with_red_change_preview(self):
        self.page.resize(960, 640)
        self.page.show()
        QApplication.processEvents()

        self.assertTrue(self.page.dictionary_manager_button.isVisible())
        self.assertFalse(self.page.dictionary_dialog.isVisible())
        self.assertTrue(self.page.load_source(SOURCE))
        self.wait_for_preview()
        preview_text = self.page.preview_output.toPlainText()
        self.assertIn("說明書摘要", preview_text)
        self.assertIn("權利要求書", preview_text)
        self.assertNotIn("说明书摘要", preview_text)
        self.assertIn("1. ", preview_text)
        self.assertNotIn("【技術領域】", preview_text)
        self.assertIn("#c62828", self.page.preview_output.toHtml())
        self.assertIn("個段落有變更", self.page.preview_status.text())

    def test_preview_editor_accepts_only_plain_text_paste(self):
        editor = PlainOnlyPreviewEditor()
        try:
            mime = QMimeData()
            mime.setText("純文字貼上")
            mime.setHtml("<b><u>純文字貼上</u></b>")
            editor.insertFromMimeData(mime)

            self.assertFalse(editor.acceptRichText())
            self.assertEqual(editor.toPlainText(), "純文字貼上")
            html = editor.toHtml().lower()
            self.assertNotIn("<b>", html)
            self.assertNotIn("<u>", html)
        finally:
            editor.deleteLater()

    def test_automatic_refresh_does_not_overwrite_user_preview_edits(self):
        self.assertTrue(self.page.load_source(SOURCE))
        self.wait_for_preview()
        original = self.page.preview_output.toPlainText()
        self.page.preview_output.append("使用者手動修訂")
        edited = self.page.preview_output.toPlainText()

        self.assertNotEqual(original, edited)
        self.assertTrue(self.page.refresh_preview())
        self.assertEqual(self.page.preview_output.toPlainText(), edited)
        self.assertEqual(self.page.preview_status.text(), "● 已人工修訂")

    def test_convert_button_writes_the_editor_snapshot_not_the_source_again(self):
        self.assertTrue(self.page.load_source(SOURCE))
        self.wait_for_preview()
        lines = self.page.preview_output.toPlainText().splitlines()
        background_heading = lines.index("背景技術")
        deleted_paragraph = lines.pop(background_heading + 1)
        title_index = lines.index("說明書") + 1
        lines[title_index] = "當下修訂的專利標題"
        lines.insert(lines.index("具體實施方式") + 1, "當下新增的段落")
        self.page.preview_output.setPlainText("\n".join(lines))
        output = Path(self.temporary.name) / "editor-snapshot.docx"
        self.page.output_line.setText(str(output))

        self.page.start_conversion()
        for _attempt in range(300):
            QApplication.processEvents()
            if self.page._conversion_thread is None:
                break
            time.sleep(0.01)

        self.assertIsNone(self.page._conversion_thread)
        self.assertTrue(output.is_file())
        with zipfile.ZipFile(output, "r") as package:
            root = ET.fromstring(package.read("word/document.xml"))
        output_text = "".join(
            node.text or "" for node in root.findall(".//w:t", NS)
        )
        self.assertIn("当下修订的专利标题", output_text)
        self.assertIn("当下新增的段落", output_text)
        self.assertNotIn(_windows_simplified_chinese(deleted_paragraph), output_text)

    def test_dictionary_button_opens_the_full_dictionary_dialog(self):
        with patch.object(
            self.page.dictionary_dialog,
            "exec",
            return_value=QDialog.Accepted,
        ) as execute_dialog:
            self.page.dictionary_manager_button.click()

        execute_dialog.assert_called_once_with()
        button_texts = {
            button.text()
            for button in self.page.dictionary_dialog.findChildren(QPushButton)
        }
        self.assertTrue({"新增", "刪除", "同步", "上傳", "完成"}.issubset(button_texts))

    def test_dictionary_table_remains_readable_with_a_dark_system_palette(self):
        original_palette = self.app.palette()
        dark_palette = QPalette(original_palette)
        dark_palette.setColor(QPalette.Base, QColor("#000000"))
        dark_palette.setColor(QPalette.AlternateBase, QColor("#101010"))
        dark_palette.setColor(QPalette.Text, QColor("#000000"))
        try:
            self.app.setPalette(dark_palette)
            self.app.setStyleSheet(APP_STYLE)
            self.page.show()
            QApplication.processEvents()
            palette = self.page.dictionary_table.palette()
            self.assertEqual(palette.color(QPalette.Base).name(), "#ffffff")
            self.assertEqual(palette.color(QPalette.AlternateBase).name(), "#f8fafc")
            self.assertEqual(palette.color(QPalette.Text).name(), "#111827")
            self.assertEqual(palette.color(QPalette.Highlight).name(), "#bfdbfe")
            self.assertEqual(
                palette.color(QPalette.HighlightedText).name(), "#111827"
            )
            self.assertIn(
                "background-color: #eef3f8",
                self.page.dictionary_dialog.styleSheet(),
            )
        finally:
            self.app.setPalette(original_palette)
            self.app.setStyleSheet(APP_STYLE)

    def test_layout_remains_inside_common_office_window_sizes(self):
        for width, height in ((960, 640), (1360, 860)):
            with self.subTest(size=(width, height)):
                self.page.resize(width, height)
                self.page.show()
                QApplication.processEvents()
                self.assertLessEqual(self.page.minimumSizeHint().width(), width)
                self.assertLessEqual(self.page.minimumSizeHint().height(), height)
                for widget in (
                    self.page.source_line,
                    self.page.output_line,
                    self.page.preview_output,
                    self.page.dictionary_manager_button,
                    self.page.convert_button,
                    self.page.message_output,
                ):
                    bottom_right = widget.mapTo(self.page, widget.rect().bottomRight())
                    self.assertLess(bottom_right.x(), width)
                    self.assertLess(bottom_right.y(), height)
                self.assertGreater(
                    self.page.message_panel.mapTo(self.page, self.page.message_panel.rect().topLeft()).x(),
                    self.page.left_panel.mapTo(self.page, self.page.left_panel.rect().topLeft()).x(),
                )
                self.assertLess(
                    self.page.message_panel.width(),
                    self.page.left_panel.width(),
                )

    def test_dictionary_table_is_directly_editable_and_marks_local_revision(self):
        row = next(
            index
            for index in range(self.page.dictionary_table.rowCount())
            if self.page.dictionary_table.item(index, 0).text() == "射出成型"
        )
        self.page.dictionary_table.item(row, 1).setText("注射加工")

        self.assertEqual(self.page.dictionary_status.text(), "● 已修訂")
        self.assertIn(
            ("射出成型", "注射加工"),
            self.page._dictionary_pairs_from_table(),
        )

    def test_page_open_automatically_loads_cloud_default_in_background(self):
        cloud = Path(self.temporary.name) / "cloud.tsv"
        cloud.write_text("圖式\t附圖\n", encoding="utf-8")
        page = TaiwanChinaSpecPage(
            lambda: None,
            dictionary_store=self.store,
            auto_load_dictionary=True,
        )
        try:
            for _attempt in range(100):
                QApplication.processEvents()
                if (
                    page._dictionary_thread is None
                    and page.dictionary_status.text() == "● 雲端"
                ):
                    break
                time.sleep(0.01)
            self.assertEqual(page.dictionary_status.text(), "● 雲端")
            self.assertEqual(page.dictionary_table.rowCount(), 1)
            self.assertEqual(page.dictionary_table.item(0, 0).text(), "圖式")
            self.assertEqual(page.dictionary_table.item(0, 1).text(), "附圖")
        finally:
            page.shutdown()
            page.close()
            page.deleteLater()

    def test_output_name_tracks_template_until_user_edits_it(self):
        source = PROJECT_ROOT / "tests" / "fixtures" / "sample.docx"
        self.page.source_line.setText(str(source))
        self.page._suggest_output_path()
        self.assertTrue(self.page.output_line.text().endswith("-北京泰吉.docx"))
        self.page.template_combo.setCurrentIndex(1)
        self.assertTrue(self.page.output_line.text().endswith("-上海一品.docx"))
        self.page._mark_output_manual("manual.docx")
        self.page.output_line.setText("manual.docx")
        self.page.template_combo.setCurrentIndex(0)
        self.assertEqual(self.page.output_line.text(), "manual.docx")


if __name__ == "__main__":
    unittest.main()
