APP_VERSION = "1.09.0"
APP_NAME = f"Saint-Island_Patent_MDS v{APP_VERSION}"

# 公司區域網路錯誤回報資料夾。
REVIEW_REPORT_NETWORK_DIR = (
    r"\\CPC2856\Saint-Island_Patent_MDS\錯誤回報"
)

# YOLO / OCR 參數
YOLO_CONF = 0.25
# High-recall policy requested for the 63-class v3 character model. On the 18
# manually-labelled validation pages, 0.05 retained 96.90% recall and 97.23%
# precision. Low-confidence results remain visible in red for manual review.
# Group locators and legacy character models continue to use YOLO_CONF.
V3_YOLO_CONF = 0.05
YOLO_IOU = 0.40
OCR_CONF = 0.20
IMG_SIZE = 1536
PAD = 6
OCR_ALLOWLIST = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'"

# Drawing symbols and line fragments are occasionally classified as ordinary
# letters by the very-low 0.05 high-recall detector.  Apply a separate, modest
# threshold to normal alphabetic labels without reducing numeric recall.
# I/V/X stay exempt because they are also the Roman-numeral classes used in
# patent drawings. J keeps its existing stricter 0.90 policy below.
LETTER_MIN_CONFIDENCE = 0.20
FILTERED_LETTER_CLASSES = "ABCDEFGHKLMNOPQRSTUWYZ"
LETTER_CHARACTER_MIN_CONFIDENCE = {
    letter: LETTER_MIN_CONFIDENCE
    for letter in FILTERED_LETTER_CLASSES
}

# Parentheses in patent labels can look like a low-confidence 6 or 7 after
# cropping.  Approved real 6/7 samples are consistently above this threshold.
OCR_CHARACTER_MIN_CONFIDENCE = {
    **LETTER_CHARACTER_MIN_CONFIDENCE,
    "6": 0.80,
    "7": 0.80,
    "J": 0.90,
}

# V3 learned real 6/7 hard negatives and no longer needs the legacy 0.80
# suppression that caused valid digits to be discarded. Ordinary alphabetic
# labels use 0.20, Roman I/V/X retain 0.05, and J remains high-risk.
V3_CHARACTER_MIN_CONFIDENCE = {
    **LETTER_CHARACTER_MIN_CONFIDENCE,
    "J": 0.90,
}

# Group-level EasyOCR can mistake drawing strokes for a standalone J.
# Real J labels remain available when OCR is confident enough.
OCR_LABEL_CHARACTER_MIN_CONFIDENCE = {
    "J": 0.90,
}

# A valid J should be approximately as tall as numeric labels on the same page.
# Keep 10% tolerance for detector-box variation.
J_MIN_DIGIT_HEIGHT_RATIO = 0.90

# 標號組合參數
Y_TOLERANCE = 15
MAX_X_GAP = 15
MAX_LABEL_LENGTH = 8

# 辨識模式：
# "auto"       = 自動判斷模型是否可直接輸出字元
# "easyocr"    = 強制使用舊模式：YOLO 抓框 + EasyOCR 讀數字
# "yolo_char"  = 強制使用新版模式：直接讀 YOLO class name
RECOGNITION_MODE = "auto"

# PDF 轉圖解析度
PDF_DPI = 300

# 圖片預覽最大放大倍率
MAX_IMAGE_PREVIEW_ZOOM = 2.0
