import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication, QMessageBox

from features.patent_ocr.figure_heading_classes import (
    FIGURE_PREFIX_ROTATE_RIGHT_CLASS,
)
from training.manual_annotation.annotate_pages import AnnotationWindow
from training.manual_annotation.common import (
    Annotation,
    make_page_record,
    read_page_record,
    write_page_record,
)


class AnnotationWindowNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dataset = Path(self.temp_dir.name)
        image_dir = self.dataset / "images" / "val"
        label_dir = self.dataset / "labels" / "val"
        image_dir.mkdir(parents=True)
        label_dir.mkdir(parents=True)

        for page_number, reviewed in ((1, True), (2, False), (3, False)):
            page_id = f"page_{page_number:03d}"
            image_path = image_dir / f"{page_id}.png"
            pixmap = QPixmap(100, 100)
            pixmap.fill(QColor("white"))
            self.assertTrue(pixmap.save(str(image_path)))
            record = make_page_record(
                page_id=page_id,
                split="val",
                image_path=image_path.relative_to(self.dataset),
                source_path=image_path,
                image_width=100,
                image_height=100,
                annotations=[Annotation("1", 10, 10, 20, 30)],
                reviewed=reviewed,
            )
            write_page_record(label_dir / f"{page_id}.json", record)

        self.window = AnnotationWindow(self.dataset)

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.temp_dir.cleanup()

    def test_reopens_at_first_unfinished_page(self):
        self.assertEqual(self.window.current_index, 1)
        self.assertEqual(self.window.current_record["page_id"], "page_002")
        self.assertFalse(self.window.reviewed_checkbox.isChecked())

    def test_previous_and_next_visit_adjacent_pages_including_completed(self):
        self.window.navigate(-1)
        self.assertEqual(self.window.current_record["page_id"], "page_001")
        self.window.navigate(1)
        self.assertEqual(self.window.current_record["page_id"], "page_002")

    def test_navigation_saves_draft_without_marking_it_complete(self):
        self.window.annotations.append(Annotation("A", 30, 30, 45, 55))
        self.window.reviewed_checkbox.setChecked(False)
        current_path = self.window.record_paths[self.window.current_index]

        self.window.navigate(1)

        saved = read_page_record(current_path)
        self.assertFalse(saved["reviewed"])
        self.assertEqual(len(saved["annotations"]), 2)

    def test_can_jump_to_any_page_and_boundaries_do_not_wrap(self):
        self.window.page_jump_spin.setValue(3)
        self.window.go_to_selected_page()
        self.assertEqual(self.window.current_record["page_id"], "page_003")
        self.window.navigate(1)
        self.assertEqual(self.window.current_record["page_id"], "page_003")

        self.window.page_jump_spin.setValue(1)
        self.window.go_to_selected_page()
        self.assertEqual(self.window.current_record["page_id"], "page_001")
        self.window.navigate(-1)
        self.assertEqual(self.window.current_record["page_id"], "page_001")


class FigureHeadingAnnotationWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dataset = Path(self.temp_dir.name)
        image_path = self.dataset / "images" / "train" / "page.png"
        record_path = self.dataset / "labels" / "train" / "page.json"
        image_path.parent.mkdir(parents=True)
        record_path.parent.mkdir(parents=True)
        pixmap = QPixmap(160, 100)
        pixmap.fill(QColor("white"))
        self.assertTrue(pixmap.save(str(image_path)))
        record = make_page_record(
            page_id="page",
            split="train",
            image_path=image_path.relative_to(self.dataset),
            source_path="sample.pdf#page=1",
            image_width=160,
            image_height=100,
            annotations=[
                Annotation(
                    "figure_prefix",
                    20,
                    40,
                    45,
                    70,
                    source="pdf_text_seed",
                ),
                Annotation(
                    "figure_identifier",
                    50,
                    43,
                    75,
                    68,
                    source="pdf_text_seed",
                    text="03a",
                    seed_text="03a",
                ),
            ],
            reviewed=False,
        )
        record["annotation_mode"] = "figure-heading"
        write_page_record(record_path, record)
        self.window = AnnotationWindow(self.dataset, mode="figure-heading")

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.temp_dir.cleanup()

    def test_completion_normalizes_text_and_assigns_shared_pair_id(self):
        self.assertEqual(self.window._prepare_heading_completion(), "")
        prefix, identifier = self.window.annotations
        self.assertEqual(identifier.text, "3A")
        self.assertTrue(prefix.pair_id)
        self.assertEqual(prefix.pair_id, identifier.pair_id)

    def test_completion_rejects_identifier_without_text(self):
        self.window.annotations[1].text = ""
        self.assertIn("必須輸入", self.window._prepare_heading_completion())

    def test_instructions_distinguish_prefix_and_identifier_seed_colors(self):
        instructions = self.window.instructions_label.text()
        self.assertIn("綠框是文字層的「圖」種子", instructions)
        self.assertIn("藍框是文字層的「圖號」種子", instructions)

    def test_sideways_prefix_class_is_available_and_can_complete_a_pair(self):
        self.assertGreaterEqual(
            self.window.label_combo.findData(FIGURE_PREFIX_ROTATE_RIGHT_CLASS),
            0,
        )
        self.assertIn("Alt+3", self.window.instructions_label.text())
        self.window.annotations[0].label = FIGURE_PREFIX_ROTATE_RIGHT_CLASS
        self.assertEqual(self.window._prepare_heading_completion(), "")
        self.assertEqual(
            self.window.annotations[0].pair_id,
            self.window.annotations[1].pair_id,
        )

    def test_manual_boxes_do_not_reorder_the_original_heading_queue(self):
        label_dir = self.dataset / "labels" / "train"
        shared_image = self.dataset / "images" / "train" / "page.png"
        manual = make_page_record(
            page_id="manual",
            split="train",
            image_path=shared_image.relative_to(self.dataset),
            source_path="manual.pdf#page=1",
            image_width=160,
            image_height=100,
            annotations=[Annotation("figure_prefix", 5, 5, 20, 20)],
            reviewed=False,
        )
        blank = make_page_record(
            page_id="blank",
            split="train",
            image_path=shared_image.relative_to(self.dataset),
            source_path="blank.pdf#page=1",
            image_width=160,
            image_height=100,
            annotations=[],
            reviewed=False,
        )
        write_page_record(label_dir / "aaa_manual.json", manual)
        write_page_record(label_dir / "bbb_blank.json", blank)

        self.window.reload_records()

        page_ids = [
            read_page_record(path)["page_id"] for path in self.window.record_paths
        ]
        self.assertEqual(page_ids, ["page", "manual", "blank"])

    def test_new_identifier_resets_to_prefix_and_clears_editor(self):
        self.window.select_current_label("figure_identifier")
        self.window.identifier_text_line.setText("04b")

        self.window.add_box(QRectF(90, 40, 25, 25))

        added = self.window.annotations[-1]
        self.assertEqual(added.label, "figure_identifier")
        self.assertEqual(added.text, "4B")
        self.assertEqual(self.window.current_label(), "figure_prefix")
        self.assertEqual(self.window.identifier_text_line.text(), "")

    def test_cancelling_empty_seed_warning_keeps_page_as_draft(self):
        self.window.annotations.clear()
        self.window.reviewed_checkbox.setChecked(True)

        with patch.object(
            QMessageBox,
            "warning",
            return_value=QMessageBox.StandardButton.Cancel,
        ) as warning:
            self.assertFalse(self.window.save_current())

        self.assertEqual(warning.call_args.args[1], "確認清空文字層種子")
        self.assertIn("完成為負樣本", warning.call_args.args[2])
        self.assertFalse(self.window.reviewed_checkbox.isChecked())
        saved = read_page_record(self.window.record_paths[0])
        self.assertFalse(saved["reviewed"])
        self.assertTrue(saved["had_pdf_text_seed"])
        self.assertFalse(saved["empty_seed_page_confirmed"])

    def test_confirming_empty_seed_warning_records_explicit_confirmation(self):
        self.window.annotations.clear()
        self.window.reviewed_checkbox.setChecked(True)

        with patch.object(
            QMessageBox,
            "warning",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            self.assertTrue(self.window.save_current())

        saved = read_page_record(self.window.record_paths[0])
        self.assertTrue(saved["reviewed"])
        self.assertTrue(saved["had_pdf_text_seed"])
        self.assertTrue(saved["empty_seed_page_confirmed"])

    def test_annotation_list_displays_pair_id(self):
        self.assertEqual(self.window._prepare_heading_completion(), "")
        self.window.rebuild_scene(fit=False)

        entries = [
            self.window.annotation_list.item(index).text()
            for index in range(self.window.annotation_list.count())
        ]
        self.assertEqual(len(entries), 2)
        self.assertTrue(all("配對 caption-001" in entry for entry in entries))


if __name__ == "__main__":
    unittest.main()
