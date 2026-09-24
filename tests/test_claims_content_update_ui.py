"""Compatibility checks for the unexposed legacy claims-to-content backend."""

from html import escape
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QTextCursor
from PySide6.QtWidgets import QApplication

from app.styles import APP_STYLE
from features.taiwan_china_spec.converter import parse_edited_preview_text
from ui.taiwan_china_spec_page import TaiwanChinaSpecPage, _source_key


def sample_text(content="舊內容含過時技術。", kind="發明"):
    heading = "發明內容" if kind == "發明" else "實用新型內容"
    return (
        "說明書摘要\n一個摘要😀元件。\n權利要求書\n"
        "1. 一種測試裝置，包括殼體及一個處理器。\n"
        "2. 根據權利要求1所述的測試裝置，其中處理器包括記憶體。\n"
        "說明書\n測試裝置\n技術領域\n一個領域元件。\n"
        f"背景技術\n保留原背景。\n{heading}\n{content}\n"
        "附圖說明\n圖1為示意圖。\n具體實施方式\n一個殼體。"
    )


class ClaimsContentUpdateUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.store = Mock()
        self.store.load_bundled.return_value = SimpleNamespace(
            pairs=(("模組", "模塊"),), source_kind="內建", source_path=Path("local.txt"), warning="",
        )
        self.page = TaiwanChinaSpecPage(lambda: None, dictionary_store=self.store, auto_load_dictionary=False)
        self.page.setStyleSheet(APP_STYLE)

    def tearDown(self):
        self.page.shutdown()
        self.page.close()
        self.page.deleteLater()
        QApplication.processEvents()

    def load_preview(self, text, kind="invention", fixed_content_layout=False):
        headings = {"說明書摘要", "權利要求書", "說明書", "技術領域", "背景技術", "發明內容", "實用新型內容", "附圖說明", "具體實施方式"}
        paragraphs = []
        for line in text.split("\n"):
            style = "font-size:17px;font-weight:700;color:#0f2742;margin-top:14px;margin-bottom:6px" if line in headings else "font-size:15px;color:#008000;margin:5px"
            paragraphs.append(f'<p style="{style}">{escape(line) if line else "<br>"}</p>')
        self.page.source_line.setText("manual_source.docx")
        self.page._preview_source = _source_key("manual_source.docx")
        self.page._preview_specification_kind = kind
        self.page._preview_fixed_content_layout = fixed_content_layout
        self.page.preview_output.setHtml("".join(paragraphs))
        self.page.article_review.reset()
        self.page._preview_dirty = False

    def paragraph_format(self, text):
        editor = self.page.preview_output
        found = editor.document().find(text)
        self.assertFalse(found.isNull())
        block = found.blockFormat()
        char = found.charFormat()
        point_size = char.fontPointSize() or char.font().pixelSize() * 72 / 96
        return (block.topMargin(), block.bottomMargin(), block.alignment(), point_size, char.fontWeight(), char.foreground().color().name())

    def test_legacy_backend_preserves_other_sections_and_undo(self):
        self.load_preview(sample_text())
        editor, panel = self.page.preview_output, self.page.article_review
        # Simulate the user's manual correction, not the original source file.
        found = editor.document().find("一個處理器")
        found.insertText("處理器與備援電池")
        before_text, before_html = editor.toPlainText(), editor.toHtml()
        before = parse_edited_preview_text(before_text, "invention")
        before_heading = self.paragraph_format("附圖說明")
        before_preserved_content = self.paragraph_format("舊內容含過時技術。")
        old_history = len(panel._undo_history)
        self.page.replace_content_from_claims()
        after = parse_edited_preview_text(editor.toPlainText(), "invention")
        self.assertIn("舊內容含過時技術。", after.invention_content)
        self.assertEqual(
            self.paragraph_format("舊內容含過時技術。"),
            before_preserved_content,
        )
        self.assertIn("處理器與備援電池", "".join(after.invention_content))
        for key in ("abstract", "claims", "title", "technology_field", "background", "drawing_description", "embodiments"):
            self.assertEqual(getattr(after, key), getattr(before, key), key)
        self.assertEqual(self.paragraph_format("附圖說明"), before_heading)
        self.assertEqual(len(panel._undo_history), old_history + 1)
        self.assertTrue(self.page._preview_dirty)
        updated_text = editor.toPlainText()
        editor.undo()
        self.assertEqual(editor.toPlainText(), before_text)
        self.assertEqual(editor.toHtml(), before_html)
        editor.redo()
        self.assertEqual(editor.toPlainText(), updated_text)
        self.assertIn("Ctrl+Z", self.page.message_output.toPlainText())

    def test_fixed_layout_button_replaces_entire_old_claim_block(self):
        purpose = "本發明的目的在於提供一種測試裝置。"
        mechanism = "本發明的測試裝置並包含殼體及處理器。"
        benefit = "本發明的有益效果在於：降低操作時間。"
        old_claim_one = "本發明的舊測試裝置，這一段曾人工修改而不再完全相符。"
        old_claim_two = "本發明的舊測試裝置，舊處理器包括舊記憶體。"
        fixed_text = sample_text(content="\n".join([
                purpose, mechanism, old_claim_one, old_claim_two, benefit,
            ])).replace(
                "1. 一種測試裝置，包括殼體及一個處理器。",
                "1. 一種測試裝置，並包含殼體及處理器，所述殼體包括安裝部。",
            )
        self.load_preview(
            fixed_text,
            fixed_content_layout=True,
        )

        with patch(
            "ui.taiwan_china_spec_page.replace_content_from_edited_claims",
            wraps=__import__(
                "features.taiwan_china_spec.converter",
                fromlist=["replace_content_from_edited_claims"],
            ).replace_content_from_edited_claims,
        ) as replacement:
            self.assertTrue(self.page.replace_content_from_claims())

        self.assertTrue(replacement.call_args.kwargs["fixed_content_layout"])
        content = parse_edited_preview_text(
            self.page.preview_output.toPlainText(), "invention"
        ).invention_content
        self.assertEqual(content[:2], [purpose, mechanism])
        self.assertEqual(content[-1], benefit)
        self.assertNotIn(old_claim_one, content)
        self.assertNotIn(old_claim_two, content)
        self.assertEqual(content[2], "所述殼體包括安裝部。")
        self.assertTrue(content[3].startswith("本發明的測試裝置，"))

    def test_empty_target_does_not_inherit_following_heading_style(self):
        self.load_preview(sample_text(content=""))
        before = self.paragraph_format("附圖說明")
        self.assertTrue(self.page.replace_content_from_claims())
        self.assertEqual(self.paragraph_format("附圖說明"), before)
        content = self.paragraph_format("本發明的目的")
        self.assertLess(content[3], before[3])
        self.assertLess(content[4], before[4])

    def test_utility_heading_stays_utility_and_repeat_click_is_noop(self):
        self.load_preview(sample_text(kind="新型"), kind="utility_model")
        self.assertTrue(self.page.replace_content_from_claims())
        editor, panel = self.page.preview_output, self.page.article_review
        text, html, depth = editor.toPlainText(), editor.toHtml(), len(panel._undo_history)
        self.assertIn("實用新型內容", text)
        self.assertIn("本實用新型", text)
        self.assertFalse(self.page.replace_content_from_claims())
        self.assertEqual((editor.toPlainText(), editor.toHtml(), len(panel._undo_history)), (text, html, depth))

    def test_invalid_sections_leave_text_html_selection_and_history_unchanged(self):
        self.load_preview(sample_text().replace("附圖說明", "刪掉標題"))
        editor, panel = self.page.preview_output, self.page.article_review
        cursor = editor.document().find("摘要😀")
        editor.setTextCursor(cursor)
        before = (editor.toPlainText(), editor.toHtml(), cursor.position(), cursor.anchor(), len(panel._undo_history))
        self.assertFalse(self.page.replace_content_from_claims())
        current = editor.textCursor()
        self.assertEqual((editor.toPlainText(), editor.toHtml(), current.position(), current.anchor(), len(panel._undo_history)), before)
        self.assertIn("未變更原內容", self.page.message_output.toPlainText())

    def test_busy_missing_source_or_readonly_cannot_modify_preview(self):
        self.assertFalse(hasattr(self.page.article_review, "replace_content_button"))
        self.load_preview(sample_text())
        editor, panel = self.page.preview_output, self.page.article_review
        original = editor.toHtml()
        for attribute, value in (("_conversion_running", True), ("_preview_thread", object()), ("_closing", True), ("_preview_source", "")):
            with self.subTest(state=attribute), patch.object(self.page, attribute, value):
                self.assertFalse(self.page.replace_content_from_claims())
                self.assertEqual(editor.toHtml(), original)
        editor.setReadOnly(True)
        panel._update_buttons()
        self.assertFalse(hasattr(panel, "replace_content_button"))
        self.assertFalse(self.page.replace_content_from_claims())
        self.assertEqual(editor.toHtml(), original)
        editor.setReadOnly(False)
        self.page._set_controls_enabled(False)
        self.assertFalse(hasattr(panel, "replace_content_button"))

    def test_conversion_arrow_is_white_and_larger_with_large_text(self):
        self.page.ensurePolished()
        button = self.page.convert_button
        self.assertEqual(button.iconSize().width(), 32)
        self.assertGreaterEqual(button.minimumHeight(), 56)
        self.assertGreaterEqual(button.font().pointSizeF(), 18)
        pixels = button.icon().pixmap(64, 64, QIcon.Normal).toImage()
        opaque = [pixels.pixelColor(x, y) for y in range(pixels.height()) for x in range(pixels.width()) if pixels.pixelColor(x, y).alpha() > 128]
        self.assertTrue(opaque)
        self.assertTrue(all(color.red() == color.green() == color.blue() == 255 for color in opaque))


if __name__ == "__main__":
    unittest.main()
