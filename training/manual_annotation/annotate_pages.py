"""Full-page GUI for adding missed 0-9/A-Z/prime patent-character boxes."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGraphicsItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

try:
    from .common import (
        Annotation,
        CLASS_NAMES,
        display_label,
        find_page_records,
        normalize_label,
        read_page_record,
        write_page_record,
    )
    from .import_sources import import_sources
except ImportError:
    from common import (
        Annotation,
        CLASS_NAMES,
        display_label,
        find_page_records,
        normalize_label,
        read_page_record,
        write_page_record,
    )
    from import_sources import import_sources


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_ANNOTATION_ROOT = SCRIPT_DIR / "annotation_set"


def parse_args():
    parser = argparse.ArgumentParser(description="Annotate real patent characters on pages.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_ANNOTATION_ROOT)
    return parser.parse_args()


class AnnotationRectItem(QGraphicsRectItem):
    def __init__(self, rect, annotation_index, label, source):
        super().__init__(rect)
        self.annotation_index = annotation_index
        self.label = label
        self.source = source
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setZValue(5)

        self.text_item = QGraphicsSimpleTextItem(display_label(label), self)
        self.text_item.setBrush(QColor(255, 255, 255))
        self.text_item.setPos(rect.left(), max(0.0, rect.top() - 18.0))
        self.text_item.setZValue(6)
        self.text_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.update_style()

    def update_style(self):
        if self.isSelected():
            color = QColor(255, 60, 60)
            width = 3
        elif self.source == "manual":
            color = QColor(255, 170, 0)
            width = 2
        else:
            color = QColor(0, 190, 120)
            width = 2
        self.setPen(QPen(color, width))


class AnnotationView(QGraphicsView):
    box_drawn = Signal(QRectF)

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setBackgroundBrush(QColor(55, 55, 55))
        self.draw_enabled = True
        self.start_point = None
        self.preview_item = None
        self.image_rect = QRectF()

    def set_draw_enabled(self, enabled):
        self.draw_enabled = bool(enabled)
        self.setDragMode(
            QGraphicsView.DragMode.NoDrag
            if self.draw_enabled
            else QGraphicsView.DragMode.ScrollHandDrag
        )

    def _annotation_item_at(self, position):
        item = self.itemAt(position)
        while item is not None:
            if isinstance(item, AnnotationRectItem):
                return item
            item = item.parentItem()
        return None

    def mousePressEvent(self, event):
        if (
            self.draw_enabled
            and event.button() == Qt.MouseButton.LeftButton
            and self._annotation_item_at(event.position().toPoint()) is None
        ):
            point = self.mapToScene(event.position().toPoint())
            if self.image_rect.contains(point):
                self.scene().clearSelection()
                self.start_point = point
                self.preview_item = self.scene().addRect(
                    QRectF(point, point),
                    QPen(QColor(255, 60, 60), 2, Qt.PenStyle.DashLine),
                )
                self.preview_item.setZValue(20)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.start_point is not None and self.preview_item is not None:
            current = self.mapToScene(event.position().toPoint())
            rect = QRectF(self.start_point, current).normalized().intersected(self.image_rect)
            self.preview_item.setRect(rect)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.start_point is not None
            and self.preview_item is not None
        ):
            rect = self.preview_item.rect().normalized().intersected(self.image_rect)
            self.scene().removeItem(self.preview_item)
            self.preview_item = None
            self.start_point = None
            if rect.width() >= 2 and rect.height() >= 2:
                self.box_drawn.emit(rect)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        factor = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
        self.scale(factor, factor)


class AnnotationWindow(QMainWindow):
    def __init__(self, annotation_root):
        super().__init__()
        self.annotation_root = Path(annotation_root).resolve()
        self.record_paths = []
        self.current_index = 0
        self.current_record = None
        self.annotations = []
        self.box_items = []
        self._syncing_selection = False

        self.setWindowTitle("專利字元整頁人工標註（0-9 / A-Z / prime）")
        self.resize(1500, 900)
        self._build_ui()
        self._install_shortcuts()
        self.reload_records()

    def _build_ui(self):
        self.view = AnnotationView()
        self.view.box_drawn.connect(self.add_box)
        self.view.scene().selectionChanged.connect(self.on_scene_selection_changed)

        self.progress_label = QLabel()
        self.page_label = QLabel()
        self.summary_label = QLabel()

        previous_button = QPushButton("上一頁 (PgUp)")
        previous_button.clicked.connect(lambda: self.navigate(-1))
        next_button = QPushButton("下一頁 (PgDn)")
        next_button.clicked.connect(lambda: self.navigate(1))
        import_button = QPushButton("匯入測試圖片 / PDF")
        import_button.clicked.connect(self.import_files)

        navigation = QHBoxLayout()
        navigation.addWidget(previous_button)
        navigation.addWidget(next_button)
        navigation.addWidget(import_button)

        self.label_combo = QComboBox()
        for class_name in CLASS_NAMES:
            self.label_combo.addItem(display_label(class_name), class_name)
        self.label_combo.setCurrentIndex(self.label_combo.findData("I"))

        self.annotation_list = QListWidget()
        self.annotation_list.currentRowChanged.connect(self.on_list_selection_changed)

        apply_label_button = QPushButton("套用標籤到選取框")
        apply_label_button.clicked.connect(self.apply_label)
        delete_button = QPushButton("刪除選取框 (Delete)")
        delete_button.clicked.connect(self.delete_selected)

        self.draw_mode_button = QPushButton("繪框模式（取消後可拖曳平移）")
        self.draw_mode_button.setCheckable(True)
        self.draw_mode_button.setChecked(True)
        self.draw_mode_button.toggled.connect(self.view.set_draw_enabled)

        zoom_in_button = QPushButton("放大 +")
        zoom_in_button.clicked.connect(lambda: self.view.scale(1.25, 1.25))
        zoom_out_button = QPushButton("縮小 -")
        zoom_out_button.clicked.connect(lambda: self.view.scale(0.8, 0.8))
        fit_button = QPushButton("符合視窗")
        fit_button.clicked.connect(self.fit_page)

        zoom_row = QHBoxLayout()
        zoom_row.addWidget(zoom_in_button)
        zoom_row.addWidget(zoom_out_button)
        zoom_row.addWidget(fit_button)

        self.reviewed_checkbox = QCheckBox("本頁已逐一檢查完成")
        self.skip_reviewed_checkbox = QCheckBox("翻頁時略過已完成頁")
        self.skip_reviewed_checkbox.setChecked(True)

        save_button = QPushButton("儲存 (Ctrl+S)")
        save_button.clicked.connect(self.save_current)
        finish_button = QPushButton("完成本頁並到下一頁 (Ctrl+Enter)")
        finish_button.clicked.connect(self.finish_page)

        instructions = QLabel(
            "操作方式：\n"
            "1. 先選標籤，再在字元外框拖曳。\n"
            "2. VIII 要分成 V、I、I、I 四個框。\n"
            "3. 7' 要分成 7 與 prime 兩個框。\n"
            "4. 綠框是既有建議、橘框是手動畫框；錯框請刪除。\n"
            "5. 字母/數字鍵可快速切換目前標籤；滑鼠滾輪縮放。"
        )
        instructions.setWordWrap(True)

        sidebar = QVBoxLayout()
        sidebar.addWidget(self.progress_label)
        sidebar.addWidget(self.page_label)
        sidebar.addWidget(self.summary_label)
        sidebar.addLayout(navigation)
        sidebar.addWidget(QLabel("目前要畫的 class："))
        sidebar.addWidget(self.label_combo)
        sidebar.addWidget(self.draw_mode_button)
        sidebar.addLayout(zoom_row)
        sidebar.addWidget(QLabel("本頁標註："))
        sidebar.addWidget(self.annotation_list, 1)
        sidebar.addWidget(apply_label_button)
        sidebar.addWidget(delete_button)
        sidebar.addWidget(self.reviewed_checkbox)
        sidebar.addWidget(self.skip_reviewed_checkbox)
        sidebar.addWidget(save_button)
        sidebar.addWidget(finish_button)
        sidebar.addWidget(instructions)

        sidebar_widget = QWidget()
        sidebar_widget.setLayout(sidebar)
        sidebar_widget.setMinimumWidth(340)
        sidebar_widget.setMaximumWidth(430)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.view)
        splitter.addWidget(sidebar_widget)
        splitter.setStretchFactor(0, 1)
        self.setCentralWidget(splitter)

    def _install_shortcuts(self):
        self.shortcuts = []

        def add_shortcut(sequence, callback):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(callback)
            self.shortcuts.append(shortcut)

        add_shortcut(QKeySequence.StandardKey.Save, self.save_current)
        add_shortcut(Qt.Key.Key_Delete, self.delete_selected)
        add_shortcut(Qt.Key.Key_PageUp, lambda: self.navigate(-1))
        add_shortcut(Qt.Key.Key_PageDown, lambda: self.navigate(1))
        add_shortcut("Ctrl+Return", self.finish_page)

        for class_name in tuple("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
            add_shortcut(
                class_name,
                lambda value=class_name: self.select_current_label(value),
            )
        add_shortcut("'", lambda: self.select_current_label("prime"))

    def select_current_label(self, label):
        index = self.label_combo.findData(label)
        if index >= 0:
            self.label_combo.setCurrentIndex(index)

    def reload_records(self, preferred_path=None):
        self.record_paths = find_page_records(self.annotation_root)
        if not self.record_paths:
            self.current_record = None
            self.progress_label.setText(
                "尚無頁面。請先執行 prepare_annotation_set.py 或匯入圖片/PDF。"
            )
            return

        if preferred_path is not None:
            preferred_path = Path(preferred_path).resolve()
            for index, path in enumerate(self.record_paths):
                if path.resolve() == preferred_path:
                    self.current_index = index
                    break
        else:
            self.current_index = 0
            for index, path in enumerate(self.record_paths):
                if not read_page_record(path).get("reviewed", False):
                    self.current_index = index
                    break
        self.load_current(fit=True)

    def load_current(self, fit=False):
        if not self.record_paths:
            return

        path = self.record_paths[self.current_index]
        record = read_page_record(path)
        image_path = self.annotation_root / record["image_path"]
        pixmap = QPixmap(str(image_path))
        if pixmap.isNull():
            raise ValueError(f"Cannot load image: {image_path}")

        self.current_record = record
        self.annotations = []
        for raw in record.get("annotations", []):
            annotation = Annotation(**raw).normalized(record["width"], record["height"])
            if annotation.is_valid(record["width"], record["height"]):
                self.annotations.append(annotation)
        self.sort_annotations()
        self.reviewed_checkbox.setChecked(bool(record.get("reviewed")))
        self.rebuild_scene(pixmap=pixmap, fit=fit)
        self.update_page_text()

    def sort_annotations(self):
        self.annotations.sort(key=lambda item: (item.y1, item.x1))

    def rebuild_scene(self, pixmap=None, fit=False, selected_index=None):
        if self.current_record is None:
            return
        scene = self.view.scene()
        self.box_items = []
        scene.clear()

        if pixmap is None:
            pixmap = QPixmap(str(self.annotation_root / self.current_record["image_path"]))
        scene.addPixmap(pixmap).setZValue(-10)
        self.view.image_rect = QRectF(0, 0, pixmap.width(), pixmap.height())
        scene.setSceneRect(self.view.image_rect)

        self.annotation_list.blockSignals(True)
        self.annotation_list.clear()
        for index, annotation in enumerate(self.annotations):
            rect = QRectF(
                annotation.x1,
                annotation.y1,
                annotation.x2 - annotation.x1,
                annotation.y2 - annotation.y1,
            )
            item = AnnotationRectItem(
                rect,
                annotation_index=index,
                label=annotation.label,
                source=annotation.source,
            )
            scene.addItem(item)
            self.box_items.append(item)
            self.annotation_list.addItem(
                f"{index + 1:03d}  {display_label(annotation.label):>5}  "
                f"({annotation.x1:.0f},{annotation.y1:.0f})"
            )
        self.annotation_list.blockSignals(False)

        if selected_index is not None and 0 <= selected_index < len(self.box_items):
            self.box_items[selected_index].setSelected(True)
            self.annotation_list.setCurrentRow(selected_index)
        if fit:
            self.fit_page()
        self.update_summary()

    def fit_page(self):
        if not self.view.image_rect.isEmpty():
            self.view.fitInView(self.view.image_rect, Qt.AspectRatioMode.KeepAspectRatio)

    def current_label(self):
        return normalize_label(self.label_combo.currentData())

    def add_box(self, rect):
        label = self.current_label()
        if not label or self.current_record is None:
            return
        self.annotations.append(
            Annotation(
                label=label,
                x1=rect.left(),
                y1=rect.top(),
                x2=rect.right(),
                y2=rect.bottom(),
                source="manual",
            )
        )
        self.sort_annotations()
        selected = self.annotations.index(
            min(
                self.annotations,
                key=lambda item: abs(item.x1 - rect.left()) + abs(item.y1 - rect.top()),
            )
        )
        self.reviewed_checkbox.setChecked(False)
        self.rebuild_scene(fit=False, selected_index=selected)

    def selected_index(self):
        selected = [item for item in self.box_items if item.isSelected()]
        return selected[0].annotation_index if selected else -1

    def on_scene_selection_changed(self):
        if self._syncing_selection:
            return
        for item in self.box_items:
            item.update_style()
        index = self.selected_index()
        if index >= 0:
            self._syncing_selection = True
            self.annotation_list.setCurrentRow(index)
            self.select_current_label(self.annotations[index].label)
            self._syncing_selection = False

    def on_list_selection_changed(self, index):
        if self._syncing_selection or index < 0 or index >= len(self.box_items):
            return
        self._syncing_selection = True
        self.view.scene().clearSelection()
        item = self.box_items[index]
        item.setSelected(True)
        item.update_style()
        self.view.centerOn(item)
        self.select_current_label(self.annotations[index].label)
        self._syncing_selection = False

    def apply_label(self):
        index = self.selected_index()
        label = self.current_label()
        if index < 0 or not label:
            return
        self.annotations[index].label = label
        self.annotations[index].source = "manual"
        self.reviewed_checkbox.setChecked(False)
        self.rebuild_scene(fit=False, selected_index=index)

    def delete_selected(self):
        index = self.selected_index()
        if index < 0:
            return
        del self.annotations[index]
        self.reviewed_checkbox.setChecked(False)
        self.rebuild_scene(fit=False)

    def save_current(self):
        if self.current_record is None:
            return
        self.current_record["reviewed"] = self.reviewed_checkbox.isChecked()
        self.current_record["annotations"] = [asdict(item) for item in self.annotations]
        write_page_record(self.record_paths[self.current_index], self.current_record)
        self.update_page_text()

    def finish_page(self):
        if self.current_record is None:
            return
        self.reviewed_checkbox.setChecked(True)
        self.save_current()
        self.navigate(1)

    def navigate(self, direction):
        if not self.record_paths:
            return
        self.save_current()
        count = len(self.record_paths)
        candidate = self.current_index
        for _ in range(count):
            candidate = (candidate + direction) % count
            if not self.skip_reviewed_checkbox.isChecked():
                break
            if not read_page_record(self.record_paths[candidate]).get("reviewed", False):
                break
        self.current_index = candidate
        self.load_current(fit=True)

    def update_summary(self):
        counts = Counter(item.label for item in self.annotations)
        roman = counts["I"] + counts["V"] + counts["X"]
        self.summary_label.setText(
            f"本頁框數：{len(self.annotations)}｜I/V/X：{roman}｜prime：{counts['prime']}"
        )

    def update_page_text(self):
        if self.current_record is None:
            return
        completed = sum(
            bool(read_page_record(path).get("reviewed", False))
            for path in self.record_paths
        )
        self.progress_label.setText(
            f"頁面 {self.current_index + 1}/{len(self.record_paths)}｜已完成 {completed}"
        )
        self.page_label.setText(
            f"{self.current_record['page_id']}\n來源：{self.current_record['source_path']}"
        )
        self.page_label.setWordWrap(True)
        self.update_summary()

    def import_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "匯入實際測試圖片或 PDF",
            "",
            "Patent sources (*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.pdf)",
        )
        if not paths:
            return
        try:
            imported = import_sources(paths, self.annotation_root, dpi=300)
        except Exception as exc:
            QMessageBox.critical(self, "匯入失敗", str(exc))
            return
        if imported:
            self.reload_records(preferred_path=imported[0])
        else:
            QMessageBox.information(self, "沒有新增", "這些來源已經匯入過。")

    def closeEvent(self, event):
        self.save_current()
        super().closeEvent(event)


def main():
    args = parse_args()
    app = QApplication(sys.argv)
    window = AnnotationWindow(args.dataset)
    window.show()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
