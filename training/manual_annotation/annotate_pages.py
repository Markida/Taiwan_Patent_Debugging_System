"""GUI for adding missed 0-9/A-Z/a-z/prime patent-character boxes."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (
    QBrush,
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
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from features.patent_ocr.figure_heading import pair_figure_heading_boxes
from features.patent_ocr.figure_heading_classes import (
    FIGURE_PREFIX_CLASS_NAMES,
    FIGURE_PREFIX_ROTATE_RIGHT_CLASS,
)
from features.patent_ocr.figure_identifiers import normalize_figure_identifier

try:
    from .common import (
        Annotation,
        EXTENDED_CLASS_NAMES,
        FIGURE_HEADING_CLASS_NAMES,
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
        EXTENDED_CLASS_NAMES,
        FIGURE_HEADING_CLASS_NAMES,
        display_label,
        find_page_records,
        normalize_label,
        read_page_record,
        write_page_record,
    )
    from import_sources import import_sources


DEFAULT_ANNOTATION_ROOT = SCRIPT_DIR / "annotation_set"


def parse_args():
    parser = argparse.ArgumentParser(description="Annotate real patent characters on pages.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_ANNOTATION_ROOT)
    parser.add_argument(
        "--mode",
        choices=("characters", "figure-heading"),
        default="characters",
    )
    return parser.parse_args()


class AnnotationRectItem(QGraphicsRectItem):
    def __init__(self, rect, annotation_index, label, source, text=""):
        super().__init__(rect)
        self.annotation_index = annotation_index
        self.label = label
        self.source = source
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setZValue(5)
        # Keep the glyph permanently visible.  Relying on Qt's platform-
        # dependent default brush can produce an opaque rectangle on some
        # Windows graphics drivers/remote desktop sessions.
        self.setBrush(QBrush(Qt.BrushStyle.NoBrush))

        overlay_text = display_label(label)
        if label == "figure_identifier" and str(text or "").strip():
            overlay_text = f"{overlay_text} {str(text).strip()}"
        self.text_item = QGraphicsSimpleTextItem(overlay_text, self)
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
        elif self.label == "figure_identifier":
            color = QColor(40, 130, 255)
            width = 2
        else:
            color = QColor(0, 190, 120)
            width = 2
        self.setPen(QPen(color, width))


class AnnotationView(QGraphicsView):
    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setBackgroundBrush(QColor(55, 55, 55))
        # Transparent overlays and a persistent pixmap allow Qt to update only
        # the damaged region. FullViewportUpdate can saturate the Windows event
        # loop while the mouse is moving.
        self.setViewportUpdateMode(
            QGraphicsView.ViewportUpdateMode.SmartViewportUpdate
        )
        self.draw_enabled = True
        self._rubber_from = None
        self._rubber_to = None
        self._rubber_rect = None
        self._rubber_active = False
        self.box_drawn_callback = None
        self.image_rect = QRectF()
        # Let Qt's native code own the complete press/move/release sequence.
        # Python only receives the resulting rectangle through this signal.
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.rubberBandChanged.connect(self._on_rubber_band_changed)

    def set_draw_enabled(self, enabled):
        self.draw_enabled = bool(enabled)
        self.setDragMode(
            QGraphicsView.DragMode.RubberBandDrag
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

    def _on_rubber_band_changed(self, viewport_rect, from_scene, to_scene):
        if not self.draw_enabled:
            return
        if not viewport_rect.isNull():
            self._rubber_from = from_scene
            self._rubber_to = to_scene
            self._rubber_rect = QRectF(
                self.mapToScene(viewport_rect.topLeft()),
                self.mapToScene(viewport_rect.bottomRight()),
            ).normalized().intersected(self.image_rect)
            self._rubber_active = True
            return
        if not self._rubber_active:
            return
        rect = self._rubber_rect
        self._rubber_from = None
        self._rubber_to = None
        self._rubber_rect = None
        self._rubber_active = False
        if rect is None:
            return
        callback = self.box_drawn_callback
        if callback is not None and rect.width() >= 2 and rect.height() >= 2:
            callback(rect)

    def wheelEvent(self, event):
        factor = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
        self.scale(factor, factor)


class AnnotationWindow(QMainWindow):
    def __init__(self, annotation_root, mode="characters"):
        super().__init__()
        self.annotation_root = Path(annotation_root).resolve()
        self.mode = str(mode)
        self.record_paths = []
        self.current_index = 0
        self.current_record = None
        self.annotations = []
        self.box_items = []
        self.current_pixmap = None
        self.background_item = None
        self._syncing_selection = False
        self._page_had_pdf_text_seed = False
        self._empty_seed_page_confirmed = False

        self.setWindowTitle(
            "專利圖題人工標註（圖 / 圖號）"
            if self.mode == "figure-heading"
            else "專利字元人工標註（0-9 / A-Z / a-z / prime）"
        )
        self.resize(1500, 900)
        self._build_ui()
        self._install_shortcuts()
        self.reload_records()

    def _build_ui(self):
        self.view = AnnotationView()
        # A direct Python callback avoids transferring QRectF ownership through
        # a Python-defined Qt signal during a native mouse event. That signal
        # path is unstable in the bundled Windows PySide6 runtime.
        self.view.box_drawn_callback = self.add_box
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

        self.page_jump_spin = QSpinBox()
        self.page_jump_spin.setMinimum(1)
        self.page_jump_spin.setMaximum(1)
        self.page_jump_spin.setKeyboardTracking(False)
        self.page_jump_spin.setToolTip("輸入任意頁碼，可返回修改先前頁面。")
        jump_button = QPushButton("跳至頁面")
        jump_button.clicked.connect(self.go_to_selected_page)
        self.page_jump_spin.lineEdit().returnPressed.connect(
            self.go_to_selected_page
        )

        page_jump_row = QHBoxLayout()
        page_jump_row.addWidget(QLabel("指定頁碼："))
        page_jump_row.addWidget(self.page_jump_spin)
        page_jump_row.addWidget(jump_button)

        self.label_combo = QComboBox()
        class_names = (
            FIGURE_HEADING_CLASS_NAMES
            if self.mode == "figure-heading"
            else EXTENDED_CLASS_NAMES
        )
        for class_name in class_names:
            self.label_combo.addItem(display_label(class_name), class_name)
        default_label = "figure_prefix" if self.mode == "figure-heading" else "I"
        self.label_combo.setCurrentIndex(self.label_combo.findData(default_label))

        self.identifier_text_label = QLabel("選取框的完整圖號文字：")
        self.identifier_text_line = QLineEdit()
        self.identifier_text_line.setPlaceholderText("例如 1、3A、1' 或 B")
        self.identifier_text_line.setToolTip(
            "圖號框必須保存實際文字，才能驗證後續 OCR 是否讀對。"
        )
        self.identifier_text_line.returnPressed.connect(self.apply_identifier_text)
        self.identifier_text_apply_button = QPushButton("套用圖號文字到選取框")
        self.identifier_text_apply_button.clicked.connect(
            self.apply_identifier_text
        )
        heading_mode = self.mode == "figure-heading"
        self.identifier_text_label.setVisible(heading_mode)
        self.identifier_text_line.setVisible(heading_mode)
        self.identifier_text_apply_button.setVisible(heading_mode)

        self.lowercase_checkbox = QCheckBox(
            "小寫字母模式（a-z，Ctrl+L 切換）"
        )
        self.lowercase_checkbox.toggled.connect(self.on_case_mode_toggled)
        self.lowercase_checkbox.setVisible(self.mode != "figure-heading")

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

        save_button = QPushButton("儲存 (Ctrl+S)")
        save_button.clicked.connect(self.save_current)
        finish_button = QPushButton("確認完成本頁並到下一頁 (Ctrl+Enter)")
        finish_button.clicked.connect(self.finish_page)

        if self.mode == "figure-heading":
            instruction_text = (
                "操作方式：\n"
                "1. 「圖」只框正向圖題開頭的單一圖字；若整頁需向右旋轉 90°，"
                "改選「橫著的圖（右旋90°）」。\n"
                "2. 「圖號」要把其後完整的 1、3A 或 1' 框在同一框，"
                "並輸入相同文字。\n"
                "3. 不要框立體圖、視圖、圖式、圖例、附圖等一般文字。\n"
                "4. 綠框是文字層的「圖」種子、藍框是文字層的「圖號」種子；"
                "橘框是手動畫框，錯框請刪除。\n"
                "5. Alt+1／Alt+2／Alt+3 可快速切換「圖」、「圖號」與"
                "「橫著的圖」。\n"
                "6. 每一個圖題都必須恰好有一組相鄰的兩個框。\n"
                "7. 上一頁、下一頁與跳頁只保存草稿，不會標成完成。\n"
                "8. 只有按「完成本頁」或自行勾選完成，才算逐一檢查。"
            )
        else:
            instruction_text = (
                "操作方式：\n"
                "1. 先選標籤，再在字元外框拖曳。\n"
                "2. VIII 要分成 V、I、I、I 四個框。\n"
                "3. 7' 要分成 7 與 prime 兩個框。\n"
                "4. 綠框是既有建議、橘框是手動畫框；錯框請刪除。\n"
                "5. 字母/數字鍵可快速切換目前標籤；滑鼠滾輪縮放。\n"
                "6. 小寫字母請先勾選小寫模式；A 與 a 是不同 class。\n"
                "7. 上一頁、下一頁與跳頁只會保存草稿，不會自動標成完成。\n"
                "8. 只有按「完成本頁」或自行勾選完成，才算已逐一檢查。"
            )
        self.instructions_label = QLabel(instruction_text)
        self.instructions_label.setWordWrap(True)

        sidebar = QVBoxLayout()
        sidebar.addWidget(self.progress_label)
        sidebar.addWidget(self.page_label)
        sidebar.addWidget(self.summary_label)
        sidebar.addLayout(navigation)
        sidebar.addLayout(page_jump_row)
        sidebar.addWidget(QLabel("目前要畫的 class："))
        sidebar.addWidget(self.label_combo)
        sidebar.addWidget(self.identifier_text_label)
        sidebar.addWidget(self.identifier_text_line)
        sidebar.addWidget(self.identifier_text_apply_button)
        sidebar.addWidget(self.lowercase_checkbox)
        sidebar.addWidget(self.draw_mode_button)
        sidebar.addLayout(zoom_row)
        sidebar.addWidget(QLabel("本頁標註："))
        sidebar.addWidget(self.annotation_list, 1)
        sidebar.addWidget(apply_label_button)
        sidebar.addWidget(delete_button)
        sidebar.addWidget(self.reviewed_checkbox)
        sidebar.addWidget(save_button)
        sidebar.addWidget(finish_button)
        sidebar.addWidget(self.instructions_label)

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

        if self.mode == "figure-heading":
            add_shortcut("Alt+1", lambda: self.select_current_label("figure_prefix"))
            add_shortcut(
                "Alt+2",
                lambda: self.select_current_label("figure_identifier"),
            )
            add_shortcut(
                "Alt+3",
                lambda: self.select_current_label(
                    FIGURE_PREFIX_ROTATE_RIGHT_CLASS
                ),
            )
        else:
            for class_name in tuple("0123456789"):
                add_shortcut(
                    class_name,
                    lambda value=class_name: self.select_current_label(value),
                )
            for class_name in tuple("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
                add_shortcut(
                    class_name,
                    lambda value=class_name: self.select_keyboard_letter(value),
                )
            add_shortcut("'", lambda: self.select_current_label("prime"))
            add_shortcut("Ctrl+L", self.toggle_lowercase_mode)

    def select_keyboard_letter(self, uppercase_label):
        label = (
            uppercase_label.lower()
            if self.lowercase_checkbox.isChecked()
            else uppercase_label
        )
        self.select_current_label(label)

    def toggle_lowercase_mode(self):
        self.lowercase_checkbox.setChecked(
            not self.lowercase_checkbox.isChecked()
        )

    def on_case_mode_toggled(self, lowercase_enabled):
        current = str(self.label_combo.currentData() or "")
        if len(current) == 1 and current.isalpha():
            self.select_current_label(
                current.lower() if lowercase_enabled else current.upper()
            )

    def select_current_label(self, label):
        index = self.label_combo.findData(label)
        if index >= 0:
            self.label_combo.setCurrentIndex(index)
        if (
            hasattr(self, "lowercase_checkbox")
            and len(str(label)) == 1
            and str(label).isalpha()
        ):
            self.lowercase_checkbox.blockSignals(True)
            self.lowercase_checkbox.setChecked(str(label).islower())
            self.lowercase_checkbox.blockSignals(False)

    def reload_records(self, preferred_path=None):
        self.record_paths = find_page_records(self.annotation_root)
        if self.mode == "figure-heading":
            def stable_heading_order(path):
                record = read_page_record(path)
                originally_seeded = bool(
                    record.get("had_pdf_text_seed", False)
                    or record.get("seed_pair_count", 0)
                    or any(
                        str(raw.get("source", "")) == "pdf_text_seed"
                        for raw in record.get("annotations", [])
                        if isinstance(raw, dict)
                    )
                )
                return (not originally_seeded, str(path))

            self.record_paths.sort(
                key=stable_heading_order
            )
        if not self.record_paths:
            self.current_record = None
            self.progress_label.setText(
                "尚無頁面。請先執行 prepare_annotation_set.py 或匯入圖片/PDF。"
            )
            return

        self.page_jump_spin.setMaximum(len(self.record_paths))

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
        # Keep an owned copy for the whole lifetime of the displayed record.
        # Rebuilding annotation overlays must not depend on a temporary local
        # QPixmap object.
        self.current_pixmap = QPixmap(pixmap)

        self.current_record = record
        raw_annotations = list(record.get("annotations", []))
        self._page_had_pdf_text_seed = bool(
            record.get("had_pdf_text_seed", False)
            or any(
                str(raw.get("source", "")) == "pdf_text_seed"
                for raw in raw_annotations
            )
        )
        self._empty_seed_page_confirmed = bool(
            record.get("empty_seed_page_confirmed", False)
            and not raw_annotations
        )
        self.page_jump_spin.blockSignals(True)
        self.page_jump_spin.setValue(self.current_index + 1)
        self.page_jump_spin.blockSignals(False)
        self.annotations = []
        for raw in record.get("annotations", []):
            annotation = Annotation(**raw).normalized(record["width"], record["height"])
            if annotation.is_valid(record["width"], record["height"]):
                self.annotations.append(annotation)
        self.identifier_text_line.clear()
        self.sort_annotations()
        self.reviewed_checkbox.setChecked(bool(record.get("reviewed")))
        self.rebuild_scene(pixmap=pixmap, fit=fit)
        self.update_page_text()

    def sort_annotations(self):
        self.annotations.sort(key=lambda item: (item.y1, item.x1))

    def _annotation_list_entry(self, index, annotation):
        label = display_label(annotation.label)
        if annotation.label == "figure_identifier" and annotation.text:
            label = f"{label} {annotation.text}"
        pair_text = (
            f"  配對 {annotation.pair_id}"
            if str(annotation.pair_id or "").strip()
            else ""
        )
        return (
            f"{index + 1:03d}  {label:>8}  "
            f"({annotation.x1:.0f},{annotation.y1:.0f}){pair_text}"
        )

    def rebuild_scene(self, pixmap=None, fit=False, selected_index=None):
        if self.current_record is None:
            return
        scene = self.view.scene()
        self.box_items = []
        self.view._rubber_from = None
        self.view._rubber_to = None
        self.view._rubber_rect = None
        self.view._rubber_active = False
        scene.clear()

        if pixmap is not None:
            self.current_pixmap = QPixmap(pixmap)
        if self.current_pixmap is None or self.current_pixmap.isNull():
            self.current_pixmap = QPixmap(
                str(self.annotation_root / self.current_record["image_path"])
            )
        if self.current_pixmap.isNull():
            raise ValueError(
                f"Cannot reload image: {self.current_record['image_path']}"
            )
        self.background_item = scene.addPixmap(self.current_pixmap)
        self.background_item.setZValue(-1000)
        self.background_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.view.image_rect = QRectF(
            0,
            0,
            self.current_pixmap.width(),
            self.current_pixmap.height(),
        )
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
                text=annotation.text,
            )
            scene.addItem(item)
            self.box_items.append(item)
            self.annotation_list.addItem(
                self._annotation_list_entry(index, annotation)
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

    def _normalized_identifier_editor_text(self, show_error=False):
        raw_text = self.identifier_text_line.text().strip()
        if not raw_text:
            return ""
        try:
            return str(normalize_figure_identifier(raw_text))
        except ValueError as error:
            if show_error:
                QMessageBox.warning(self, "圖號格式不正確", str(error))
            return None

    def _sync_identifier_editor(self, index):
        text = ""
        if 0 <= index < len(self.annotations):
            annotation = self.annotations[index]
            if annotation.label == "figure_identifier":
                text = annotation.text
        self.identifier_text_line.blockSignals(True)
        self.identifier_text_line.setText(text)
        self.identifier_text_line.blockSignals(False)

    def add_box(self, rect):
        label = self.current_label()
        if not label or self.current_record is None:
            return
        identifier_text = ""
        if label == "figure_identifier":
            identifier_text = self._normalized_identifier_editor_text(
                show_error=True
            )
            if identifier_text is None:
                return
        annotation = Annotation(
            label=label,
            x1=rect.left(),
            y1=rect.top(),
            x2=rect.right(),
            y2=rect.bottom(),
            source="manual",
            text=identifier_text,
        )
        # Append incrementally instead of clearing/rebuilding QGraphicsScene
        # from a mouse-release callback.  Rebuilding here can invalidate Qt's
        # current mouse/paint objects and crash on Windows/PySide6.
        self.annotations.append(annotation)
        self._empty_seed_page_confirmed = False
        selected = len(self.annotations) - 1
        item = AnnotationRectItem(
            QRectF(
                annotation.x1,
                annotation.y1,
                annotation.x2 - annotation.x1,
                annotation.y2 - annotation.y1,
            ),
            annotation_index=selected,
            label=annotation.label,
            source=annotation.source,
            text=annotation.text,
        )
        self.view.scene().addItem(item)
        self.box_items.append(item)

        self.annotation_list.blockSignals(True)
        self.annotation_list.addItem(
            self._annotation_list_entry(selected, annotation)
        )
        self.annotation_list.blockSignals(False)

        self.reviewed_checkbox.setChecked(False)
        self.view.scene().clearSelection()
        item.setSelected(True)
        item.update_style()
        self.annotation_list.setCurrentRow(selected)
        if self.mode == "figure-heading" and label == "figure_identifier":
            self.select_current_label("figure_prefix")
            self.identifier_text_line.clear()
        self.update_summary()

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
            self._sync_identifier_editor(index)
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
        self._sync_identifier_editor(index)
        self._syncing_selection = False

    def apply_identifier_text(self):
        if self.mode != "figure-heading":
            return
        index = self.selected_index()
        if index < 0 or self.annotations[index].label != "figure_identifier":
            QMessageBox.information(
                self,
                "請先選取圖號框",
                "請先在圖面或清單選取一個「圖號」框。",
            )
            return
        normalized = self._normalized_identifier_editor_text(show_error=True)
        if not normalized:
            QMessageBox.warning(
                self,
                "缺少圖號文字",
                "請輸入這個框內的完整圖號，例如 1、3A、1' 或 B。",
            )
            return
        annotation = self.annotations[index]
        annotation.text = normalized
        annotation.source = "manual"
        annotation.pair_id = ""
        self.identifier_text_line.setText(normalized)
        self.reviewed_checkbox.setChecked(False)
        self.rebuild_scene(fit=False, selected_index=index)

    def apply_label(self):
        index = self.selected_index()
        label = self.current_label()
        if index < 0 or not label:
            return
        normalized = ""
        if label == "figure_identifier":
            normalized = self._normalized_identifier_editor_text(show_error=True)
            if normalized is None:
                return
        annotation = self.annotations[index]
        annotation.label = label
        annotation.source = "manual"
        annotation.pair_id = ""
        if label == "figure_identifier":
            annotation.text = normalized
        else:
            annotation.text = ""
        self.reviewed_checkbox.setChecked(False)
        self.rebuild_scene(fit=False, selected_index=index)

    def delete_selected(self):
        index = self.selected_index()
        if index < 0:
            return
        del self.annotations[index]
        self._empty_seed_page_confirmed = False
        self.reviewed_checkbox.setChecked(False)
        self.rebuild_scene(fit=False)

    def _confirm_empty_seed_page(self):
        if (
            self.mode != "figure-heading"
            or not self._page_had_pdf_text_seed
            or self.annotations
            or self._empty_seed_page_confirmed
        ):
            return True

        answer = QMessageBox.warning(
            self,
            "確認清空文字層種子",
            "這一頁原本含有文字層預填的「圖／圖號」種子，現在所有框都已刪除。\n\n"
            "只有在你已逐一確認本頁確實沒有任何圖題時，才選擇「是」將它完成為負樣本；"
            "選擇「取消」會保留為未完成草稿。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return False
        self._empty_seed_page_confirmed = True
        return True

    def _prepare_heading_completion(self):
        if self.mode != "figure-heading":
            return ""

        prefix_entries = [
            (index, item)
            for index, item in enumerate(self.annotations)
            if item.label in FIGURE_PREFIX_CLASS_NAMES
        ]
        identifier_entries = [
            (index, item)
            for index, item in enumerate(self.annotations)
            if item.label == "figure_identifier"
        ]
        if len(prefix_entries) != len(identifier_entries):
            return (
                "本頁的「圖」與「圖號」框數必須相等，目前為 "
                f"{len(prefix_entries)} / {len(identifier_entries)}。"
            )

        canonical_texts = []
        for _index, annotation in identifier_entries:
            if not annotation.text.strip():
                return "每個「圖號」框都必須輸入完整圖號文字。"
            try:
                canonical_texts.append(
                    str(normalize_figure_identifier(annotation.text))
                )
            except ValueError as error:
                return str(error)

        def as_box(annotation):
            return {
                "x1": annotation.x1,
                "y1": annotation.y1,
                "x2": annotation.x2,
                "y2": annotation.y2,
                "confidence": 1.0,
                "class_name": annotation.label,
            }

        pairs = pair_figure_heading_boxes(
            [as_box(item) for _index, item in prefix_entries],
            [as_box(item) for _index, item in identifier_entries],
        )
        if len(pairs) != len(prefix_entries):
            return (
                "有框無法依「圖→圖號」方向與距離配成一組；"
                "請調整框的位置或刪除錯框。"
            )

        for (_index, annotation), text in zip(identifier_entries, canonical_texts):
            annotation.text = text
        for pair_number, pair in enumerate(pairs, start=1):
            pair_id = f"caption-{pair_number:03d}"
            prefix_index = prefix_entries[pair["prefix_index"]][0]
            identifier_index = identifier_entries[pair["identifier_index"]][0]
            self.annotations[prefix_index].pair_id = pair_id
            self.annotations[identifier_index].pair_id = pair_id
        return ""

    def save_current(self):
        if self.current_record is None:
            return False
        completion_valid = True
        if self.reviewed_checkbox.isChecked():
            error = self._prepare_heading_completion()
            if error:
                completion_valid = False
                self.reviewed_checkbox.setChecked(False)
                QMessageBox.warning(self, "本頁尚未完成", error)
            elif not self._confirm_empty_seed_page():
                completion_valid = False
                self.reviewed_checkbox.setChecked(False)
        if self.mode == "figure-heading" and self._page_had_pdf_text_seed:
            self.current_record["had_pdf_text_seed"] = True
            self.current_record["empty_seed_page_confirmed"] = bool(
                self._empty_seed_page_confirmed and not self.annotations
            )
        self.current_record["reviewed"] = self.reviewed_checkbox.isChecked()
        self.current_record["annotations"] = [asdict(item) for item in self.annotations]
        write_page_record(self.record_paths[self.current_index], self.current_record)
        self.update_page_text()
        return completion_valid

    def finish_page(self):
        if self.current_record is None:
            return
        self.reviewed_checkbox.setChecked(True)
        if not self.save_current():
            return
        self.navigate(1)

    def navigate(self, direction):
        if not self.record_paths:
            return
        self.save_current()
        count = len(self.record_paths)
        candidate = max(0, min(self.current_index + int(direction), count - 1))
        if candidate == self.current_index:
            return
        self.current_index = candidate
        self.load_current(fit=True)

    def go_to_selected_page(self):
        """Save the current draft and open any requested page directly."""

        if not self.record_paths:
            return
        self.save_current()
        candidate = max(
            0,
            min(self.page_jump_spin.value() - 1, len(self.record_paths) - 1),
        )
        self.current_index = candidate
        self.load_current(fit=True)

    def update_summary(self):
        counts = Counter(item.label for item in self.annotations)
        if self.mode == "figure-heading":
            identifiers_with_text = sum(
                item.label == "figure_identifier" and bool(item.text.strip())
                for item in self.annotations
            )
            self.summary_label.setText(
                f"本頁框數：{len(self.annotations)}｜"
                f"圖：{counts['figure_prefix']}｜"
                f"橫著的圖：{counts[FIGURE_PREFIX_ROTATE_RIGHT_CLASS]}｜"
                f"圖號：{counts['figure_identifier']}｜"
                f"已填文字：{identifiers_with_text}/{counts['figure_identifier']}"
            )
            return
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
            f"頁面 {self.current_index + 1}/{len(self.record_paths)}｜已完成 {completed}｜"
            f"目前：{'已完成' if self.reviewed_checkbox.isChecked() else '草稿未完成'}"
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
    window = AnnotationWindow(args.dataset, mode=args.mode)
    window.show()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
