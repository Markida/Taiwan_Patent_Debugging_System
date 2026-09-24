"""Section-grouped, occurrence-specific review in the editable preview."""

from dataclasses import dataclass, replace

from PySide6.QtCore import QItemSelectionModel, QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QTextBlockFormat, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView, QFrame, QLabel, QPushButton, QStyledItemDelegate,
    QStyleOptionViewItem, QTextEdit,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from features.taiwan_china_spec.article_review import (
    ARTICLE_SECTIONS,
    find_article_occurrences,
    rebase_excluded_starts as rebase_token_starts,
)


@dataclass(frozen=True)
class ReviewSnapshot:
    html: str
    text: str
    cursor_position: int
    cursor_anchor: int
    scroll_value: int
    selected_starts: frozenset[int]


class WrappedOccurrenceDelegate(QStyledItemDelegate):
    """Size complete two-part rows at the tree's current available width."""

    def sizeHint(self, option, index):
        view_option = QStyleOptionViewItem(option)
        self.initStyleOption(view_option, index)
        tree = self.parent()
        depth = 1
        ancestor = index.parent()
        while ancestor.isValid():
            depth += 1
            ancestor = ancestor.parent()
        width = max(60, tree.viewport().width() - tree.indentation() * depth - 12)
        bounds = view_option.fontMetrics.boundingRect(
            QRect(0, 0, width, 10000), Qt.TextWordWrap, view_option.text
        )
        return QSize(width, bounds.height() + 16)


class ArticleReviewPanel(QFrame):
    """Review and delete exact selected tokens across the preview sections.

    Positions are UTF-16 offsets for QTextCursor. Highlights are display-only
    extra selections, and keyboard/context-menu undo preserves redline styles.
    Live cursors keep selections attached to their tokens during manual edits.
    """

    HISTORY_LIMIT = 50

    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self.editor = editor
        self.setObjectName("Panel")
        self._occurrences = []
        self._selected_starts = set()
        self._selection_anchors = []
        self._undo_history = []
        self._redo_history = []
        self._internal_change = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 7, 8, 8)
        layout.setSpacing(5)
        title = QLabel("「一個」檢查")
        title.setObjectName("PanelTitle")
        layout.addWidget(title)
        self.selection_hint = QLabel("可使用shift/ctrl進行複數選取")
        self.selection_hint.setObjectName("SyncStatus")
        self.selection_hint.setWordWrap(True)
        layout.addWidget(self.selection_hint)
        self.count_label = QLabel()
        self.count_label.setObjectName("SyncStatus")
        self.count_label.setWordWrap(True)
        layout.addWidget(self.count_label)

        self.occurrence_list = QTreeWidget()
        self.occurrence_list.setObjectName("ArticleOccurrenceList")
        self.occurrence_list.setColumnCount(1)
        self.occurrence_list.setHeaderHidden(True)
        self.occurrence_list.setIndentation(12)
        self.occurrence_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.occurrence_list.setWordWrap(True)
        self.occurrence_list.setTextElideMode(Qt.ElideNone)
        self.occurrence_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.occurrence_list.setMinimumHeight(80)
        self.occurrence_list.setItemDelegate(WrappedOccurrenceDelegate(self.occurrence_list))
        self.occurrence_list.header().sectionResized.connect(
            self.occurrence_list.doItemsLayout
        )
        self.occurrence_list.setStyleSheet(
            "QTreeWidget {background:#ffffff; color:#102a43; border:1px solid #cbd5e1;"
            " border-radius:7px; font-family:'Microsoft JhengHei'; font-size:12px;}"
            "QTreeWidget::item {padding:7px 3px; border-bottom:1px solid #e2e8f0;}"
            "QTreeWidget::item:selected {background:#bfdbfe; color:#102a43;}"
        )
        self.occurrence_list.setToolTip(
            "每列顯示一處「一個」的前後文；點選定位完整原句，Ctrl／Shift 可複選。\n"
            "各章節內優先置頂：每一個、各一個、上一個、下一個、其中一個、另一個、"
            "其中另一個、至少一個、第一個及後 10 字內接空間的用法。"
        )
        self.occurrence_list.currentItemChanged.connect(self._highlight_item)
        self.occurrence_list.itemClicked.connect(self._highlight_item)
        self.occurrence_list.itemSelectionChanged.connect(self._selection_changed)
        layout.addWidget(self.occurrence_list, 1)

        self.delete_selected_button = QPushButton("刪除所選項目")
        self.delete_selected_button.setObjectName("ToolButton")
        self.delete_selected_button.setToolTip("只刪除所選位置的「一個」兩字，保留句子其餘內容")
        self.delete_selected_button.clicked.connect(self.delete_selected)
        layout.addWidget(self.delete_selected_button)

        self._last_snapshot = self._snapshot()
        self.editor.document().contentsChange.connect(self._contents_changed)
        self.editor.textChanged.connect(self._text_changed)
        self.editor.review_history = self
        self._rebuild_list()

    def _selected_positions(self):
        return {
            hit.start
            for item in self.occurrence_list.selectedItems()
            if (hit := item.data(0, Qt.UserRole)) is not None
        }

    def _snapshot(self):
        cursor = self.editor.textCursor()
        return ReviewSnapshot(
            html=self.editor.toHtml(), text=self.editor.toPlainText(),
            cursor_position=cursor.position(), cursor_anchor=cursor.anchor(),
            scroll_value=self.editor.verticalScrollBar().value(),
            selected_starts=frozenset(self._selected_positions()),
        )

    def reset(self):
        """Start a fresh review when the page loads a different preview."""
        self._selected_starts.clear()
        self._undo_history.clear()
        self._redo_history.clear()
        self._rebuild_list()
        self._last_snapshot = self._snapshot()

    def _remember(self, snapshot):
        self._undo_history.append(snapshot)
        del self._undo_history[:-self.HISTORY_LIMIT]
        self._redo_history.clear()

    def _selection_changed(self):
        self._selected_starts = self._selected_positions()
        self._rebuild_selection_anchors()
        self._update_buttons()
        if self.editor.toPlainText() == self._last_snapshot.text:
            # Selecting list rows never changes document markup. Avoid
            # serialising a whole patent's rich text on every row click.
            cursor = self.editor.textCursor()
            self._last_snapshot = replace(
                self._last_snapshot,
                cursor_position=cursor.position(), cursor_anchor=cursor.anchor(),
                scroll_value=self.editor.verticalScrollBar().value(),
                selected_starts=frozenset(self._selected_starts),
            )

    def _rebuild_selection_anchors(self):
        self._selection_anchors = []
        for position in sorted(self._selected_starts):
            start = QTextCursor(self.editor.document())
            start.setPosition(position)
            start.setKeepPositionOnInsert(False)
            end = QTextCursor(self.editor.document())
            end.setPosition(position + 2)
            end.setKeepPositionOnInsert(True)
            self._selection_anchors.append((start, end))

    def _contents_changed(self, _position, _removed, _added):
        if self._internal_change:
            return
        # A grouped edit reports one broad changed range; individual live
        # boundary cursors still distinguish untouched tokens inside it.
        retained = set()
        for start, end in self._selection_anchors:
            if end.position() - start.position() != 2:
                continue
            token = QTextCursor(start)
            token.setPosition(end.position(), QTextCursor.KeepAnchor)
            if token.selectedText() == "一個":
                retained.add(start.position())
        self._selected_starts = retained

    def _text_changed(self):
        if self._internal_change:
            return
        if self.editor.toPlainText() != self._last_snapshot.text:
            self._remember(self._last_snapshot)
        self._rebuild_list()
        self._last_snapshot = self._snapshot()

    def _rebuild_list(self, selected_starts=None):
        self.editor.setExtraSelections([])
        self._occurrences = find_article_occurrences(self.editor.toPlainText())
        if selected_starts is not None:
            self._selected_starts = set(selected_starts)
        self._selected_starts.intersection_update(hit.start for hit in self._occurrences)
        expanded = {
            self.occurrence_list.topLevelItem(index).data(0, Qt.UserRole + 1):
            self.occurrence_list.topLevelItem(index).isExpanded()
            for index in range(self.occurrence_list.topLevelItemCount())
        }
        order = {hit.start: index for index, hit in enumerate(self._occurrences, 1)}
        self.occurrence_list.blockSignals(True)
        try:
            self.occurrence_list.clear()
            for section, title in ARTICLE_SECTIONS:
                hits = [hit for hit in self._occurrences if hit.section == section]
                group = QTreeWidgetItem([f"{title}（{len(hits)}）"])
                group.setData(0, Qt.UserRole + 1, section)
                group.setFlags(Qt.ItemIsEnabled)
                font = group.font(0)
                font.setBold(True)
                group.setFont(0, font)
                group.setBackground(0, QColor("#eaf1f8"))
                group.setForeground(0, QColor("#173e67"))
                self.occurrence_list.addTopLevelItem(group)
                for hit in sorted(hits, key=lambda hit: (not hit.is_priority, hit.start)):
                    prefix = f"第 {hit.paragraph_index} 段 · 第 {order[hit.start]} 處"
                    if hit.is_priority:
                        prefix = f"★ {hit.priority_reason}｜{prefix}"
                    item = QTreeWidgetItem([f"{prefix}\n{self._context_label(hit)}"])
                    item.setData(0, Qt.UserRole, hit)
                    item.setToolTip(0, hit.sentence)
                    if hit.is_priority:
                        item.setBackground(0, QColor("#fff8dc"))
                        item.setForeground(0, QColor("#102a43"))
                    group.addChild(item)
                    item.setSelected(hit.start in self._selected_starts)
                group.setExpanded(expanded.get(section, True))
        finally:
            self.occurrence_list.blockSignals(False)
        self._rebuild_selection_anchors()
        self._update_buttons()

    @staticmethod
    def _context_label(hit):
        # Keep long claims compact in the list without losing full tooltips.
        encoded = hit.sentence.encode("utf-16-le")
        relative = (hit.start - hit.sentence_start) * 2
        before = encoded[:relative].decode("utf-16-le")
        after = encoded[relative + 4:].decode("utf-16-le")
        return (
            ("…" if len(before) > 18 else "") + before[-18:]
            + "【一個】" + after[:32] + ("…" if len(after) > 32 else "")
        )

    def _update_buttons(self):
        enabled = self.editor.isEnabled() and not self.editor.isReadOnly()
        selected = len(self._selected_positions())
        total = len(self._occurrences)
        self.delete_selected_button.setEnabled(enabled and selected > 0)
        priority = sum(hit.is_priority for hit in self._occurrences)
        self.count_label.setText(f"共 {total} · 已選 {selected} · 優先 {priority}")

    def _highlight_item(self, item, _previous=None):
        hit = item.data(0, Qt.UserRole) if item is not None else None
        if hit is None:
            self.editor.setExtraSelections([])
            return
        cursor = QTextCursor(self.editor.document())
        cursor.setPosition(hit.start)
        cursor.setPosition(hit.end, QTextCursor.KeepAnchor)
        if cursor.selectedText() != "一個":
            self._rebuild_list()
            return
        sentence = QTextEdit.ExtraSelection()
        sentence.cursor = QTextCursor(self.editor.document())
        sentence.cursor.setPosition(hit.sentence_start)
        sentence.cursor.setPosition(hit.sentence_end, QTextCursor.KeepAnchor)
        sentence.format.setBackground(QColor("#fff59d"))
        article = QTextEdit.ExtraSelection()
        article.cursor = cursor
        article.format.setBackground(QColor("#ffcc80"))
        self.editor.setExtraSelections([sentence, article])
        cursor.clearSelection()
        cursor.setPosition(hit.start)
        self.editor.setTextCursor(cursor)
        self.editor.ensureCursorVisible()
        scroll = self.editor.verticalScrollBar()
        scroll.setValue(
            scroll.value() + self.editor.cursorRect().top() - self.editor.viewport().height() // 3
        )

    def delete_selected(self):
        self._delete_occurrences(selected=True)

    def delete_unselected(self):
        """Complementary deletion backend; not exposed by any UI control."""
        self._delete_occurrences(selected=False)

    def _ordered_items(self):
        tree = self.occurrence_list
        return [
            tree.topLevelItem(group).child(row)
            for group in range(tree.topLevelItemCount())
            for row in range(tree.topLevelItem(group).childCount())
        ]

    def _following_start(self, chosen):
        # Follow the actual grouped/priority-sorted list, not document offsets.
        # With disjoint selections, continue from the first removed row so no
        # unchecked item between selections is skipped. At the end, go back
        # to the closest preceding survivor instead of wrapping to the top.
        starts = [item.data(0, Qt.UserRole).start for item in self._ordered_items()]
        first = next(index for index, start in enumerate(starts) if start in chosen)
        candidates = starts[first + 1:] + list(reversed(starts[:first]))
        return next((start for start in candidates if start not in chosen), None)

    def _select_following_item(self, start):
        items = self._ordered_items()
        if not items:
            self.occurrence_list.setCurrentItem(None)
            self.occurrence_list.clearSelection()
            self.editor.setExtraSelections([])
            return
        item = next(
            (item for item in items if item.data(0, Qt.UserRole).start == start),
            items[0],  # Deletion can itself join text into a new "一個".
        )
        item.parent().setExpanded(True)
        self.occurrence_list.setCurrentItem(item, 0, QItemSelectionModel.ClearAndSelect)
        self.occurrence_list.scrollToItem(item)

    def _delete_occurrences(self, *, selected):
        if not self.editor.isEnabled() or self.editor.isReadOnly():
            return
        snapshot = self._snapshot()
        chosen = set(snapshot.selected_starts)
        # Always scan the current document; never delete from stale tree rows.
        self._rebuild_list(chosen)
        targets = [
            hit for hit in self._occurrences
            if (hit.start in chosen) == selected
        ]
        if not targets:
            return
        following = self._following_start(chosen) if selected else None
        following_starts = {following} if following is not None else set()
        self._remember(snapshot)
        cursor = QTextCursor(self.editor.document())
        self._internal_change = True
        cursor.beginEditBlock()
        try:
            for hit in sorted(targets, key=lambda hit: hit.start, reverse=True):
                cursor.setPosition(hit.start)
                cursor.setPosition(hit.end, QTextCursor.KeepAnchor)
                if cursor.selectedText() == "一個":
                    cursor.removeSelectedText()
                    self._selected_starts = rebase_token_starts(
                        self._selected_starts, hit.start, 2, 0
                    )
                    following_starts = rebase_token_starts(following_starts, hit.start, 2, 0)
        finally:
            cursor.endEditBlock()
            self._internal_change = False
        self._rebuild_list()
        if selected:
            self._select_following_item(next(iter(following_starts), None))
        self._last_snapshot = self._snapshot()

    def can_undo(self):
        return bool(self._undo_history)

    def apply_text_edits(self, edits, expected_text, *, content_edit=None):
        """Apply validated Python-offset edits as one undoable preview change.

        Only replaced content gets body styling. Other paragraphs retain their
        original rich-text formatting, and live selections use UTF-16 offsets.
        """
        if not self.editor.isEnabled() or self.editor.isReadOnly():
            return False
        snapshot = self._snapshot()
        if snapshot.text != expected_text:
            return False
        ordered = sorted(edits, key=lambda edit: (edit[0], edit[1]))
        previous_end = 0
        for start, end, replacement in ordered:
            if not (previous_end <= start <= end <= len(expected_text)):
                raise ValueError("Overlapping or invalid preview replacement")
            if not isinstance(replacement, str):
                raise ValueError("Preview replacement must be text")
            previous_end = end
        ordered = [edit for edit in ordered if expected_text[edit[0]:edit[1]] != edit[2]]
        if not ordered:
            return False

        self._remember(snapshot)
        cursor = QTextCursor(self.editor.document())
        self._internal_change = True
        cursor.beginEditBlock()
        try:
            for edit in reversed(ordered):
                start, end, replacement = edit
                utf_start = len(expected_text[:start].encode("utf-16-le")) // 2
                utf_end = len(expected_text[:end].encode("utf-16-le")) // 2
                utf_added = len(replacement.encode("utf-16-le")) // 2
                following_block = self.editor.document().findBlock(utf_end)
                boundary_style = None
                if edit == content_edit and following_block.isValid() and following_block.position() == utf_end:
                    boundary_style = (following_block.blockFormat(), following_block.charFormat())
                cursor.setPosition(utf_start)
                cursor.setPosition(utf_end, QTextCursor.KeepAnchor)
                if edit == content_edit:
                    body_style = QTextCharFormat()
                    body_style.setFontFamilies(["Microsoft JhengHei"])
                    body_style.setFontPointSize(11.25)
                    body_style.setFontWeight(QFont.Normal)
                    body_style.setForeground(QColor("#c62828"))
                    cursor.insertText(replacement, body_style)
                    # Never include the following heading's block in styling.
                    if replacement.rstrip("\r\n"):
                        body_cursor = QTextCursor(self.editor.document())
                        body_cursor.setPosition(utf_start)
                        body_cursor.setPosition(
                            utf_start + len(replacement.rstrip("\r\n").encode("utf-16-le")) // 2,
                            QTextCursor.KeepAnchor,
                        )
                        block_style = QTextBlockFormat()
                        block_style.setAlignment(Qt.AlignLeft)
                        block_style.setTopMargin(5)
                        block_style.setBottomMargin(5)
                        body_cursor.setBlockFormat(block_style)
                    if boundary_style is not None:
                        following_cursor = QTextCursor(self.editor.document())
                        following_cursor.setPosition(utf_start + utf_added)
                        following_cursor.setBlockFormat(boundary_style[0])
                        following_cursor.setBlockCharFormat(boundary_style[1])
                else:
                    cursor.insertText(replacement)
                self._selected_starts = rebase_token_starts(
                    self._selected_starts, utf_start, utf_end - utf_start, utf_added
                )
        finally:
            cursor.endEditBlock()
            self._internal_change = False
        self._rebuild_list()
        if content_edit is not None:
            position = len(expected_text[:content_edit[0]].encode("utf-16-le")) // 2
            cursor.setPosition(min(position, self.editor.document().characterCount() - 1))
            cursor.clearSelection()
            self.editor.setTextCursor(cursor)
            self.editor.ensureCursorVisible()
        self._last_snapshot = self._snapshot()
        return True

    def can_redo(self):
        return bool(self._redo_history)

    def _restore(self, snapshot):
        self._internal_change = True
        try:
            if self.editor.toHtml() != snapshot.html:
                self.editor.setHtml(snapshot.html)
            cursor = self.editor.textCursor()
            limit = self.editor.document().characterCount() - 1
            cursor.setPosition(min(snapshot.cursor_anchor, limit))
            cursor.setPosition(min(snapshot.cursor_position, limit), QTextCursor.KeepAnchor)
            self.editor.setTextCursor(cursor)
        finally:
            self._internal_change = False
        self._rebuild_list(snapshot.selected_starts)
        self.editor.verticalScrollBar().setValue(snapshot.scroll_value)
        self._last_snapshot = self._snapshot()

    def undo(self):
        if self.editor.isEnabled() and not self.editor.isReadOnly() and self.can_undo():
            self._redo_history.append(self._snapshot())
            self._restore(self._undo_history.pop())

    def redo(self):
        if self.editor.isEnabled() and not self.editor.isReadOnly() and self.can_redo():
            self._undo_history.append(self._snapshot())
            self._restore(self._redo_history.pop())
