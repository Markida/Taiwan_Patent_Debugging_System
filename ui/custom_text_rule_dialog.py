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

from features.patent_review.custom_rules import (
    CUSTOM_RULE_BLACKLIST,
    CUSTOM_RULE_WHITELIST,
    CustomRuleError,
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
        close_button = QPushButton("完成")
        close_button.setObjectName("SecondaryButton")
        close_button.clicked.connect(self.accept)
        action_layout.addWidget(close_button)
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
        table.itemSelectionChanged.connect(
            partial(self._update_delete_button, rule_type)
        )
        return page

    def refresh_rules(self) -> bool:
        try:
            rules = self.store.load()
        except CustomRuleError as error:
            for table in self._tables.values():
                table.setRowCount(0)
            self.status_label.setText(str(error))
            self.status_label.setStyleSheet("color:#b91c1c")
            for rule_type in self._tables:
                self._update_delete_button(rule_type)
            return False

        for table in self._tables.values():
            table.setRowCount(0)
        counts = {CUSTOM_RULE_BLACKLIST: 0, CUSTOM_RULE_WHITELIST: 0}
        for rule in rules:
            table = self._tables[rule.rule_type]
            row = table.rowCount()
            table.insertRow(row)
            text_item = QTableWidgetItem(rule.text)
            text_item.setData(Qt.UserRole, rule.rule_id)
            table.setItem(row, 0, text_item)
            table.setItem(row, 1, QTableWidgetItem(rule.rule_id))
            counts[rule.rule_type] += 1
        self.status_label.setStyleSheet("color:#475569")
        self.status_label.setText(
            f"黑名單 {counts[CUSTOM_RULE_BLACKLIST]} 條 · "
            f"白名單 {counts[CUSTOM_RULE_WHITELIST]} 條 · {self.store.path}"
        )
        for rule_type in self._tables:
            self._update_delete_button(rule_type)
        return True

    def add_current_rule(self, rule_type: str = CUSTOM_RULE_BLACKLIST) -> bool:
        rule_input = self._inputs[rule_type]
        try:
            rule, created = self.store.add(rule_input.text(), rule_type)
        except CustomRuleError as error:
            self.status_label.setText(str(error))
            self.status_label.setStyleSheet("color:#b91c1c")
            return False
        list_name = "黑名單" if rule_type == CUSTOM_RULE_BLACKLIST else "白名單"
        if not created:
            self.status_label.setText(
                f"{list_name}項目「{rule.text}」已經存在，不會重複新增。"
            )
            self.status_label.setStyleSheet("color:#b45309")
            return False
        self.rules_changed = True
        rule_input.clear()
        self.refresh_rules()
        self.status_label.setText(f"已將「{rule.text}」新增至{list_name}。")
        return True

    def delete_selected_rules(
        self,
        rule_type: str = CUSTOM_RULE_BLACKLIST,
    ) -> int:
        table = self._tables[rule_type]
        rule_ids = []
        for index in table.selectionModel().selectedRows():
            item = table.item(index.row(), 0)
            if item is not None and item.data(Qt.UserRole):
                rule_ids.append(str(item.data(Qt.UserRole)))
        if not rule_ids:
            return 0
        try:
            removed = self.store.remove(rule_ids)
        except CustomRuleError as error:
            self.status_label.setText(str(error))
            self.status_label.setStyleSheet("color:#b91c1c")
            return 0
        if removed:
            self.rules_changed = True
            self.refresh_rules()
            self.status_label.setText(f"已刪除 {removed} 條規則。")
        return removed

    def _update_delete_button(self, rule_type: str = CUSTOM_RULE_BLACKLIST):
        table = self._tables[rule_type]
        self._delete_buttons[rule_type].setEnabled(
            bool(table.selectionModel().selectedRows())
        )
