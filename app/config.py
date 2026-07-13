APP_NAME = "聖島專利 - Patent Utility Suite v1.05"

# YOLO / OCR 參數
YOLO_CONF = 0.25
YOLO_IOU = 0.40
OCR_CONF = 0.20
IMG_SIZE = 1536
PAD = 6
OCR_ALLOWLIST = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'"

# Parentheses in patent labels can look like a low-confidence 6 or 7 after
# cropping.  Approved real 6/7 samples are consistently above this threshold.
OCR_CHARACTER_MIN_CONFIDENCE = {
    "6": 0.80,
    "7": 0.80,
}

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
