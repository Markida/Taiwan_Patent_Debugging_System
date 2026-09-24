"""Isolated, manually opened research UI; no production issues are emitted."""
from PySide6.QtWidgets import QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton, QTextBrowser, QVBoxLayout
from PySide6.QtGui import QTextOption
from app.background_tasks import BackgroundTaskRunner
from features.patent_review.syntax_lab import analyze_syntax_lab, render_syntax_lab


class SyntaxLabDialog(QDialog):
    def __init__(self, document, parent=None):
        super().__init__(parent)
        self.setWindowTitle("語法對照研究台（內測／不影響正式檢核）")
        self.resize(1000, 740)
        self.report = None
        layout = QVBoxLayout(self)
        self.status = QLabel("正在背景整理候選…；這不是涵蓋判定。")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        controls = QHBoxLayout()
        self.claim = QComboBox()
        self.claim.setMinimumWidth(250)
        self.section = QComboBox()
        self.section.setMinimumWidth(170)
        self.section.addItem("發明／新型內容", "disclosure")
        self.section.addItem("實施方式", "embodiments")
        controls.addWidget(self.claim)
        controls.addWidget(self.section)
        controls.addStretch()
        close = QPushButton("關閉")
        close.clicked.connect(self.reject)
        controls.addWidget(close)
        layout.addLayout(controls)
        self.viewer = QTextBrowser()
        self.viewer.setOpenExternalLinks(False)
        self.viewer.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        layout.addWidget(self.viewer)
        self.claim.currentIndexChanged.connect(self.refresh)
        self.section.currentIndexChanged.connect(self.refresh)
        self.task = BackgroundTaskRunner(self)
        self.task.succeeded.connect(self.ready)
        self.task.failed.connect(lambda error: self.status.setText("內測未完成：" + error))
        self.finished.connect(lambda _: self.task.shutdown())
        self.task.start(lambda cancel: analyze_syntax_lab(document, cancel=cancel))

    def ready(self, report):
        self.report = report
        self.claim.blockSignals(True)
        for c in report["claims"]:
            self.claim.addItem(f"請求項{c['number']}（{'獨立標的' if c['kind'] == 'independent' else '附屬／待確認'}）", c["claim_id"])
        self.claim.blockSignals(False)
        self.status.setText("僅列候選原文；不合併態樣、不判定涵蓋、不改写文件。附屬項請切換至實施方式。")
        self.refresh()

    def refresh(self):
        if self.report is not None:
            self.viewer.setHtml(render_syntax_lab(self.report, claim_id=self.claim.currentData(), section=self.section.currentData()))
