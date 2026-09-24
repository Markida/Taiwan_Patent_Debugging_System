"""Feature metadata with lazy page imports for faster visible startup."""

from __future__ import annotations

from importlib import import_module


class LazyPageClass:
    """Resolve a feature page only when the main window constructs that page."""

    def __init__(self, module_name: str, class_name: str):
        self.module_name = module_name
        self.class_name = class_name
        self._resolved_class = None

    def resolve(self):
        if self._resolved_class is None:
            module = import_module(self.module_name)
            self._resolved_class = getattr(module, self.class_name)
        return self._resolved_class

    def __call__(self, *args, **kwargs):
        return self.resolve()(*args, **kwargs)

    @property
    def __name__(self):
        return self.class_name


FEATURES = [
    {
        "id": "patent_review",
        "title": "專利文件偵錯",
        "navigation_title": "文件偵錯",
        "description": "檢核完整專利說明書並將符號清單送往圖片標號識別",
        "page_class": LazyPageClass(
            "ui.patent_review_page",
            "PatentReviewPage",
        ),
    },
    {
        "id": "patent_ocr",
        "title": "圖片標號識別",
        "navigation_title": "圖式標號",
        "description": "辨識專利圖式標號並與標號清單比對",
        "page_class": LazyPageClass(
            "ui.recognition_page",
            "RecognitionPage",
        ),
    },
    {
        "id": "embodiment_figure_compare",
        "title": "實施方式與圖式比對",
        "navigation_title": "段落圖式比對",
        "description": "並排選擇實施方式段落與圖式圖片，交叉檢視 OCR 標號差異",
        "page_class": LazyPageClass(
            "ui.embodiment_figure_compare_page",
            "EmbodimentFigureComparePage",
        ),
    },
    {
        "id": "taiwan_china_spec",
        "title": "台灣－大陸專利說明書轉換",
        "navigation_title": "台陸轉換",
        "description": "將台灣專利說明書轉入大陸範本並套用用語辭典",
        "page_class": LazyPageClass(
            "ui.taiwan_china_spec_page",
            "TaiwanChinaSpecPage",
        ),
    },
    {
        "id": "demo_tool",
        "title": "功能測試頁",
        "show_in_navigation": False,
        "show_on_home": False,
        "description": "新增功能時可直接複製的空白頁面模板",
        "page_class": LazyPageClass(
            "ui.demo_tool_page",
            "DemoToolPage",
        ),
    },
]
