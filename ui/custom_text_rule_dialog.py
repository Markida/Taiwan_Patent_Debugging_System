"""Dialog for managing persistent blacklist and similarity whitelist rules."""

from __future__ import annotations

from functools import partial

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.background_tasks import BackgroundTaskRunner
from features.patent_review.custom_rules import (
    CUSTOM_RULE_BLACKLIST,
    CUSTOM_RULE_WHITELIST,
    CustomRuleError,
    CustomTextRule,
    CustomTextRuleStore,
)


class CustomTextRuleDialog(QDialog):
    """Manage exact-error phrases and component-similarity exemptions."""

    def __init__(self, store: CustomTextRuleStore, parent=None):
        super().__init__(parent)
        self.store = store
        self.rules_changed = False
        self.setWindowTitle("自訂文字規則")
        self.setMinimumSize(680, 470)
        self._tables = {}
        self._inputs = {}
        self._delete_buttons = {}
        self._add_buttons = {}
        self._rules = ()
        self._job_kind = ""
        self._closing = False
        self._tasks = BackgroundTaskRunner(self)
        self._tasks.succeeded.connect(self._job_succeeded)
        self._tasks.failed.connect(self._job_failed)
        self._tasks.finished.connect(self._job_finished)
        self._build_ui()
        self.refresh_rules()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        help_label = QLabel(
            "黑名單：逐字找到該文字時列為錯誤。\n"
            "白名單：只排除元件名稱的相似詞警告，不會關閉標號、數量詞或其他錯誤檢查。"
        )
        help_label.setWordWrap(True)
        layout.addWidget(help_label)

        self.rule_tabs = QTabWidget()
        self.rule_tabs.addTab(
            self._build_rule_tab(
                CUSTOM_RULE_BLACKLIST,
                "輸入需要偵測為錯誤的單行文字，例如「的的」或「個個」",
                "新增黑名單",
            ),
            "黑名單",
        )
        self.rule_tabs.addTab(
            self._build_rule_tab(
                CUSTOM_RULE_WHITELIST,
                "輸入不需要進行元件相似詞比對的文字",
                "新增白名單",
            ),
            "白名單",
        )
        layout.addWidget(self.rule_tabs, 1)

        # Backwards-compatible aliases used by existing UI tests and any
        # downstream automation. They intentionally point to the blacklist.
        self.rule_input = self._inputs[CUSTOM_RULE_BLACKLIST]
        self.rule_table = self._tables[CUSTOM_RULE_BLACKLIST]
        self.delete_button = self._delete_buttons[CUSTOM_RULE_BLACKLIST]

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.status_label)

        action_layout = QHBoxLayout()
        action_layout.addStretch()
        self.close_button = QPushButton("完成")
        self.close_button.setObjectName("SecondaryButton")
        self.close_button.clicked.connect(self.accept)
        action_layout.addWidget(self.close_button)
        layout.addLayout(action_layout)

    def _build_rule_tab(
        self,
        rule_type: str,
        placeholder: str,
        add_label: str,
    ) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        input_layout = QHBoxLayout()
        rule_input = QLineEdit()
        rule_input.setPlaceholderText(placeholder)
        rule_input.returnPressed.connect(partial(self.add_current_rule, rule_type))
        input_layout.addWidget(rule_input, 1)
        add_button = QPushButton(add_label)
        add_button.setObjectName("PrimaryButton")
        add_button.clicked.connect(
            lambda _checked=False, selected_type=rule_type: self.add_current_rule(
                selected_type
            )
        )
        input_layout.addWidget(add_button)
        layout.addLayout(input_layout)

        table = QTableWidget(0, 2)
        table.setHorizontalHeaderLabels(["文字", "規則編號"])
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        layout.addWidget(table, 1)

        delete_button = QPushButton("刪除選取項目")
        delete_button.setObjectName("ToolButton")
        delete_button.setEnabled(False)
        delete_button.clicked.connect(
            lambda _checked=False, selected_type=rule_type: self.delete_selected_rules(
                selected_type
            )
        )
        layout.addWidget(delete_button, 0, Qt.AlignLeft)

        self._inputs[rule_type] = rule_input
        self._tables[rule_type] = table
        self._delete_buttons[rule_type] = delete_button
        self._add_buttons[rule_type] = add_button
        table.itemSelectionChanged.connect(
            partial(self._update_delete_button, rule_type)
        )
        return page

    def refresh_rules(self) -> bool:
        """Queue a refresh; True means accepted, not yet read from the share."""
        store = self.store
        return self._start_job(
            "load", lambda cancel: {} if cancel.is_set() else {"rules": store.load()},
            "正在讀取共用規則…",
        )

    def _render_rules(self, rules):
        self._rules = tuple(rules)
        counts = {CUSTOM_RULE_BLACKLIST: 0, CUSTOM_RULE_WHITELIST: 0}
        for rule_type, table in self._tables.items():
            group = [rule for rule in self._rules if rule.rule_type == rule_type]
            counts[rule_type] = len(group)
            table.blockSignals(True)
            table.setUpdatesEnabled(False)
            try:
                table.setRowCount(len(group))
                for row, rule in enumerate(group):
                    text_item = QTableWidgetItem(rule.text)
                    text_item.setData(Qt.UserRole, rule.rule_id)
                    table.setItem(row, 0, text_item)
                    table.setItem(row, 1, QTableWidgetItem(rule.rule_id))
            finally:
                table.setUpdatesEnabled(True)
                table.blockSignals(False)
        self.status_label.setStyleSheet("color:#475569")
        self.status_label.setText(
            f"黑名單 {counts[CUSTOM_RULE_BLACKLIST]} 條 · "
            f"白名單 {counts[CUSTOM_RULE_WHITELIST]} 條 · {self.store.path}"
        )
        for rule_type in self._tables:
            self._update_delete_button(rule_type)

    def add_current_rule(self, rule_type: str = CUSTOM_RULE_BLACKLIST) -> bool:
        """Validate locally, then queue an atomic add using the existing store."""
        if self._closing or self._tasks.busy:
            return False
        rule_input = self._inputs[rule_type]
        try:
            rule = CustomTextRule.from_text(rule_input.text(), rule_type)
        except CustomRuleError as error:
            self.status_label.setText(str(error))
            self.status_label.setStyleSheet("color:#b91c1c")
            return False
        list_name = "黑名單" if rule_type == CUSTOM_RULE_BLACKLIST else "白名單"
        store = self.store

        def add(cancel):
            if cancel.is_set():
                return {}
            saved, created = store.add(rule.text, rule_type)
            result = {"rule": saved, "created": created, "rule_type": rule_type}
            if created:
                # The write can succeed while the following share read fails.
                # Report that distinction; never tell users the save failed.
                result.update(CustomTextRuleDialog._refresh_after_write(store))
            return result

        return self._start_job("add", add, f"正在儲存{list_name}…")

    def delete_selected_rules(
        self,
        rule_type: str = CUSTOM_RULE_BLACKLIST,
    ) -> int:
        """Return the number queued for deletion; completion updates the UI."""
        if self._closing or self._tasks.busy:
            return 0
        table = self._tables[rule_type]
        rule_ids = []
        for index in table.selectionModel().selectedRows():
            item = table.item(index.row(), 0)
            if item is not None and item.data(Qt.UserRole):
                rule_ids.append(str(item.data(Qt.UserRole)))
        if not rule_ids:
            return 0
        store = self.store
        selected_ids = tuple(rule_ids)

        def remove(cancel):
            if cancel.is_set():
                return {}
            removed = store.remove(selected_ids)
            return {"removed": removed, **CustomTextRuleDialog._refresh_after_write(store)}

        accepted = self._start_job("remove", remove, "正在刪除選取規則…")
        return len(selected_ids) if accepted else 0

    @staticmethod
    def _refresh_after_write(store):
        try:
            return {"rules": store.load()}
        except Exception as error:
            return {"reload_error": str(error)}

    def _start_job(self, kind, work, status):
        if self._closing or self._tasks.busy:
            return False
        self._job_kind = kind
        self.status_label.setText(status)
        self.status_label.setStyleSheet("color:#1d4ed8")
        if not self._tasks.start(work):
            return False
        self._set_busy_controls(True)
        return True

    def _set_busy_controls(self, busy):
        for rule_type in self._tables:
            self._inputs[rule_type].setEnabled(not busy)
            self._tables[rule_type].setEnabled(not busy)
            self._add_buttons[rule_type].setEnabled(not busy)
            self._update_delete_button(rule_type)
        # Window X / Escape are deliberately still available during slow I/O.
        self.close_button.setEnabled(not busy)

    def _job_finished(self):
        if not self._closing:
            self._set_busy_controls(False)

    def _job_failed(self, message):
        if self._closing:
            return
        if self._job_kind == "load":
            self._render_rules(())
        self.status_label.setText(message)
        self.status_label.setStyleSheet("color:#b91c1c")

    def _job_succeeded(self, result):
        if self._closing:
            return
        if "rules" in result:
            self._render_rules(result["rules"])
        if self._job_kind == "add":
            rule = result["rule"]
            list_name = "黑名單" if result["rule_type"] == CUSTOM_RULE_BLACKLIST else "白名單"
            if result["created"]:
                self.rules_changed = True
                self._inputs[result["rule_type"]].clear()
                self.status_label.setText(f"已將「{rule.text}」新增至{list_name}。")
            else:
                self.status_label.setText(f"{list_name}項目「{rule.text}」已經存在，不會重複新增。")
                self.status_label.setStyleSheet("color:#b45309")
        elif self._job_kind == "remove":
            self.rules_changed = self.rules_changed or bool(result["removed"])
            self.status_label.setText(f"已刪除 {result['removed']} 條規則。")
        if result.get("reload_error"):
            self.status_label.setText(self.status_label.text() + "\n規則清單重新讀取失敗：" + result["reload_error"])
            self.status_label.setStyleSheet("color:#b45309")

    def shutdown(self):
        self._closing = True
        self._tasks.shutdown()

    def done(self, result):
        self.shutdown()
        super().done(result)

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)

    def _update_delete_button(self, rule_type: str = CUSTOM_RULE_BLACKLIST):
        table = self._tables[rule_type]
        self._delete_buttons[rule_type].setEnabled(
            not self._tasks.busy and not self._closing
            and bool(table.selectionModel().selectedRows())
        )
