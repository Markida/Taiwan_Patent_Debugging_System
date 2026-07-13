"""Small keyboard-first UI for approving character crop labels."""

import argparse
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent, QPixmap, QTransform
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from common import display_label, normalize_label, read_manifest, write_manifest


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_MANIFEST = SCRIPT_DIR / "review_dataset" / "manifest.csv"


def parse_args():
    parser = argparse.ArgumentParser(description="Review patent-character crop labels.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--all", action="store_true", help="Include approved/skipped rows.")
    parser.add_argument("--split", choices=("train", "val"), default=None)
    parser.add_argument("--kind", choices=("digits", "letters"), default=None)
    parser.add_argument("--max-confidence", type=float, default=None)
    return parser.parse_args()


class ReviewWindow(QMainWindow):
    def __init__(
        self,
        manifest_path,
        show_all=False,
        split=None,
        kind=None,
        max_confidence=None,
    ):
        super().__init__()
        self.manifest_path = Path(manifest_path).resolve()
        self.dataset_root = self.manifest_path.parent
        self.rows = read_manifest(self.manifest_path)
        self.indices = [
            index
            for index, row in enumerate(self.rows)
            if (split is None or row.get("split") == split)
            and (
                kind is None
                or (
                    kind == "digits"
                    and bool(row.get("label"))
                    and row.get("label") in "0123456789"
                )
                or (
                    kind == "letters"
                    and bool(row.get("label"))
                    and (
                        row.get("label") == "prime"
                        or row.get("label") in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                    )
                )
            )
            and (
                max_confidence is None
                or float(row.get("confidence") or 0.0) < max_confidence
            )
            and (show_all or row.get("status") not in {"approved", "skipped"})
        ]
        self.position = 0
        self.setWindowTitle("Patent character crop review")
        self.resize(760, 680)

        self.image_label = QLabel(alignment=Qt.AlignCenter)
        self.image_label.setMinimumSize(560, 420)
        self.info_label = QLabel()
        self.progress_label = QLabel()
        self.label_input = QLineEdit()
        self.label_input.setMaxLength(5)
        self.label_input.setPlaceholderText("0-9, A-Z, or '")
        self.label_input.returnPressed.connect(self.approve_current)

        approve_button = QPushButton("Approve (Enter)")
        approve_button.clicked.connect(self.approve_current)
        suggestion_button = QPushButton("Accept suggestion (Space)")
        suggestion_button.clicked.connect(self.accept_suggestion)
        skip_button = QPushButton("Skip (Ctrl+S)")
        skip_button.clicked.connect(self.skip_current)
        left_button = QPushButton("Rotate left (Ctrl+Q)")
        left_button.clicked.connect(lambda: self.rotate_current(-90))
        right_button = QPushButton("Rotate right (Ctrl+E)")
        right_button.clicked.connect(lambda: self.rotate_current(90))

        buttons = QHBoxLayout()
        for button in (
            approve_button,
            suggestion_button,
            skip_button,
            left_button,
            right_button,
        ):
            buttons.addWidget(button)

        layout = QVBoxLayout()
        layout.addWidget(self.image_label, 1)
        layout.addWidget(self.info_label)
        layout.addWidget(self.label_input)
        layout.addLayout(buttons)
        layout.addWidget(self.progress_label)

        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)
        self.show_current()

    def current_row(self):
        if not self.indices:
            return None
        return self.rows[self.indices[self.position]]

    def save(self):
        write_manifest(self.manifest_path, self.rows)

    def show_current(self):
        row = self.current_row()

        if row is None:
            self.image_label.setText("All selected crops are reviewed.")
            self.info_label.clear()
            self.progress_label.clear()
            self.label_input.setEnabled(False)
            return

        crop_path = self.dataset_root / row["crop_path"]
        pixmap = QPixmap(str(crop_path))
        rotation = int(row.get("rotation") or 0) % 360

        if rotation:
            pixmap = pixmap.transformed(QTransform().rotate(rotation))

        self.image_label.setPixmap(
            pixmap.scaled(
                self.image_label.size(),
                Qt.KeepAspectRatio,
                Qt.FastTransformation,
            )
        )
        confidence = float(row.get("confidence") or 0.0)
        self.info_label.setText(
            f"{row['id']} | suggestion: {display_label(row.get('label')) or '-'} "
            f"({confidence:.3f}) | rotation: {rotation}°"
        )
        self.progress_label.setText(
            f"Review item {self.position + 1} / {len(self.indices)}"
        )
        self.label_input.setText(display_label(row.get("label")))
        self.label_input.setFocus()
        self.label_input.selectAll()

    def advance(self):
        if self.position < len(self.indices) - 1:
            self.position += 1
        else:
            self.indices = [
                index
                for index in self.indices
                if self.rows[index].get("status") not in {"approved", "skipped"}
            ]
            self.position = 0
        self.show_current()

    def approve_current(self):
        row = self.current_row()
        label = normalize_label(self.label_input.text())

        if row is None or not label:
            return

        row["label"] = label
        row["status"] = "approved"
        self.save()
        self.advance()

    def accept_suggestion(self):
        row = self.current_row()

        if row is None:
            return

        label = normalize_label(row.get("label"))

        if label:
            self.label_input.setText(display_label(label))
            self.approve_current()

    def skip_current(self):
        row = self.current_row()

        if row is None:
            return

        row["status"] = "skipped"
        self.save()
        self.advance()

    def rotate_current(self, delta):
        row = self.current_row()

        if row is None:
            return

        row["rotation"] = (int(row.get("rotation") or 0) + delta) % 360
        self.save()
        self.show_current()

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key_Space:
            self.accept_suggestion()
            return
        if event.key() == Qt.Key_S and event.modifiers() & Qt.ControlModifier:
            self.skip_current()
            return
        if event.key() == Qt.Key_Q and event.modifiers() & Qt.ControlModifier:
            self.rotate_current(-90)
            return
        if event.key() == Qt.Key_E and event.modifiers() & Qt.ControlModifier:
            self.rotate_current(90)
            return
        if (
            event.key() == Qt.Key_Left
            and event.modifiers() & Qt.AltModifier
            and self.position > 0
        ):
            self.position -= 1
            self.show_current()
            return
        if (
            event.key() == Qt.Key_Right
            and event.modifiers() & Qt.AltModifier
            and self.position < len(self.indices) - 1
        ):
            self.position += 1
            self.show_current()
            return

        super().keyPressEvent(event)


def main():
    args = parse_args()

    if not args.manifest.exists():
        raise FileNotFoundError(
            f"Manifest not found: {args.manifest}\nRun prepare_crops.py first."
        )

    app = QApplication(sys.argv)
    window = ReviewWindow(
        args.manifest,
        show_all=args.all,
        split=args.split,
        kind=args.kind,
        max_confidence=args.max_confidence,
    )
    window.show()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
