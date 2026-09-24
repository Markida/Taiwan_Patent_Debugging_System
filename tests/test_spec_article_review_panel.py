import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QMimeData, Qt, QUrl, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QTreeWidget, QWidget

from app.styles import APP_STYLE
from features.taiwan_china_spec.article_review import find_article_occurrences
from features.taiwan_china_spec.converter import (
    NS, _windows_simplified_chinese, convert_document, parse_edited_preview_text,
)
from ui.spec_article_review import ArticleReviewPanel
from ui.taiwan_china_spec_page import PlainOnlyPreviewEditor, TaiwanChinaSpecPage


SECTION_TEXT = (
    "說明書摘要\n一個摘要元件。每一個摘要模組。\n"
    "權利要求書\n1. 一個請求元件。至少一個請求模組。\n"
    "說明書\n技術領域\n一個說明元件。各一個說明模組。\n"
    "附圖說明\n一個圖說元件。每一個圖說模組。\n"
    "具體實施方式\n"
)


def tree_items(panel):
    tree = panel.occurrence_list
    return [
        tree.topLevelItem(group).child(index)
        for group in range(tree.topLevelItemCount())
        for index in range(tree.topLevelItem(group).childCount())
    ]


def tree_hits(panel):
    return [item.data(0, Qt.UserRole) for item in tree_items(panel)]


def selected_starts(panel):
    return {
        item.data(0, Qt.UserRole).start
        for item in panel.occurrence_list.selectedItems()
        if item.data(0, Qt.UserRole) is not None
    }


def fake_dictionary_store():
    store = Mock()
    store.load_bundled.return_value = SimpleNamespace(
        pairs=(("模組", "模塊"),), source_kind="內建",
        source_path=Path("test_dictionary.txt"), warning="",
    )
    return store


class FakeDropEvent:
    def __init__(self, path):
        self.mime = QMimeData()
        self.mime.setUrls([QUrl.fromLocalFile(str(path))])
        self.accepted = False

    def type(self):
        return QEvent.Type.Drop

    def mimeData(self):
        return self.mime

    def acceptProposedAction(self):
        self.accepted = True


class ArticleReviewPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.editor = PlainOnlyPreviewEditor()
        self.editor.resize(650, 180)
        self.panel = ArticleReviewPanel(self.editor)
        self.panel.resize(300, 400)
        self.editor.show()
        self.panel.show()
        QApplication.processEvents()

    def tearDown(self):
        self.panel.close()
        self.editor.close()
        self.panel.deleteLater()
        self.editor.deleteLater()
        QApplication.processEvents()

    def load_text(self, text):
        self.editor.setPlainText(text)
        self.panel.reset()

    def load_html(self, html):
        self.editor.setHtml(html)
        self.panel.reset()

    def hits(self):
        return tree_hits(self.panel)

    def select_start(self, start):
        for item in tree_items(self.panel):
            if item.data(0, Qt.UserRole).start == start:
                self.panel.occurrence_list.setCurrentItem(item)
                return
        self.fail(f"No occurrence at UTF-16 position {start}")

    def test_five_sections_are_ordered_expanded_and_not_selectable(self):
        self.load_text(SECTION_TEXT.replace(
            "附圖說明\n", "發明內容\n一個內容元件。每一個內容模組。\n附圖說明\n"
        ))
        tree = self.panel.occurrence_list
        self.assertIsInstance(tree, QTreeWidget)
        self.assertEqual(tree.topLevelItemCount(), 5)
        for index, (label, section) in enumerate((
            ("說明書摘要", "abstract"), ("權利要求書", "claims"), ("說明書", "specification"),
            ("發明/實用新型內容", "invention_content"),
            ("附圖說明", "drawing_description"),
        )):
            with self.subTest(section=section):
                group = tree.topLevelItem(index)
                self.assertIn(label, group.text(0))
                self.assertTrue(group.isExpanded())
                self.assertFalse(group.flags() & Qt.ItemIsSelectable)
                self.assertEqual(group.childCount(), 2)
                self.assertTrue(group.child(0).data(0, Qt.UserRole).is_priority)
                for child_index in range(group.childCount()):
                    item = group.child(child_index)
                    self.assertEqual(item.data(0, Qt.UserRole).section, section)
                    self.assertTrue(item.flags() & Qt.ItemIsSelectable)
        self.assertEqual(len(self.hits()), 10)

    def test_empty_preview_still_has_five_groups_and_only_delete_button(self):
        self.load_text("")
        self.assertEqual(self.panel.occurrence_list.topLevelItemCount(), 5)
        self.assertEqual(self.hits(), [])
        self.assertEqual(self.panel.selection_hint.text(), "可使用shift/ctrl進行複數選取")
        labels = [button.text() for button in self.panel.findChildren(QPushButton)]
        self.assertEqual(set(labels), {"刪除所選項目"})
        self.assertFalse(self.panel.delete_selected_button.isEnabled())
        self.assertFalse(hasattr(self.panel, "replace_content_button"))
        self.assertFalse(hasattr(self.panel, "delete_unselected_button"))

    def test_content_group_highlights_deletes_and_undoes_only_its_selected_token(self):
        text = (
            "權利要求書\n一個相同元件。\n"
            "發明內容\n😀一個相同元件。每一個模組。\n"
            "附圖說明\n一個相同元件。"
        )
        self.load_text(text)
        group = self.panel.occurrence_list.topLevelItem(3)
        self.assertEqual(group.data(0, Qt.UserRole + 1), "invention_content")
        self.assertEqual(group.childCount(), 2)
        group.setExpanded(False)
        self.panel._rebuild_list()
        group = self.panel.occurrence_list.topLevelItem(3)
        self.assertFalse(group.isExpanded())
        group.setExpanded(True)
        hit = next(
            hit for hit in self.hits()
            if hit.section == "invention_content" and not hit.is_priority
        )
        self.select_start(hit.start)
        selections = self.editor.extraSelections()
        self.assertEqual(
            selections[0].cursor.selectedText(),
            "😀一個相同元件。",
        )
        self.panel.delete_selected_button.click()
        self.assertEqual(
            self.editor.toPlainText(),
            text.replace("😀一個相同元件", "😀相同元件"),
        )
        remaining = [hit for hit in self.hits() if hit.section == "invention_content"]
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].priority_reason, "每一個")
        self.editor.undo()
        self.assertEqual(self.editor.toPlainText(), text)
        self.assertEqual(
            len([hit for hit in self.hits() if hit.section == "invention_content"]),
            2,
        )

    def test_priority_order_is_stable_and_covers_every_occurrence(self):
        text = (
            "一個普通。每一個模組。各一個元件。另一個裝置。上一個位置。下一個位置。"
            "一個小空間。其中一個模組。其中另一個元件。至少一個裝置。第一個模組。"
        )
        self.load_text(text)
        expected = sorted(find_article_occurrences(text), key=lambda hit: (not hit.is_priority, hit.start))
        self.assertEqual(self.hits(), expected)
        self.assertEqual([hit.is_priority for hit in self.hits()], [True] * 10 + [False])
        self.assertEqual([hit.priority_reason for hit in self.hits()], [
            "每一個", "各一個", "另一個", "上一個", "下一個", "10字內接「空間」",
            "其中一個", "其中另一個", "至少一個", "第一個", "",
        ])

    def test_priority_phrases_can_be_selected_to_keep_before_delete_unselected(self):
        self.load_text(
            "一個普通。其中一個模組。另一個裝置。其中另一個元件。至少一個裝置。第一個模組。一個結尾。"
        )
        for item in tree_items(self.panel)[:5]:
            self.assertTrue(item.data(0, Qt.UserRole).is_priority)
            item.setSelected(True)
        self.panel.delete_unselected()
        self.assertEqual(self.editor.toPlainText(),
            "普通。其中一個模組。另一個裝置。其中另一個元件。至少一個裝置。第一個模組。結尾。")
        self.assertEqual(len(self.hits()), 5)
        self.assertEqual(selected_starts(self.panel), {hit.start for hit in self.hits()})
        self.assertTrue(self.panel.delete_selected_button.isEnabled())

    def test_selecting_item_scrolls_and_highlights_sentence_without_html_changes(self):
        self.load_html("<p>前言</p>" * 50
            + '<p>😀此處有<span style="color:#c62828">一個模組</span>，用於檢測。</p>')
        before_html, before_text = self.editor.toHtml(), self.editor.toPlainText()
        self.editor.verticalScrollBar().setValue(0)
        hit = self.hits()[0]
        self.select_start(hit.start)
        selections = self.editor.extraSelections()
        self.assertEqual(len(selections), 2)
        self.assertEqual(selections[0].cursor.selectedText(), hit.sentence)
        self.assertEqual(selections[0].format.background().color().name(), "#fff59d")
        self.assertEqual(selections[1].cursor.selectedText(), "一個")
        self.assertEqual(self.editor.textCursor().position(), hit.start)
        self.assertGreater(self.editor.verticalScrollBar().value(), 0)
        self.assertEqual(self.editor.toHtml(), before_html)
        self.assertEqual(self.editor.toPlainText(), before_text)
        self.assertFalse(self.panel.can_undo())

    def test_long_claim_uses_compact_distinct_context_rows(self):
        text = "模組" * 200 + "，".join(f"一個第{index}元件" for index in range(64)) + "。"
        self.load_text(text)
        self.assertEqual(len(self.hits()), 64)
        self.assertEqual(len({hit.start for hit in self.hits()}), 64)
        for item in tree_items(self.panel):
            self.assertEqual(item.data(0, Qt.UserRole).sentence, text)
            self.assertEqual(item.toolTip(0), text)
            self.assertIn("【一個】", item.text(0))
            self.assertLess(len(item.text(0)), 150)
        self.select_start(self.hits()[-1].start)
        selections = self.editor.extraSelections()
        self.assertEqual(selections[0].cursor.selectedText(), text)
        self.assertEqual(selections[1].cursor.selectedText(), "一個")

    def test_delete_selected_only_removes_selected_token_not_sentence_peers(self):
        self.load_text("一個甲與一個乙及一個丙。")
        self.select_start(self.hits()[1].start)
        QTest.mouseClick(self.panel.delete_selected_button, Qt.LeftButton)
        self.assertEqual(self.editor.toPlainText(), "一個甲與乙及一個丙。")
        self.assertEqual(len(self.hits()), 2)
        self.assertEqual(selected_starts(self.panel), {self.hits()[1].start})
        self.assertTrue(self.panel.delete_selected_button.isEnabled())

    def test_multi_selection_delete_selected_only_changes_chosen_occurrences(self):
        self.load_text("一個甲。一個乙。一個丙。")
        items = tree_items(self.panel)
        items[0].setSelected(True)
        items[2].setSelected(True)
        self.panel.delete_selected()
        self.assertEqual(self.editor.toPlainText(), "甲。一個乙。丙。")
        self.assertEqual(len(self.hits()), 1)
        self.assertEqual(self.hits()[0].sentence, "一個乙。")

    def test_ctrl_and_shift_click_allow_multiple_selection(self):
        self.load_text("一個甲。一個乙。一個丙。")
        tree = self.panel.occurrence_list
        items = tree_items(self.panel)
        QApplication.processEvents()
        QTest.mouseClick(tree.viewport(), Qt.LeftButton,
                         pos=tree.visualItemRect(items[0]).center())
        QTest.mouseClick(tree.viewport(), Qt.LeftButton, Qt.ControlModifier,
                         pos=tree.visualItemRect(items[2]).center())
        self.assertEqual(selected_starts(self.panel), {
            items[0].data(0, Qt.UserRole).start, items[2].data(0, Qt.UserRole).start,
        })
        tree.clearSelection()
        QTest.mouseClick(tree.viewport(), Qt.LeftButton,
                         pos=tree.visualItemRect(items[0]).center())
        QTest.mouseClick(tree.viewport(), Qt.LeftButton, Qt.ShiftModifier,
                         pos=tree.visualItemRect(items[2]).center())
        self.assertEqual(selected_starts(self.panel), {hit.start for hit in self.hits()})

    def test_auto_advance_follows_priority_list_order_not_document_order(self):
        self.load_text("一個甲。每一個乙。一個丙。")
        self.select_start(self.hits()[0].start)
        self.panel.delete_selected()
        current = self.panel.occurrence_list.currentItem()
        hit = current.data(0, Qt.UserRole)
        self.assertEqual(hit.sentence, "一個甲。")
        self.assertEqual(selected_starts(self.panel), {hit.start})
        self.assertEqual(self.editor.textCursor().position(), hit.start)
        highlights = self.editor.extraSelections()
        self.assertEqual(highlights[0].cursor.selectedText(), "一個甲。")
        self.assertEqual(highlights[1].cursor.selectedText(), "一個")

    def test_auto_advance_crosses_empty_group_and_expands_next_section(self):
        self.load_text("說明書摘要\n一個甲。\n權利要求書\n說明書\n一個乙。")
        self.panel.occurrence_list.topLevelItem(2).setExpanded(False)
        self.select_start(self.hits()[0].start)
        self.panel.delete_selected()
        current = self.panel.occurrence_list.currentItem()
        self.assertEqual(current.data(0, Qt.UserRole).section, "specification")
        self.assertTrue(current.parent().isExpanded())
        self.assertTrue(current.isSelected())

    def test_disjoint_deletion_continues_after_first_removed_row_without_skipping_gap(self):
        self.load_text("一個甲。一個乙。一個丙。一個丁。")
        items = tree_items(self.panel)
        items[0].setSelected(True)
        items[2].setSelected(True)
        self.panel.delete_selected()
        self.assertEqual(self.editor.toPlainText(), "甲。一個乙。丙。一個丁。")
        current = self.panel.occurrence_list.currentItem()
        self.assertEqual(current.data(0, Qt.UserRole).sentence, "一個乙。")
        self.assertEqual(len(self.panel.occurrence_list.selectedItems()), 1)

    def test_deleting_final_row_selects_previous_survivor(self):
        self.load_text("一個甲。一個乙。一個丙。")
        self.select_start(self.hits()[-1].start)
        self.panel.delete_selected()
        self.assertEqual(self.panel.occurrence_list.currentItem().data(0, Qt.UserRole).sentence, "一個乙。")
        self.assertTrue(self.panel.delete_selected_button.isEnabled())

    def test_repeated_delete_clicks_keep_advancing_until_empty(self):
        self.load_text("一個甲。一個乙。一個丙。")
        self.select_start(self.hits()[0].start)
        for expected_count in (2, 1, 0):
            QTest.mouseClick(self.panel.delete_selected_button, Qt.LeftButton)
            self.assertEqual(len(self.hits()), expected_count)
            self.assertEqual(len(selected_starts(self.panel)), min(expected_count, 1))
        self.assertEqual(self.editor.toPlainText(), "甲。乙。丙。")
        self.assertIsNone(self.panel.occurrence_list.currentItem())
        self.assertEqual(self.editor.extraSelections(), [])
        self.assertFalse(self.panel.delete_selected_button.isEnabled())

    def test_auto_advance_rebases_utf16_and_undo_restores_original_selection_and_html(self):
        self.load_html('<p>😀一個甲。𠮷<span style="color:#c62828">一個乙</span>。一個丙。</p>')
        before_html = self.editor.toHtml()
        before = self.hits()
        self.select_start(before[0].start)
        self.panel.delete_selected()
        self.assertEqual(selected_starts(self.panel), {before[1].start - 2})
        self.assertEqual(self.editor.textCursor().position(), before[1].start - 2)
        self.panel.undo()
        self.assertEqual(self.editor.toHtml(), before_html)
        self.assertEqual(selected_starts(self.panel), {before[0].start})
        self.panel.redo()
        self.assertEqual(selected_starts(self.panel), {before[1].start - 2})

    def test_auto_advance_can_select_a_new_token_created_by_joining_text(self):
        self.load_text("一一個個")
        self.select_start(self.hits()[0].start)
        self.panel.delete_selected()
        self.assertEqual(self.editor.toPlainText(), "一個")
        self.assertEqual(selected_starts(self.panel), {0})
        self.panel.delete_selected()
        self.assertEqual(self.editor.toPlainText(), "")

    def test_auto_advance_scrolls_next_item_into_view(self):
        self.load_text("\n".join(f"一個第{index}元件。" for index in range(60)))
        self.select_start(self.hits()[55].start)
        self.panel.delete_selected()
        QApplication.processEvents()
        tree = self.panel.occurrence_list
        self.assertEqual(tree.currentItem().data(0, Qt.UserRole).sentence, "一個第56元件。")
        self.assertTrue(tree.visualItemRect(tree.currentItem()).intersects(tree.viewport().rect()))
        self.assertGreater(self.editor.verticalScrollBar().value(), 0)

    def test_new_token_inserted_before_selected_one_does_not_inherit_selection(self):
        self.load_text("一個甲。一個乙。")
        target = self.hits()[1]
        self.select_start(target.start)
        cursor = self.editor.textCursor()
        cursor.setPosition(target.start)
        cursor.insertText("一個新。")
        self.assertEqual(len(self.hits()), 3)
        self.assertEqual(selected_starts(self.panel), {target.start + 4})
        self.panel.delete_unselected()
        self.assertEqual(self.editor.toPlainText(), "甲。新。一個乙。")

    def test_delete_unselected_preserves_selected_middle_and_red_format(self):
        self.load_html('<p>一個甲與<span style="color:#c62828">一個乙</span>及一個丙。</p>')
        middle = self.hits()[1]
        self.select_start(middle.start)
        self.panel.delete_unselected()
        self.assertEqual(self.editor.toPlainText(), "甲與一個乙及丙。")
        self.assertEqual([hit.start for hit in self.hits()], [middle.start - 2])
        self.assertEqual(selected_starts(self.panel), {middle.start - 2})
        self.assertIn("#c62828", self.editor.toHtml())

    def test_delete_unselected_includes_collapsed_groups_and_keeps_cross_group_selection(self):
        self.load_text(SECTION_TEXT)
        original_hits = find_article_occurrences(SECTION_TEXT)
        kept_starts = set()
        for group_index in (0, 2):
            group = self.panel.occurrence_list.topLevelItem(group_index)
            item = group.child(0)
            item.setSelected(True)
            kept_starts.add(item.data(0, Qt.UserRole).start)
            group.setExpanded(False)
        self.panel.occurrence_list.topLevelItem(1).setExpanded(False)
        self.panel.delete_unselected()
        expected = SECTION_TEXT.encode("utf-16-le")
        for hit in reversed(original_hits):
            if hit.start not in kept_starts:
                expected = expected[:hit.start * 2] + expected[hit.end * 2:]
        self.assertEqual(self.editor.toPlainText(), expected.decode("utf-16-le"))
        self.assertEqual([hit.section for hit in self.hits()], ["abstract", "specification"])
        self.assertEqual(selected_starts(self.panel), {hit.start for hit in self.hits()})

    def test_no_selection_delete_selected_is_noop_and_delete_unselected_removes_all(self):
        self.load_text(SECTION_TEXT)
        self.panel.delete_selected()
        self.assertEqual(self.editor.toPlainText(), SECTION_TEXT)
        self.assertFalse(self.panel.can_undo())
        self.panel.delete_unselected()
        self.assertEqual(self.editor.toPlainText(), SECTION_TEXT.replace("一個", ""))
        self.assertEqual(self.hits(), [])
        self.assertTrue(self.panel.can_undo())

    def test_all_selected_delete_unselected_is_noop(self):
        self.load_text("一個甲。一個乙。")
        for item in tree_items(self.panel):
            item.setSelected(True)
        self.panel.delete_unselected()
        self.assertEqual(self.editor.toPlainText(), "一個甲。一個乙。")
        self.assertFalse(self.panel.can_undo())
        self.panel.delete_selected()
        self.assertEqual(self.editor.toPlainText(), "甲。乙。")

    def test_undo_batch_restores_html_all_hits_and_selected_occurrence(self):
        self.load_html('<p>一個甲與<span style="color:#c62828">一個乙</span>及一個丙。</p>')
        original_html, original_text, original_hits = self.editor.toHtml(), self.editor.toPlainText(), self.hits()
        self.select_start(original_hits[1].start)
        self.panel.delete_unselected()
        self.panel.undo()
        self.assertEqual(self.editor.toHtml(), original_html)
        self.assertEqual(self.editor.toPlainText(), original_text)
        self.assertEqual(self.hits(), original_hits)
        self.assertEqual(selected_starts(self.panel), {original_hits[1].start})
        self.assertFalse(self.panel.can_undo())
        self.panel.redo()
        self.assertEqual(self.editor.toPlainText(), "甲與一個乙及丙。")
        self.assertEqual(selected_starts(self.panel), {original_hits[1].start - 2})

    def test_manual_insertion_before_selected_token_rebases_utf16_and_undo_restores(self):
        self.load_text("😀一個甲。一個乙。")
        original_text, original_hits = self.editor.toPlainText(), self.hits()
        chosen_start = original_hits[1].start
        self.select_start(chosen_start)
        cursor = self.editor.textCursor()
        cursor.setPosition(0)
        cursor.insertText("𠮷新")
        self.assertEqual(selected_starts(self.panel), {chosen_start + 3})
        self.assertEqual(len(self.hits()), 2)
        self.panel.undo()
        self.assertEqual(self.editor.toPlainText(), original_text)
        self.assertEqual(selected_starts(self.panel), {chosen_start})
        self.assertEqual(self.hits(), original_hits)

    def test_manual_edit_inside_selected_token_clears_selection_and_undo_restores(self):
        self.load_text("一個甲。每一個乙。")
        target = next(hit for hit in self.hits() if hit.is_priority)
        self.select_start(target.start)
        cursor = self.editor.textCursor()
        cursor.setPosition(target.start + 1)
        cursor.insertText("兩")
        self.assertEqual(selected_starts(self.panel), set())
        self.panel.undo()
        self.assertEqual(self.editor.toPlainText(), "一個甲。每一個乙。")
        self.assertEqual(selected_starts(self.panel), {target.start})

    def test_coalesced_edits_around_selected_middle_preserve_token_for_delete_unselected(self):
        original = "一個甲。每一個乙。一個丙。"
        self.load_text(original)
        target = next(hit for hit in self.hits() if hit.is_priority)
        self.select_start(target.start)
        changes = []
        self.editor.document().contentsChange.connect(
            lambda position, removed, added: changes.append((position, removed, added)))
        cursor = self.editor.textCursor()
        cursor.beginEditBlock()
        cursor.setPosition(0)
        cursor.insertText("😀前")
        cursor.movePosition(QTextCursor.End)
        cursor.insertText("後")
        cursor.endEditBlock()
        self.assertEqual(len(changes), 1)
        self.assertGreater(changes[0][1], 0)
        self.assertEqual(self.editor.toPlainText(), "😀前" + original + "後")
        self.assertEqual(selected_starts(self.panel), {target.start + 3})
        self.assertEqual(len(self.hits()), 3)
        self.panel.delete_unselected()
        self.assertEqual(self.editor.toPlainText(), "😀前甲。每一個乙。丙。後")
        self.assertEqual(selected_starts(self.panel), {target.start + 1})
        self.panel.undo()
        self.assertEqual(self.editor.toPlainText(), "😀前" + original + "後")
        self.assertEqual(selected_starts(self.panel), {target.start + 3})
        self.panel.undo()
        self.assertEqual(self.editor.toPlainText(), original)
        self.assertEqual(selected_starts(self.panel), {target.start})

    def test_insertion_at_selected_start_shifts_identity(self):
        self.load_text("一個甲。每一個乙。一個丙。")
        target = next(hit for hit in self.hits() if hit.is_priority)
        self.select_start(target.start)
        cursor = self.editor.textCursor()
        cursor.setPosition(target.start)
        cursor.insertText("新😀")
        self.assertEqual(selected_starts(self.panel), {target.start + 3})
        self.panel.delete_unselected()
        self.assertEqual(self.editor.toPlainText(), "甲。每新😀一個乙。丙。")
        self.assertEqual(selected_starts(self.panel), {target.start + 1})

    def test_insertion_at_selected_end_keeps_identity(self):
        self.load_text("一個甲。每一個乙。一個丙。")
        target = next(hit for hit in self.hits() if hit.is_priority)
        self.select_start(target.start)
        cursor = self.editor.textCursor()
        cursor.setPosition(target.end)
        cursor.insertText("新😀")
        self.assertEqual(selected_starts(self.panel), {target.start})
        self.panel.delete_unselected()
        self.assertEqual(self.editor.toPlainText(), "甲。每一個新😀乙。丙。")
        self.assertEqual(selected_starts(self.panel), {target.start - 2})

    def test_insertion_inside_selected_token_cannot_delete_partial_or_new_words(self):
        self.load_text("一個甲。每一個乙。一個丙。")
        target = next(hit for hit in self.hits() if hit.is_priority)
        self.select_start(target.start)
        cursor = self.editor.textCursor()
        cursor.setPosition(target.start + 1)
        cursor.insertText("新😀")
        self.assertEqual(selected_starts(self.panel), set())
        self.assertEqual(len(self.hits()), 2)
        self.panel.delete_unselected()
        self.assertEqual(self.editor.toPlainText(), "甲。每一新😀個乙。丙。")

    def test_changing_selection_after_manual_edit_controls_next_deletion(self):
        self.load_text("一個甲。一個乙。一個丙。")
        self.select_start(self.hits()[0].start)
        cursor = self.editor.textCursor()
        cursor.setPosition(0)
        cursor.insertText("前")
        self.select_start(self.hits()[2].start)
        self.panel.delete_unselected()
        self.assertEqual(self.editor.toPlainText(), "前甲。乙。一個丙。")
        self.assertEqual(selected_starts(self.panel), {self.hits()[0].start})

    def test_ctrl_z_and_ctrl_y_restore_batch_and_selection_without_back_button(self):
        self.load_text("一個甲。一個乙。")
        first = self.hits()[0]
        self.select_start(first.start)
        self.panel.delete_unselected()
        self.editor.setFocus()
        QTest.keyClick(self.editor, Qt.Key_Z, Qt.ControlModifier)
        self.assertEqual(self.editor.toPlainText(), "一個甲。一個乙。")
        self.assertEqual(selected_starts(self.panel), {first.start})
        QTest.keyClick(self.editor, Qt.Key_Y, Qt.ControlModifier)
        self.assertEqual(self.editor.toPlainText(), "一個甲。乙。")
        self.assertEqual(selected_starts(self.panel), {first.start})
        self.assertFalse(hasattr(self.panel, "back_button"))

    def test_reset_clears_history_selection_and_highlights_for_new_document(self):
        self.load_text("一個舊內容。")
        self.select_start(self.hits()[0].start)
        self.panel.delete_selected()
        self.editor.setPlainText("一個新內容。")
        self.panel.reset()
        self.assertFalse(self.panel.can_undo())
        self.assertFalse(self.panel.can_redo())
        self.assertEqual(selected_starts(self.panel), set())
        self.assertEqual(self.editor.extraSelections(), [])
        self.panel.undo()
        self.editor.undo()
        self.assertEqual(self.editor.toPlainText(), "一個新內容。")

    def test_disabled_or_readonly_editor_prevents_operations_and_disables_controls(self):
        for mode in ("disabled", "readonly"):
            with self.subTest(mode=mode):
                self.editor.setEnabled(True)
                self.editor.setReadOnly(False)
                self.load_text("一個甲。一個乙。一個丙。")
                self.select_start(self.hits()[0].start)
                self.panel.delete_selected()
                self.select_start(self.hits()[0].start)
                before = self.editor.toHtml(), self.hits(), selected_starts(self.panel)
                if mode == "disabled":
                    self.editor.setEnabled(False)
                else:
                    self.editor.setReadOnly(True)
                self.panel._update_buttons()
                self.assertFalse(self.panel.delete_selected_button.isEnabled())
                self.panel.delete_selected()
                self.panel.delete_unselected()
                self.panel.undo()
                self.panel.redo()
                self.assertEqual((self.editor.toHtml(), self.hits(), selected_starts(self.panel)), before)

    def test_plain_paste_keeps_only_text_and_participates_in_shared_undo(self):
        self.load_text("一個甲。")
        source = QMimeData()
        source.setText("一個乙。")
        source.setHtml('<b style="color:green">一個乙。</b>')
        cursor = self.editor.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.editor.setTextCursor(cursor)
        self.editor.insertFromMimeData(source)
        self.assertEqual(self.editor.toPlainText(), "一個甲。一個乙。")
        self.assertEqual(len(self.hits()), 2)
        self.assertNotIn("#008000", self.editor.toHtml())
        self.editor.undo()
        self.assertEqual(self.editor.toPlainText(), "一個甲。")
        self.assertEqual(len(self.hits()), 1)


class ArticleReviewPageIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.store = fake_dictionary_store()
        self.page = TaiwanChinaSpecPage(lambda: None, dictionary_store=self.store, auto_load_dictionary=False)
        self.page.setStyleSheet(APP_STYLE)

    def tearDown(self):
        self.page.close()
        self.page.deleteLater()
        QApplication.processEvents()

    def wait_for_preview(self):
        for _attempt in range(400):
            QApplication.processEvents()
            if self.page._preview_thread is None and self.page._pending_preview is None:
                return
            time.sleep(0.005)
        self.fail("文件預覽未在時限內完成")

    def assert_fits_layout(self):
        for width, height in ((960, 640), (1360, 860)):
            with self.subTest(size=(width, height)):
                self.page.resize(width, height)
                self.page.show()
                QApplication.processEvents()
                self.assertEqual((self.page.width(), self.page.height()), (width, height))
                self.assertLessEqual(self.page.minimumSizeHint().width(), width)
                self.assertLessEqual(self.page.minimumSizeHint().height(), height)
                review, messages = self.page.article_review, self.page.message_panel
                self.assertLess(review.height() / 5, messages.height())
                self.assertGreater(review.height(), messages.height() * 2)
                self.assertGreater(messages.y(), review.y() + review.height())
                self.assertGreater(self.page.preview_output.width(), review.width())
                for control in (
                    review.occurrence_list, review.selection_hint,
                    review.delete_selected_button,
                    self.page.message_output, self.page.preview_output, self.page.preview_status,
                    self.page.dictionary_manager_button, self.page.convert_button,
                ):
                    self.assertTrue(control.isVisible())
                    top_left = control.mapTo(self.page, control.rect().topLeft())
                    bottom_right = control.mapTo(self.page, control.rect().bottomRight())
                    self.assertTrue(self.page.rect().contains(top_left), control.objectName())
                    self.assertTrue(self.page.rect().contains(bottom_right), control.objectName())
                self.assertGreaterEqual(review.occurrence_list.height(), 80)
                self.assertEqual(review.occurrence_list.horizontalScrollBarPolicy(), Qt.ScrollBarAlwaysOff)

    def test_sidebar_fits_small_and_large_layout_with_lower_smaller_messages(self):
        self.assert_fits_layout()

    def test_preview_legend_omits_editable_notice_but_keeps_red_change_legend(self):
        labels = [label.text() for label in self.page.findChildren(QLabel)]
        self.assertFalse(any("繁體可編輯" in text for text in labels))
        self.assertIn("紅字＝自動變更", labels)
        self.assertFalse(self.page.preview_output.isReadOnly())

    def test_conversion_busy_state_disables_panel_operations(self):
        self.page.preview_output.setPlainText("一個甲。一個乙。")
        panel = self.page.article_review
        panel.reset()
        panel.occurrence_list.setCurrentItem(tree_items(panel)[0])
        self.page._set_controls_enabled(False)
        self.assertFalse(panel.isEnabled())
        self.assertFalse(panel.delete_selected_button.isEnabled())
        self.page._set_controls_enabled(True)
        self.assertTrue(panel.isEnabled())
        self.assertTrue(panel.delete_selected_button.isEnabled())

    def test_source_loaded_sidebar_and_preview_still_fit_supported_sizes(self):
        source = Path(__file__).resolve().parent / "fixtures" / "TW_方向校正專利圖式標號檢核系統_發明說明書.docx"
        self.assertTrue(self.page.load_source(str(source)))
        self.wait_for_preview()
        text = self.page.preview_output.toPlainText()
        expected = find_article_occurrences(text)
        self.assertIn("說明書摘要", text)
        self.assertGreater(len(expected), 0)
        self.assertEqual(len(tree_hits(self.page.article_review)), len(expected))
        self.assertFalse(self.page.article_review.can_undo())
        self.assert_fits_layout()
        self.store.load_preferred.assert_not_called()
        self.store.upload_pairs.assert_not_called()

    def test_claim_update_button_preserves_effect_and_other_content_then_undoes_atomically(self):
        source = Path(__file__).resolve().parent / "fixtures" / "TW_方向校正專利圖式標號檢核系統_發明說明書.docx"
        self.assertTrue(self.page.load_source(str(source)))
        self.wait_for_preview()
        editor = self.page.preview_output
        original = editor.toPlainText()
        parsed_before = parse_edited_preview_text(
            original, self.page._preview_specification_kind
        )
        preserved = [
            paragraph for paragraph in parsed_before.invention_content
            if "有益效果" in paragraph or "功效" in paragraph
        ]
        self.assertTrue(preserved)
        claims_start = original.index("權利要求書\n") + len("權利要求書\n")
        claims_end = original.index("說明書\n", claims_start)
        revised_claims = (
            "1. 一種整合測試裝置，其特徵在於：，包括所述測試元件。\n"
            "2. 根據權利要求1所述的整合測試裝置，其特徵在於：，所述測試元件可移動。\n"
        )
        editor.setPlainText(original[:claims_start] + revised_claims + original[claims_end:])
        before_update = editor.toPlainText()

        self.assertTrue(self.page.replace_content_from_claims())

        updated = editor.toPlainText()
        parsed_after = parse_edited_preview_text(
            updated, self.page._preview_specification_kind
        )
        self.assertIn(
            "本發明的整合測試裝置，包括所述測試元件。",
            parsed_after.invention_content,
        )
        self.assertIn(
            "本發明的整合測試裝置，所述測試元件可移動。",
            parsed_after.invention_content,
        )
        for paragraph in preserved:
            self.assertEqual(parsed_after.invention_content.count(paragraph), 1)
        self.assertNotIn("其特徵在於：，", "\n".join(parsed_after.invention_content))
        self.assertNotIn("，，", "\n".join(parsed_after.invention_content))
        self.assertIn("原有功效、其他說明", self.page.message_output.toPlainText())
        self.assertIn(
            tuple(parse_edited_preview_text(updated, self.page._preview_specification_kind).claims),
            self.page._content_claim_history,
        )

        self.page.article_review.undo()
        self.assertEqual(editor.toPlainText(), before_update)
        self.assertTrue(self.page.replace_content_from_claims())
        self.assertEqual(editor.toPlainText(), updated)

        second = editor.toPlainText().replace(
            "一種整合測試裝置，其特徵在於：，包括所述測試元件。",
            "一種再次修改裝置，其特徵在於：，包括所述新元件。",
            1,
        ).replace(
            "根據權利要求1所述的整合測試裝置，其特徵在於：，所述測試元件可移動。",
            "根據權利要求1所述的再次修改裝置，其特徵在於：，所述新元件可轉動。",
            1,
        )
        editor.setPlainText(second)
        self.assertTrue(self.page.replace_content_from_claims())
        final = parse_edited_preview_text(
            editor.toPlainText(), self.page._preview_specification_kind
        )
        joined = "\n".join(final.invention_content)
        self.assertIn("本發明的再次修改裝置，包括所述新元件。", joined)
        self.assertIn("本發明的再次修改裝置，所述新元件可轉動。", joined)
        self.assertNotIn("本發明的整合測試裝置，包括所述測試元件。", joined)
        for paragraph in preserved:
            self.assertEqual(final.invention_content.count(paragraph), 1)

    def test_new_sidebar_drop_targets_use_isolated_word_loader(self):
        from app.main_window import MainWindow

        class FakeChatPage(QWidget):
            unread_count_changed = Signal(int)

            def __init__(self, **kwargs):
                super().__init__()

            def start_background_monitoring(self):
                pass

            def shutdown(self):
                pass

        with patch("ui.chat_room_page.ChatRoomPage", FakeChatPage):
            window = MainWindow()
        try:
            definition = dict(window._feature_definitions["taiwan_china_spec"])
            definition["page_class"] = lambda **kwargs: self.page
            window._feature_definitions["taiwan_china_spec"] = definition
            self.assertIs(window.feature_pages["taiwan_china_spec"], self.page)
            self.assertTrue(self.page.uses_isolated_file_drop)
            targets = (
                self.page.article_review, self.page.article_review.occurrence_list.viewport(),
                self.page.article_review.selection_hint,
                self.page.article_review.delete_selected_button,
                self.page.message_output.viewport(),
            )
            installed = window.routed_file_drop_controller._installed_widgets
            dropped = []
            self.page.file_drop_controller.on_file = dropped.append
            source = Path(__file__).resolve().parent / "fixtures" / "TW_方向校正專利圖式標號檢核系統_發明說明書.docx"
            for target in targets:
                with self.subTest(target=target):
                    self.assertNotIn(target, installed)
                    self.assertTrue(target.acceptDrops())
                    event = FakeDropEvent(source)
                    self.assertTrue(self.page.file_drop_controller.eventFilter(target, event))
                    self.assertTrue(event.accepted)
            self.assertEqual(dropped, [str(source)] * len(targets))
            self.store.load_preferred.assert_not_called()
            self.store.upload_pairs.assert_not_called()
        finally:
            window.removeWidget(self.page)
            self.page.setParent(None)
            window.close()
            window.deleteLater()
            QApplication.processEvents()

    def test_reviewed_preview_drives_simplified_docx_without_highlight_or_lost_content(self):
        source = Path(__file__).resolve().parent / "fixtures" / "TW_方向校正專利圖式標號檢核系統_發明說明書.docx"
        self.assertTrue(self.page.load_source(str(source)))
        self.wait_for_preview()
        editor, panel = self.page.preview_output, self.page.article_review
        original = editor.toPlainText()
        original_hits = find_article_occurrences(original)
        self.assertGreaterEqual(len(original_hits), 2)
        kept_item = tree_items(panel)[0]
        kept = kept_item.data(0, Qt.UserRole)
        panel.occurrence_list.setCurrentItem(kept_item)
        self.assertEqual(len(editor.extraSelections()), 2)
        panel.delete_unselected()
        expected_bytes = original.encode("utf-16-le")
        for hit in reversed(original_hits):
            if hit.start != kept.start:
                expected_bytes = expected_bytes[:hit.start * 2] + expected_bytes[hit.end * 2:]
        reviewed = editor.toPlainText()
        self.assertEqual(reviewed, expected_bytes.decode("utf-16-le"))
        self.assertEqual(reviewed.count("一個"), 1)
        parsed_before = parse_edited_preview_text(original, self.page._preview_specification_kind)
        parsed_after = parse_edited_preview_text(reviewed, self.page._preview_specification_kind)
        self.assertEqual(len(parsed_after.abstract), len(parsed_before.abstract))
        self.assertEqual(len(parsed_after.claims), len(parsed_before.claims))
        self.assertTrue(self.page._preview_dirty)
        self.assertEqual(len(tree_hits(panel)), 1)
        self.assertEqual(selected_starts(panel), {tree_hits(panel)[0].start})
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "article-reviewed.docx"
            report = convert_document(
                source, output, template_key=self.page.template_combo.currentData(),
                terminology_pairs=self.page._dictionary_pairs_from_table(), edited_preview_text=reviewed,
            )
            self.assertTrue(report.ok)
            with zipfile.ZipFile(output) as package:
                root = ET.fromstring(package.read("word/document.xml"))
        paragraphs = [
            "".join(node.text or "" for node in paragraph.findall(".//w:t", NS))
            for paragraph in root.findall(".//w:p", NS)
        ]
        document_text = "\n".join(paragraphs)
        self.assertEqual(document_text.count("一个"), 1)
        self.assertNotIn("一個", document_text)
        self.assertIn(_windows_simplified_chinese(parsed_after.title), paragraphs)
        for section in (
            "abstract", "claims", "technology_field", "background",
            "invention_content", "drawing_description", "embodiments",
        ):
            with self.subTest(section=section):
                for paragraph in getattr(parsed_after, section):
                    self.assertIn(_windows_simplified_chinese(paragraph), paragraphs)
        self.assertEqual(report.claim_count, len(parsed_after.claims))
        self.assertEqual(root.findall(".//w:highlight", NS), [])
        self.assertFalse(any(
            node.get(f"{{{NS['w']}}}fill", "").lower() in {"fff59d", "ffcc80", "ffff00"}
            for node in root.findall(".//w:shd", NS)
        ))


if __name__ == "__main__":
    unittest.main()
