import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QLabel

from app.config import MAX_IMAGE_PREVIEW_ZOOM
from app.workflow_context import PatentWorkflowContext
from features.patent_review.models import (
    PatentDocument,
    PatentParagraph,
    TextRunSpan,
)
from features.patent_review.section_parser import assign_sections
from features.patent_review.text_normalizer import normalize_patent_text
from ui.embodiment_figure_compare_page import EmbodimentFigureComparePage


def build_document():
    lines = [
        "【新型說明書】",
        "【中文新型名稱】測試裝置",
        "【實施方式】",
        "參閱圖1，底座1連接上蓋2及感測器A。",
        "該底座1支撐上蓋2及感測器A。",
        "【符號說明】",
        "1:底座",
        "2:上蓋",
        "A:感測器",
    ]
    paragraphs = [
        PatentParagraph(
            index=index,
            text=text,
            normalized_text=normalize_patent_text(text),
            source_path=f"body/p[{index}]",
            run_spans=[TextRunSpan(0, 0, len(text), text)],
        )
        for index, text in enumerate(lines)
    ]
    sections, patent_type, title = assign_sections(paragraphs)
    for value, index in enumerate((3, 4), start=1):
        paragraphs[index].numbering_id = 10
        paragraphs[index].numbering_value = value
        paragraphs[index].numbering_text = f"【{value:04d}】"
    return PatentDocument(
        source_path="comparison.docx",
        file_name="comparison.docx",
        file_size_bytes=0,
        sha256="f" * 64,
        patent_type=patent_type,
        patent_title=title,
        paragraphs=paragraphs,
        sections=sections,
    )


def add_drawing_description(document, *caption_lines):
    text = "\n".join(caption_lines)
    document.paragraphs.append(
        PatentParagraph(
            index=len(document.paragraphs),
            text=text,
            normalized_text=normalize_patent_text(text),
            source_path="body/drawing-description-count",
            section_key="brief_description_of_drawings",
            section_title="圖式簡單說明",
            content_text=normalize_patent_text(text),
            run_spans=[TextRunSpan(0, 0, len(text), text)],
        )
    )
    return document


class EmbodimentFigureComparePageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.context = PatentWorkflowContext()
        self.opened = []
        self.page = EmbodimentFigureComparePage(lambda: None)
        self.page.set_open_feature_callback(self.opened.append)
        self.page.set_workflow_context(self.context)

    def tearDown(self):
        self.page.deleteLater()

    def test_two_windows_allow_independent_paragraph_and_figure_selection(self):
        with TemporaryDirectory() as temporary_directory:
            first_path = Path(temporary_directory) / "圖1.png"
            second_path = Path(temporary_directory) / "圖2.png"
            for path, color in (
                (first_path, QColor("white")),
                (second_path, QColor("lightgray")),
            ):
                image = QImage(240, 160, QImage.Format_RGB32)
                image.fill(color)
                self.assertTrue(image.save(str(path)))

            self.context.publish_document(build_document())
            self.context.publish_ocr_results(
                [
                    {
                        "image_path": str(first_path),
                        "source_image_name": first_path.name,
                        "figure_numbers": [1],
                        "numbers": ["1", "2", "A"],
                    },
                    {
                        "image_path": str(second_path),
                        "source_image_name": second_path.name,
                        "figure_numbers": [2],
                        "numbers": ["9"],
                    },
                ]
            )

            self.assertEqual(self.page.paragraph_combo.count(), 2)
            self.assertEqual(self.page.paragraph_table.rowCount(), 2)
            self.assertEqual(self.page.figure_combo.count(), 2)
            self.assertIn("【0001】", self.page.paragraph_combo.itemText(0))
            self.assertIn("圖2", self.page.figure_combo.itemText(1))
            self.assertEqual(
                self.page.comparisons[0].paragraph_labels,
                ["1", "2", "A"],
            )

            self.page.paragraph_combo.setCurrentIndex(1)
            self.page.figure_combo.setCurrentIndex(1)
            self.assertEqual(self.page.paragraph_combo.currentIndex(), 1)
            self.assertEqual(self.page.figure_combo.currentIndex(), 1)
            self.assertIn("人工切換的其他圖式頁", self.page.comparison_result.text())
            self.assertNotIn("圖式有、段落沒有", self.page.comparison_result.text())

            self.assertTrue(self.page.select_referenced_figure_page())
            self.assertEqual(self.page.figure_combo.currentIndex(), 0)
            self.assertIn("圖式缺失標號：無", self.page.comparison_result.text())

    def test_redundant_paragraph_prompts_are_not_shown(self):
        visible_label_texts = [
            label.text()
            for label in self.page.findChildren(QLabel)
            if not label.isHidden()
        ]

        self.assertNotIn("選擇實施方式段落", visible_label_texts)
        self.assertFalse(hasattr(self.page, "paragraph_meta"))
        self.assertNotIn("沿用上一段參閱圖式", "".join(visible_label_texts))

    def test_figure_count_warning_is_pinned_and_tracks_both_sources(self):
        left_layout = self.page.figure_count_warning.parentWidget().layout()
        self.assertLess(
            left_layout.indexOf(self.page.figure_count_warning),
            left_layout.indexOf(self.page.paragraph_workspace_splitter),
        )

        document = add_drawing_description(
            build_document(),
            "圖1為測試裝置的立體圖。",
            "圖2為測試裝置的側視圖。",
        )
        self.context.publish_document(document)
        self.assertTrue(self.page.figure_count_warning.isHidden())

        self.context.publish_ocr_results(
            [{"figure_numbers": [1, 2], "numbers": []}]
        )
        self.assertTrue(self.page.figure_count_warning.isHidden())

        self.context.publish_ocr_results(
            [{"figure_numbers": [1], "numbers": []}]
        )
        self.assertFalse(self.page.figure_count_warning.isHidden())
        self.assertIn("圖式簡單說明", self.page.figure_count_warning.text())
        self.assertIn("共 2 個圖號", self.page.figure_count_warning.text())
        self.assertIn("共設定 1 個圖號", self.page.figure_count_warning.text())

        self.context.publish_ocr_results(
            [
                {"figure_numbers": [1, 2], "numbers": []},
                {"figure_numbers": [2], "numbers": []},
            ]
        )
        self.assertTrue(self.page.figure_count_warning.isHidden())

        self.context.clear_ocr_results()
        self.assertTrue(self.page.figure_count_warning.isHidden())

    def test_drawing_names_are_paired_with_figures_in_selector_and_meta(self):
        self.context.publish_ocr_results(
            [
                {
                    "source_image_name": "drawing-page.png",
                    "figure_numbers": [1, 2],
                    "numbers": [],
                }
            ]
        )
        self.assertNotIn("立體圖", self.page.figure_combo.itemText(0))

        document = add_drawing_description(
            build_document(),
            "圖1為測試裝置的立體圖。",
            "圖2為測試裝置的剖視圖。",
        )
        self.context.publish_document(document)

        selector_text = self.page.figure_combo.itemText(0)
        self.assertIn("圖1（立體圖）", selector_text)
        self.assertIn("圖2（剖視圖）", selector_text)
        self.assertIn("drawing-page.png", selector_text)
        self.assertIn("圖1（立體圖）", self.page.figure_meta.text())
        self.assertIn("圖2（剖視圖）", self.page.figure_meta.text())
        self.assertEqual(self.page.figure_combo.itemData(0), 1)
        self.assertIn(
            "圖2（剖視圖）",
            self.page.figure_combo.itemData(0, Qt.ToolTipRole),
        )

        self.context.clear_document()
        self.assertNotIn("立體圖", self.page.figure_combo.itemText(0))
        self.assertNotIn("剖視圖", self.page.figure_meta.text())
        self.assertIn("drawing-page.png", self.page.figure_meta.text())

    def test_figure_labels_are_separate_from_image_at_common_resolutions(self):
        self.assertEqual(
            self.page.figure_labels.objectName(),
            "ComparisonFigureLabels",
        )
        for width, height in ((960, 640), (1366, 768), (1920, 1080)):
            self.page.resize(width, height)
            self.page.show()
            self.app.processEvents()

            self.assertEqual(
                (self.page.width(), self.page.height()),
                (width, height),
            )
            self.assertLessEqual(self.page.minimumSizeHint().width(), width)
            self.assertLessEqual(self.page.minimumSizeHint().height(), height)

            label_top_left = self.page.figure_labels.mapTo(
                self.page,
                self.page.figure_labels.rect().topLeft(),
            )
            label_bottom_right = self.page.figure_labels.mapTo(
                self.page,
                self.page.figure_labels.rect().bottomRight(),
            )
            scroll_top_left = self.page.figure_scroll_area.mapTo(
                self.page,
                self.page.figure_scroll_area.rect().topLeft(),
            )
            scroll_bottom_right = self.page.figure_scroll_area.mapTo(
                self.page,
                self.page.figure_scroll_area.rect().bottomRight(),
            )
            result_top_left = self.page.comparison_result.mapTo(
                self.page,
                self.page.comparison_result.rect().topLeft(),
            )

            for point in (
                label_top_left,
                label_bottom_right,
                scroll_top_left,
                scroll_bottom_right,
            ):
                self.assertGreaterEqual(point.x(), 0)
                self.assertGreaterEqual(point.y(), 0)
                self.assertLess(point.x(), width)
                self.assertLess(point.y(), height)
            self.assertLess(label_bottom_right.y(), scroll_top_left.y())
            self.assertLess(scroll_bottom_right.y(), result_top_left.y())

    def test_selecting_paragraph_jumps_to_its_first_referenced_figure(self):
        with TemporaryDirectory() as temporary_directory:
            paths = []
            for number in (1, 2, 3):
                path = Path(temporary_directory) / f"圖{number}.png"
                image = QImage(200, 120, QImage.Format_RGB32)
                image.fill(QColor("white"))
                self.assertTrue(image.save(str(path)))
                paths.append(path)

            document = build_document()
            second = document.paragraphs[4]
            second.text = "參閱圖2及圖3，該底座1支撐上蓋2。"
            second.normalized_text = second.text
            second.content_text = second.text
            self.context.publish_document(document)
            self.context.publish_ocr_results(
                [
                    {
                        "image_path": str(path),
                        "source_image_name": path.name,
                        "figure_numbers": [number],
                        "numbers": ["1", "2"],
                    }
                    for number, path in enumerate(paths, start=1)
                ]
            )

            self.page.paragraph_table.selectRow(1)

            self.assertEqual(self.page.paragraph_combo.currentIndex(), 1)
            self.assertEqual(self.page.figure_combo.currentIndex(), 1)
            self.assertIn("圖2", self.page.figure_meta.text())

    def test_additive_mid_paragraph_figure_stays_in_one_logical_row(self):
        document = build_document()
        first = document.paragraphs[3]
        first.text = (
            "參閱圖2及圖4，底座1已設置，例如為圖5，"
            "上蓋2及感測器A彼此連接。"
        )
        first.normalized_text = first.text
        first.content_text = first.text

        self.context.publish_document(document)
        self.context.publish_ocr_results(
            [
                {"figure_numbers": [2], "numbers": ["1", "A"]},
                {"figure_numbers": [4], "numbers": []},
                {"figure_numbers": [5], "numbers": ["2"]},
            ]
        )

        first_group = [
            comparison
            for comparison in self.page.comparisons
            if comparison.group_index == 1
        ]
        self.assertEqual(len(first_group), 1)
        self.assertEqual(first_group[0].referenced_figures, [2, 4, 5])
        self.assertEqual(first_group[0].labels_not_in_drawings, [])
        self.assertEqual(self.page.paragraph_table.rowCount(), 2)
        self.assertIn("圖2、4、5", self.page.paragraph_combo.itemText(0))

    def test_concluding_paragraph_and_later_rows_are_hidden(self):
        document = build_document()
        conclusion = document.paragraphs[4]
        conclusion.text = "參閱圖2，綜上所述，該底座1支撐上蓋2。"
        conclusion.normalized_text = conclusion.text
        conclusion.content_text = conclusion.text

        self.context.publish_document(document)
        self.context.publish_ocr_results(
            [
                {"figure_numbers": [1], "numbers": ["1", "2", "A"]},
                {"figure_numbers": [2], "numbers": []},
            ]
        )

        self.assertEqual(len(self.page.comparisons), 1)
        self.assertEqual(self.page.paragraph_table.rowCount(), 1)
        self.assertNotIn("圖2", self.page.paragraph_combo.itemText(0))

    def test_paragraph_labels_are_boxed_and_only_missing_drawing_labels_reported(self):
        with TemporaryDirectory() as temporary_directory:
            image_path = Path(temporary_directory) / "圖1.png"
            image = QImage(240, 160, QImage.Format_RGB32)
            image.fill(QColor("white"))
            self.assertTrue(image.save(str(image_path)))

            self.context.publish_document(build_document())
            self.context.publish_ocr_results(
                [
                    {
                        "image_path": str(image_path),
                        "source_image_name": image_path.name,
                        "figure_numbers": [1],
                        "numbers": ["1", "2", "9"],
                    }
                ]
            )

            html = self.page.paragraph_text.toHtml()
            self.assertIn("border", html)
            self.assertIn(
                "圖式缺失標號：A（感測器）",
                self.page.comparison_result.text(),
            )
            self.assertNotIn("圖式缺失標號：9", self.page.comparison_result.text())

    def test_only_named_symbol_list_components_are_compared(self):
        with TemporaryDirectory() as temporary_directory:
            image_path = Path(temporary_directory) / "figure_1.png"
            image = QImage(240, 160, QImage.Format_RGB32)
            image.fill(QColor("white"))
            self.assertTrue(image.save(str(image_path)))

            document = build_document()
            paragraph = document.paragraphs[3]
            paragraph.text = (
                "\u53c3\u95b1\u57161\uff0c\u672c\u6bb5\u5171\u67092\u500b\u65b9\u5411\uff0c"
                "\u53e6\u97005\u6b21\u64cd\u4f5c\uff0c\u8a72\u5e95\u5ea71\u3002"
            )
            paragraph.normalized_text = paragraph.text
            paragraph.content_text = paragraph.text
            self.context.publish_document(document)
            self.context.publish_ocr_results(
                [
                    {
                        "image_path": str(image_path),
                        "source_image_name": image_path.name,
                        "figure_numbers": [1],
                        "numbers": ["1", "2", "5"],
                    }
                ]
            )

            comparison = self.page.comparisons[0]
            self.assertEqual(comparison.paragraph_labels, ["1"])
            self.assertEqual(comparison.labels_not_in_drawings, [])
            self.assertNotIn("2", self.page.paragraph_labels.text())
            self.assertNotIn("5", self.page.paragraph_labels.text())

    def test_referenced_figures_can_be_edited_for_each_paragraph(self):
        with TemporaryDirectory() as temporary_directory:
            paths = []
            for number in (1, 2):
                path = Path(temporary_directory) / f"圖{number}.png"
                image = QImage(240, 160, QImage.Format_RGB32)
                image.fill(QColor("white"))
                self.assertTrue(image.save(str(path)))
                paths.append(path)

            self.context.publish_document(build_document())
            self.context.publish_ocr_results(
                [
                    {
                        "image_path": str(paths[0]),
                        "source_image_name": paths[0].name,
                        "figure_numbers": [1],
                        "numbers": ["1", "2", "A"],
                    },
                    {
                        "image_path": str(paths[1]),
                        "source_image_name": paths[1].name,
                        "figure_numbers": [2],
                        "numbers": ["9"],
                    },
                ]
            )

            for row in range(self.page.paragraph_table.rowCount()):
                self.assertFalse(
                    self.page.paragraph_table.item(row, 0).flags()
                    & Qt.ItemIsEditable
                )
                self.assertTrue(
                    self.page.paragraph_table.item(row, 1).flags()
                    & Qt.ItemIsEditable
                )
                self.assertFalse(
                    self.page.paragraph_table.item(row, 2).flags()
                    & Qt.ItemIsEditable
                )

            first_reference = self.page.paragraph_table.item(0, 1)
            second_reference = self.page.paragraph_table.item(1, 1)
            first_reference.setText("圖2")
            second_reference.setText("圖1、2")

            self.assertEqual(self.page.comparisons[0].referenced_figures, [2])
            self.assertEqual(self.page.comparisons[1].referenced_figures, [1, 2])
            self.assertEqual(first_reference.text(), "圖2")
            self.assertEqual(second_reference.text(), "圖1、2")
            self.assertEqual(
                self.page.comparisons[0].labels_not_in_drawings,
                ["1", "2", "A"],
            )
            self.assertEqual(self.page.paragraph_table.item(0, 2).text(), "1、2、A")
            self.assertIn("人工修改", first_reference.toolTip())
            self.assertIn("人工修改", second_reference.toolTip())
            self.assertIn("（人工）", self.page.paragraph_combo.itemText(0))

    def test_reference_edit_validation_clear_and_restore_automatic_value(self):
        self.context.publish_document(build_document())
        self.context.publish_ocr_results(
            [{"figure_numbers": [1], "numbers": ["1", "2", "A"]}]
        )
        reference_item = self.page.paragraph_table.item(0, 1)

        reference_item.setText("圖0")
        self.assertEqual(reference_item.text(), "圖1")
        self.assertEqual(self.page.comparisons[0].referenced_figures, [1])
        self.assertIn("參閱圖式格式錯誤", self.page.source_status.text())

        reference_item.setText("圖8a到圖8e")
        self.assertEqual(
            reference_item.text(),
            "圖8A、8B、8C、8D、8E",
        )
        self.assertEqual(
            self.page.comparisons[0].referenced_figures,
            ["8A", "8B", "8C", "8D", "8E"],
        )

        reference_item.setText("")
        self.assertEqual(reference_item.text(), "未指定")
        self.assertEqual(self.page.comparisons[0].referenced_figures, [])
        self.assertEqual(
            self.page.paragraph_table.item(0, 2).text(),
            "未指定圖式",
        )
        self.assertIn("參閱圖式：未指定", self.page.comparison_result.text())

        reference_item.setText("圖1")
        restored_item = self.page.paragraph_table.item(0, 1)
        self.assertEqual(self.page.comparisons[0].referenced_figures, [1])
        self.assertNotIn("人工修改", restored_item.toolTip())
        self.assertNotIn("（人工）", self.page.paragraph_combo.itemText(0))

    def test_manual_reference_survives_ocr_result_refresh(self):
        self.context.publish_document(build_document())
        self.context.publish_ocr_results(
            [
                {"figure_numbers": [1], "numbers": ["1", "2", "A"]},
                {"figure_numbers": [2], "numbers": ["9"]},
            ]
        )
        self.page.paragraph_table.item(0, 1).setText("圖2")

        self.context.publish_ocr_results(
            [
                {"figure_numbers": [1], "numbers": ["1", "2", "A"]},
                {"figure_numbers": [2], "numbers": ["1", "9"]},
            ]
        )

        self.assertEqual(self.page.comparisons[0].referenced_figures, [2])
        self.assertEqual(
            self.page.comparisons[0].labels_not_in_drawings,
            ["2", "A"],
        )
        self.assertEqual(self.page.paragraph_table.item(0, 1).text(), "圖2")
        self.assertIn("人工修改", self.page.paragraph_table.item(0, 1).toolTip())

    def test_manual_reference_stays_with_paragraph_when_ocr_adds_a_row(self):
        document = build_document()
        first = document.paragraphs[3]
        first.text = "參閱圖1，底座1與感測器A（見圖2）連接。"
        first.normalized_text = first.text
        first.content_text = first.text
        self.context.publish_document(document)
        self.context.publish_ocr_results(
            [
                {"figure_numbers": [1], "numbers": ["1"]},
                {"figure_numbers": [3], "numbers": ["1", "2", "A"]},
            ]
        )
        self.assertEqual(self.page.paragraph_table.rowCount(), 2)
        self.page.paragraph_table.item(1, 1).setText("圖3")

        self.context.publish_ocr_results(
            [
                {"figure_numbers": [1], "numbers": ["1"]},
                {"figure_numbers": [2], "numbers": ["A"]},
                {"figure_numbers": [3], "numbers": ["1", "2", "A"]},
            ]
        )

        self.assertEqual(self.page.paragraph_table.rowCount(), 3)
        second_paragraph_rows = [
            row
            for row, comparison in enumerate(self.page.comparisons)
            if comparison.group_index == 2
        ]
        self.assertEqual(second_paragraph_rows, [2])
        row = second_paragraph_rows[0]
        self.assertEqual(self.page.comparisons[row].referenced_figures, [3])
        self.assertEqual(self.page.paragraph_table.item(row, 1).text(), "圖3")
        self.assertIn("人工修改", self.page.paragraph_table.item(row, 1).toolTip())

    def test_navigation_buttons_return_to_source_features(self):
        buttons = self.page.findChildren(type(self.page.auto_match_button))
        next(button for button in buttons if button.text() == "前往文件偵錯").click()
        next(button for button in buttons if button.text() == "前往圖式標號").click()

        self.assertEqual(self.opened, ["patent_review", "patent_ocr"])

    def test_right_drawing_view_supports_zoom_reset_and_limits(self):
        with TemporaryDirectory() as temporary_directory:
            image_path = Path(temporary_directory) / "圖1.png"
            image = QImage(1200, 800, QImage.Format_RGB32)
            image.fill(QColor("white"))
            self.assertTrue(image.save(str(image_path)))
            self.context.publish_document(build_document())
            self.context.publish_ocr_results(
                [
                    {
                        "image_path": str(image_path),
                        "source_image_name": image_path.name,
                        "figure_numbers": [1],
                        "numbers": ["1", "2", "A"],
                    }
                ]
            )
            self.page.resize(1400, 900)
            self.page.show()
            self.app.processEvents()
            self.page.update_scaled_figure()
            fitted_width = self.page.image_preview.width()

            self.page.zoom_figure_at(
                2,
                self.page.figure_scroll_area.viewport().rect().center(),
            )

            self.assertGreater(self.page.figure_zoom_factor, 1.0)
            self.assertGreater(self.page.image_preview.width(), fitted_width)
            self.assertIn("%", self.page.figure_zoom_status.text())
            self.assertTrue(self.page.zoom_in_button.isEnabled())

            self.page.zoom_figure_at(
                100,
                self.page.figure_scroll_area.viewport().rect().center(),
            )
            self.assertEqual(
                self.page.figure_zoom_factor,
                MAX_IMAGE_PREVIEW_ZOOM,
            )
            self.page.zoom_figure_at(
                -100,
                self.page.figure_scroll_area.viewport().rect().center(),
            )
            self.assertEqual(self.page.figure_zoom_factor, 0.25)

            self.page.reset_figure_zoom()
            self.assertEqual(self.page.figure_zoom_factor, 1.0)
            self.assertEqual(
                self.page.figure_zoom_status.text(),
                "滾輪縮放：適合視窗",
            )

    def test_switching_drawing_resets_zoom_to_fit_window(self):
        with TemporaryDirectory() as temporary_directory:
            paths = []
            for number in (1, 2):
                path = Path(temporary_directory) / f"圖{number}.png"
                image = QImage(800, 500, QImage.Format_RGB32)
                image.fill(QColor("white"))
                self.assertTrue(image.save(str(path)))
                paths.append(path)
            self.context.publish_document(build_document())
            self.context.publish_ocr_results(
                [
                    {
                        "image_path": str(path),
                        "source_image_name": path.name,
                        "figure_numbers": [number],
                        "numbers": ["1"],
                    }
                    for number, path in enumerate(paths, start=1)
                ]
            )
            self.page.zoom_figure_at(
                3,
                self.page.figure_scroll_area.viewport().rect().center(),
            )
            self.assertGreater(self.page.figure_zoom_factor, 1.0)

            self.page.figure_combo.setCurrentIndex(1)

            self.assertEqual(self.page.figure_zoom_factor, 1.0)
            self.assertEqual(
                self.page.figure_zoom_status.text(),
                "滾輪縮放：適合視窗",
            )

    def test_arrow_buttons_move_to_previous_and_next_drawing(self):
        with TemporaryDirectory() as temporary_directory:
            paths = []
            for number in (1, 2, 3):
                path = Path(temporary_directory) / f"圖{number}.png"
                image = QImage(320, 200, QImage.Format_RGB32)
                image.fill(QColor("white"))
                self.assertTrue(image.save(str(path)))
                paths.append(path)
            self.context.publish_document(build_document())
            self.context.publish_ocr_results(
                [
                    {
                        "image_path": str(path),
                        "source_image_name": path.name,
                        "figure_numbers": [number],
                        "numbers": [str(number)],
                    }
                    for number, path in enumerate(paths, start=1)
                ]
            )

            self.assertTrue(self.page.previous_figure_button.isEnabled())
            self.assertTrue(self.page.next_figure_button.isEnabled())
            self.assertEqual(self.page.figure_combo.currentIndex(), 0)

            self.page.next_figure_button.click()
            self.assertEqual(self.page.figure_combo.currentIndex(), 1)
            self.page.previous_figure_button.click()
            self.assertEqual(self.page.figure_combo.currentIndex(), 0)
            self.page.previous_figure_button.click()
            self.assertEqual(self.page.figure_combo.currentIndex(), 2)
            self.page.next_figure_button.click()
            self.assertEqual(self.page.figure_combo.currentIndex(), 0)


if __name__ == "__main__":
    unittest.main()
