from ui.recognition_page import RecognitionPage
from ui.demo_tool_page import DemoToolPage


FEATURES = [
    {
        "id": "patent_ocr",
        "title": "圖片標號識別",
        "description": "辨識專利圖式標號並與標號清單比對",
        "page_class": RecognitionPage
    },
    {
        "id": "demo_tool",
        "title": "功能測試頁",
        "description": "新增功能時可直接複製的空白頁面模板",
        "page_class": DemoToolPage
    }
]
