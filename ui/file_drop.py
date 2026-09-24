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
        allowed_suffix: str | Iterable[str],
        file_type_name: str,
        on_file: Callable[[str], object],
    ) -> None:
        super().__init__(owner)
        self.owner = owner
        suffix_values = (
            (allowed_suffix,)
            if isinstance(allowed_suffix, str)
            else tuple(allowed_suffix)
        )
        self.allowed_suffixes = tuple(
            dict.fromkeys(str(suffix).lower() for suffix in suffix_values)
        )
        if not self.allowed_suffixes:
            raise ValueError("allowed_suffix 至少需要一個副檔名")
        # Keep the original public attribute for callers that configure one
        # suffix while allowing the converter page to accept both Word formats.
        self.allowed_suffix = self.allowed_suffixes[0]
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
        if path.suffix.lower() not in self.allowed_suffixes:
            suffix_label = " / ".join(self.allowed_suffixes)
            return (
                f"此頁面僅接受 {self.file_type_name}（{suffix_label}）。\n\n"
                f"拖入檔案：{path.name}",
                None,
            )
        return None, path


class RoutedPatentFileDropController(QObject):
    """Route DOCX and PDF drops from any application page to their loaders."""

    def __init__(self, owner, *, on_docx, on_pdf):
        super().__init__(owner)
        self.owner = owner
        self.callbacks = {
            ".docx": on_docx,
            ".pdf": on_pdf,
        }
        self._installed_widgets = set()
        self._install_widget(owner)

    def _install_widget(self, widget):
        """Install this router once without swallowing unrelated app drops."""

        if widget in self._installed_widgets:
            return
        widget.setAcceptDrops(True)
        widget.installEventFilter(self)
        self._installed_widgets.add(widget)

    def register_widget_tree(self, widget):
        """Enable cross-feature DOCX/PDF routing on one formal page.

        Feature pages are created lazily, so scanning the main window once at
        startup misses most of their children.  Registering each page after it
        is constructed also installs this router *after* its page-local drop
        filter, ensuring a DOCX dropped over OCR (or a PDF over document
        review) is routed instead of being rejected by the current page.

        Hidden chat/game pages intentionally are not registered: they have
        their own image/file interactions which must remain independent.
        """

        for child in [widget, *widget.findChildren(QWidget)]:
            self._install_widget(child)

    def unregister_widget_tree(self, widget):
        """Remove routing from a page which is being discarded."""

        for child in [widget, *widget.findChildren(QWidget)]:
            if child not in self._installed_widgets:
                continue
            child.removeEventFilter(self)
            self._installed_widgets.discard(child)

    def _warn_invalid_extension(self, path):
        return (
            "僅支援 Word 文件（.docx）或 PDF 圖式（.pdf）。\n\n"
            f"拖入檔案：{path.name}"
        )

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
        error, path, callback = self._route_for_urls(mime_data.urls())
        if event_type in {QEvent.Type.DragEnter, QEvent.Type.DragMove}:
            event.acceptProposedAction()
            return True
        if error:
            QMessageBox.warning(self.owner, "無法拖入檔案", error)
        elif path is not None:
            callback(str(path))
        event.acceptProposedAction()
        return True

    def _route_for_urls(self, urls):
        url_list = list(urls)
        if len(url_list) != 1:
            return "一次只能拖入一個 Word 或 PDF 檔案。", None, None
        url = url_list[0]
        if not url.isLocalFile():
            return "只能拖入電腦上的本機檔案。", None, None
        path = Path(url.toLocalFile())
        callback = self.callbacks.get(path.suffix.lower())
        if callback is None:
            return self._warn_invalid_extension(path), None, None
        if not path.is_file():
            return f"找不到拖入的檔案：\n{path}", None, None
        return None, path, callback
