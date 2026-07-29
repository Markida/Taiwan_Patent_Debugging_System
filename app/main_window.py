from PySide6.QtWidgets import QStackedWidget

from app.config import APP_NAME
from app.styles import APP_STYLE
from ui.home_page import HomePage
from features.registry import FEATURES
from app.workflow_context import PatentWorkflowContext


class MainWindow(QStackedWidget):
    def __init__(self):
        super().__init__()

        self.setWindowTitle(APP_NAME)
        self.resize(1360, 860)

        self.feature_pages = {}
        self.workflow_context = PatentWorkflowContext()

        self.home_page = HomePage(
            features=FEATURES,
            open_feature_callback=self.open_feature
        )

        self.addWidget(self.home_page)

        for feature in FEATURES:
            feature_id = feature["id"]
            page_class = feature["page_class"]

            page = page_class(go_home_callback=self.go_home)

            set_workflow_context = getattr(page, "set_workflow_context", None)
            if callable(set_workflow_context):
                set_workflow_context(self.workflow_context)
            set_open_feature_callback = getattr(
                page, "set_open_feature_callback", None
            )
            if callable(set_open_feature_callback):
                set_open_feature_callback(self.open_feature)

            self.feature_pages[feature_id] = page
            self.addWidget(page)

        self.setCurrentWidget(self.home_page)
        self.setStyleSheet(APP_STYLE)

    def open_feature(self, feature_id):
        page = self.feature_pages.get(feature_id)

        if page:
            self.setCurrentWidget(page)

    def go_home(self):
        self.setCurrentWidget(self.home_page)
