from ui.recognition_page import RecognitionPage
from ui.demo_tool_page import DemoToolPage
from ui.patent_review_page import PatentReviewPage
from ui.embodiment_figure_compare_page import EmbodimentFigureComparePage


FEATURES = [
    {
        "id": "patent_review",
        "title": "專利文件偵錯",
        "navigation_title": "文件偵錯",
        "description": "檢核完整專利說明書並將符號清單送往圖片標號識別",
        "page_class": PatentReviewPage
    },
    {
        "id": "patent_ocr",
        "title": "圖片標號識別",
        "navigation_title": "圖式標號",
        "description": "辨識專利圖式標號並與標號清單比對",
        "page_class": RecognitionPage
    },
    {
        "id": "embodiment_figure_compare",
        "title": "實施方式與圖式比對 (beta)",
        "navigation_title": "段落圖式比對 (beta)",
        "description": "並排選擇實施方式段落與圖式圖片，交叉檢視 OCR 標號差異",
        "page_class": EmbodimentFigureComparePage,
    },
    {
        "id": "demo_tool",
        "title": "功能測試頁",
        "show_in_navigation": False,
        "show_on_home": False,
        "description": "新增功能時可直接複製的空白頁面模板",
        "page_class": DemoToolPage
    }
]
