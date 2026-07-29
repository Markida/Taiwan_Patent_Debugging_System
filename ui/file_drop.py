"""Reusable, conservative single-file drag-and-drop support for feature pages."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable, Optional

from PySide6.QtCore import QEvent, QObject, QUrl
from PySide6.QtWidgets import QMessageBox, QWidget


class SingleFileDropController(QObject):
    """Route one local file with an allowed suffix to an existing loader.

    The filter is installed on the page and all current child widgets so drops
    work over text edits, tables and preview panels instead of only empty space.
    Non-file drags are left untouched, preserving normal text editing behavior.
    """

    def __init__(
        self,
        owner: QWidget,
        *,
        allowed_suffix: str,
        file_type_name: str,
        on_file: Callable[[str], object],
    ) -> None:
        super().__init__(owner)
        self.owner = owner
        self.allowed_suffix = allowed_suffix.lower()
        self.file_type_name = file_type_name
        self.on_file = on_file
        self._install_on_page()

    def _install_on_page(self) -> None:
        widgets = [self.owner, *self.owner.findChildren(QWidget)]
        for widget in widgets:
            widget.setAcceptDrops(True)
            widget.installEventFilter(self)

    def eventFilter(self, watched, event):
        event_type = event.type()
        if event_type not in {
            QEvent.Type.DragEnter,
            QEvent.Type.DragMove,
            QEvent.Type.Drop,
        }:
            return False

        mime_data = event.mimeData()
        if not mime_data.hasUrls():
            return False

        if event_type in {QEvent.Type.DragEnter, QEvent.Type.DragMove}:
            # Accept first so an invalid file still reaches Drop, where a clear
            # explanation can be shown instead of silently refusing the cursor.
            event.acceptProposedAction()
            return True

        error, path = self._validate_urls(mime_data.urls())
        if error:
            QMessageBox.warning(self.owner, "無法拖入檔案", error)
        elif path is not None:
            self.on_file(str(path))
        event.acceptProposedAction()
        return True

    def _validate_urls(
        self,
        urls: Iterable[QUrl],
    ) -> tuple[Optional[str], Optional[Path]]:
        url_list = list(urls)
        if len(url_list) != 1:
            return "一次只能拖入一個檔案，請重新選擇。", None

        url = url_list[0]
        if not url.isLocalFile():
            return "只能拖入電腦上的本機檔案。", None

        path = Path(url.toLocalFile())
        if not path.is_file():
            return f"找不到拖入的檔案：\n{path}", None
        if path.suffix.lower() != self.allowed_suffix:
            return (
                f"此頁面僅接受 {self.file_type_name}（{self.allowed_suffix}）。\n\n"
                f"拖入檔案：{path.name}",
                None,
            )
        return None, path
