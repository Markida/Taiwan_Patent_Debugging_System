"""Build the screenshot-led Traditional Chinese Saint-Island user guide."""

from __future__ import annotations

import json
import math
from pathlib import Path

from reportlab.lib.colors import Color, HexColor, white
from reportlab.lib.pagesizes import A3, A4, landscape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCREENSHOT_DIR = PROJECT_ROOT / "tmp" / "user_guide" / "screenshots"
OUTPUT_PATH = (
    PROJECT_ROOT
    / "output"
    / "pdf"
    / "Saint-Island_Patent_OCR_圖文使用說明_v1.06.3.pdf"
)

FONT_REGULAR = "JhengHei"
FONT_BOLD = "JhengHeiBold"
NAVY = HexColor("#0f2742")
BLUE = HexColor("#2563eb")
DEEP_BLUE = HexColor("#0f4c81")
RED = HexColor("#dc2626")
GREEN = HexColor("#16a34a")
AMBER = HexColor("#d97706")
SLATE = HexColor("#475569")
LIGHT_BLUE = HexColor("#eff6ff")
PALE = HexColor("#f8fafc")
BORDER = HexColor("#cbd5e1")


def register_fonts():
    pdfmetrics.registerFont(
        TTFont(
            FONT_REGULAR,
            "C:/Windows/Fonts/msjh.ttc",
            subfontIndex=0,
        )
    )
    pdfmetrics.registerFont(
        TTFont(
            FONT_BOLD,
            "C:/Windows/Fonts/msjhbd.ttc",
            subfontIndex=0,
        )
    )


def wrap_text(text, font_name, font_size, max_width):
    lines = []
    for paragraph in str(text).split("\n"):
        if not paragraph:
            lines.append("")
            continue
        current = ""
        for character in paragraph:
            candidate = current + character
            if current and pdfmetrics.stringWidth(candidate, font_name, font_size) > max_width:
                lines.append(current)
                current = character
            else:
                current = candidate
        if current:
            lines.append(current)
    return lines


def draw_page_header(pdf, title, subtitle, page_width, page_height):
    pdf.setFillColor(NAVY)
    pdf.setFont(FONT_BOLD, 22)
    pdf.drawString(24, page_height - 32, title)
    pdf.setFillColor(SLATE)
    pdf.setFont(FONT_REGULAR, 10)
    pdf.drawRightString(page_width - 24, page_height - 29, subtitle)
    pdf.setStrokeColor(HexColor("#b8c7d9"))
    pdf.setLineWidth(0.8)
    pdf.line(24, page_height - 41, page_width - 24, page_height - 41)


def draw_footer(pdf, page_number, total_pages, page_width):
    pdf.setFillColor(SLATE)
    pdf.setFont(FONT_REGULAR, 8)
    pdf.drawString(24, 15, "Saint-Island Patent OCR 圖文使用說明")
    pdf.drawRightString(page_width - 24, 15, f"{page_number} / {total_pages}")


class ScreenshotPlacement:
    def __init__(self, pdf, image_path, x, y, width, height, source_width=1600, source_height=950):
        self.pdf = pdf
        self.image_path = str(image_path)
        self.x = float(x)
        self.y = float(y)
        self.width = float(width)
        self.height = float(height)
        self.source_width = float(source_width)
        self.source_height = float(source_height)
        pdf.drawImage(
            self.image_path,
            self.x,
            self.y,
            width=self.width,
            height=self.height,
            preserveAspectRatio=False,
            mask="auto",
        )
        pdf.setStrokeColor(BORDER)
        pdf.setLineWidth(0.8)
        pdf.rect(self.x, self.y, self.width, self.height, stroke=1, fill=0)

    def point(self, source_x, source_y):
        return (
            self.x + float(source_x) * self.width / self.source_width,
            self.y + self.height - float(source_y) * self.height / self.source_height,
        )

    def rect(self, source_rect):
        sx, sy, sw, sh = source_rect
        x = self.x + sx * self.width / self.source_width
        y = self.y + self.height - (sy + sh) * self.height / self.source_height
        return (
            x,
            y,
            sw * self.width / self.source_width,
            sh * self.height / self.source_height,
        )


def draw_highlight(pdf, rect, color=BLUE, fill_alpha=0.12, line_width=2.0, radius=5):
    x, y, width, height = rect
    pdf.saveState()
    pdf.setStrokeColor(color)
    pdf.setFillColor(color)
    pdf.setLineWidth(line_width)
    if hasattr(pdf, "setFillAlpha"):
        pdf.setFillAlpha(fill_alpha)
        pdf.setStrokeAlpha(0.95)
    pdf.roundRect(x, y, width, height, radius, stroke=1, fill=1)
    pdf.restoreState()


def draw_arrow(pdf, start, end, color=DEEP_BLUE, line_width=1.7, head=7):
    x1, y1 = start
    x2, y2 = end
    pdf.saveState()
    pdf.setStrokeColor(color)
    pdf.setFillColor(color)
    pdf.setLineWidth(line_width)
    pdf.line(x1, y1, x2, y2)
    angle = math.atan2(y2 - y1, x2 - x1)
    left = (
        x2 - head * math.cos(angle - math.pi / 6),
        y2 - head * math.sin(angle - math.pi / 6),
    )
    right = (
        x2 - head * math.cos(angle + math.pi / 6),
        y2 - head * math.sin(angle + math.pi / 6),
    )
    path = pdf.beginPath()
    path.moveTo(x2, y2)
    path.lineTo(*left)
    path.lineTo(*right)
    path.close()
    pdf.drawPath(path, stroke=0, fill=1)
    pdf.restoreState()


def nearest_box_edge(box, target):
    x, y, width, height = box
    tx, ty = target
    candidates = [
        (max(x, min(tx, x + width)), y),
        (max(x, min(tx, x + width)), y + height),
        (x, max(y, min(ty, y + height))),
        (x + width, max(y, min(ty, y + height))),
    ]
    return min(candidates, key=lambda point: (point[0] - tx) ** 2 + (point[1] - ty) ** 2)


def draw_callout_box(pdf, number, text, box, color=DEEP_BLUE, font_size=10.5):
    x, y, width, height = box
    pdf.saveState()
    pdf.setFillColor(white)
    pdf.setStrokeColor(color)
    pdf.setLineWidth(1.2)
    if hasattr(pdf, "setFillAlpha"):
        pdf.setFillAlpha(0.94)
    pdf.roundRect(x, y, width, height, 7, stroke=1, fill=1)
    pdf.restoreState()

    circle_x = x + 15
    circle_y = y + height - 15
    pdf.setFillColor(color)
    pdf.circle(circle_x, circle_y, 10, stroke=0, fill=1)
    pdf.setFillColor(white)
    pdf.setFont(FONT_BOLD, 9)
    pdf.drawCentredString(circle_x, circle_y - 3.2, str(number))

    text_x = x + 30
    available = width - 38
    lines = wrap_text(text, FONT_BOLD, font_size, available)
    line_height = font_size * 1.32
    total_height = len(lines) * line_height
    baseline = y + (height + total_height) / 2 - line_height + 2
    pdf.setFillColor(NAVY)
    pdf.setFont(FONT_BOLD, font_size)
    for line in lines:
        pdf.drawString(text_x, baseline, line)
        baseline -= line_height
    return box


def draw_callout(pdf, placement, number, text, source_rect, box, color=DEEP_BLUE, fill_alpha=0.12):
    target_rect = placement.rect(source_rect)
    draw_highlight(pdf, target_rect, color=color, fill_alpha=fill_alpha)
    target = (target_rect[0] + target_rect[2] / 2, target_rect[1] + target_rect[3] / 2)
    draw_callout_box(pdf, number, text, box, color=color)
    draw_arrow(pdf, nearest_box_edge(box, target), target, color=color)


def draw_sequence_strip(pdf, labels, x, y, total_width, height=31):
    gap = 8
    box_width = (total_width - gap * (len(labels) - 1)) / len(labels)
    for index, label in enumerate(labels, start=1):
        box_x = x + (index - 1) * (box_width + gap)
        pdf.setFillColor(LIGHT_BLUE if index < len(labels) else HexColor("#dcfce7"))
        pdf.setStrokeColor(BLUE if index < len(labels) else GREEN)
        pdf.roundRect(box_x, y, box_width, height, 8, stroke=1, fill=1)
        pdf.setFillColor(NAVY)
        pdf.setFont(FONT_BOLD, 9.5)
        pdf.drawCentredString(box_x + box_width / 2, y + 10, f"{index}  {label}")


def draw_number_marker(pdf, placement, number, source_rect, color=DEEP_BLUE):
    x, y, width, height = placement.rect(source_rect)
    target = (x + width / 2, y + height / 2)
    marker = (x + min(9, width * 0.18), y + height + 9)
    pdf.setFillColor(color)
    pdf.circle(marker[0], marker[1], 9, stroke=0, fill=1)
    pdf.setFillColor(white)
    pdf.setFont(FONT_BOLD, 8)
    pdf.drawCentredString(marker[0], marker[1] - 2.8, str(number))
    draw_arrow(pdf, (marker[0], marker[1] - 9), target, color=color, line_width=1.25, head=5)


def draw_legend_item(pdf, number, text, x, y, width, height=27):
    pdf.setFillColor(PALE)
    pdf.setStrokeColor(BORDER)
    pdf.roundRect(x, y, width, height, 6, stroke=1, fill=1)
    pdf.setFillColor(DEEP_BLUE)
    pdf.circle(x + 13, y + height / 2, 8, stroke=0, fill=1)
    pdf.setFillColor(white)
    pdf.setFont(FONT_BOLD, 7.5)
    pdf.drawCentredString(x + 13, y + height / 2 - 2.5, str(number))
    pdf.setFillColor(NAVY)
    pdf.setFont(FONT_BOLD, 8.3)
    pdf.drawString(x + 25, y + height / 2 - 3, text)


def draw_cropped_image(pdf, image_path, source_crop, target_rect, source_size=(1600, 950)):
    sx, sy, sw, sh = source_crop
    x, y, width, height = target_rect
    source_width, source_height = source_size
    scale = min(width / sw, height / sh)
    actual_width = sw * scale
    actual_height = sh * scale
    offset_x = x + (width - actual_width) / 2
    offset_y = y + (height - actual_height) / 2

    pdf.saveState()
    clip = pdf.beginPath()
    clip.rect(offset_x, offset_y, actual_width, actual_height)
    pdf.clipPath(clip, stroke=0, fill=0)
    draw_x = offset_x - sx * scale
    draw_y = offset_y + actual_height - (source_height - sy) * scale
    pdf.drawImage(
        str(image_path),
        draw_x,
        draw_y,
        width=source_width * scale,
        height=source_height * scale,
        preserveAspectRatio=False,
        mask="auto",
    )
    pdf.restoreState()
    pdf.setStrokeColor(BORDER)
    pdf.rect(offset_x, offset_y, actual_width, actual_height, stroke=1, fill=0)
    return (offset_x, offset_y, actual_width, actual_height)


def build_pdf():
    register_fonts()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    geometry = json.loads((SCREENSHOT_DIR / "geometry.json").read_text(encoding="utf-8"))
    button_rects = {name: value["rect"] for name, value in geometry["buttons"].items()}
    fields = geometry["fields"]
    total_pages = 8

    pdf = canvas.Canvas(str(OUTPUT_PATH), pagesize=landscape(A4), pageCompression=1)
    pdf.setTitle("Saint-Island Patent OCR 圖文使用說明")
    pdf.setAuthor("Saint-Island")
    pdf.setCreator("Saint-Island Patent OCR")
    pdf.setSubject("圖片與 PDF 匯入、辨識、修正及清單比對操作流程")

    # Page 1 - entry and workflow.
    page_width, page_height = landscape(A4)
    draw_page_header(
        pdf,
        "Saint-Island Patent OCR 圖文使用說明",
        "v1.06.3｜圖片 / PDF → 辨識 → 修正 → 比對",
        page_width,
        page_height,
    )
    home = ScreenshotPlacement(
        pdf,
        SCREENSHOT_DIR / "01_home.png",
        35,
        72,
        page_width - 70,
        (page_width - 70) * 950 / 1600,
    )
    home_button = geometry["home_buttons"]["圖片標號識別"]["rect"]
    draw_callout(
        pdf,
        home,
        1,
        "點選「圖片標號識別」進入操作畫面",
        home_button,
        (510, 246, 275, 48),
    )
    draw_sequence_strip(
        pdf,
        ["匯入", "開始辨識", "修正標號", "輸入清單", "查看比對"],
        60,
        33,
        page_width - 120,
        height=29,
    )
    draw_footer(pdf, 1, total_pages, page_width)
    pdf.showPage()

    # Page 2 - one-page all-button overview on A3 landscape.
    page_width, page_height = landscape(A3)
    pdf.setPageSize((page_width, page_height))
    draw_page_header(
        pdf,
        "全部按鍵功能總覽",
        "藍色圓點編號對應下方功能說明",
        page_width,
        page_height,
    )
    overview = ScreenshotPlacement(
        pdf,
        SCREENSHOT_DIR / "04_recognition_complete.png",
        55,
        125,
        1080,
        1080 * 950 / 1600,
    )
    overview_items = [
        (1, "回首頁", button_rects["← 回首頁"]),
        (2, "選擇模型檔", button_rects["選擇模型"]),
        (3, "選擇多張圖片", button_rects["選擇多張圖片"]),
        (4, "選擇 PDF", button_rects["選擇 PDF"]),
        (5, "上一張", button_rects["上一張"]),
        (6, "下一張", button_rects["下一張"]),
        (7, "右旋 90°", button_rects["右旋 90°"]),
        (8, "顯示 / 隱藏辨識框", button_rects["隱藏一般辨識框"]),
        (9, "傳送錯誤回報至區域網路", button_rects["匯出錯誤回報"]),
        (10, "第一步：開始辨識", button_rects["第一步.圖片數字英文辨識"]),
        (11, "第二步：執行比對", button_rects["第二步.辨識結果與標號清單比對"]),
        (12, "新增目前圖片標號", button_rects["新增目前圖片標號"]),
        (13, "刪除選取標號", button_rects["刪除選取標號"]),
        (14, "全部 / 目前圖片結果", [1465, 629, 108, 30]),
        (15, "調整低信心門檻", fields["confidence_threshold"]),
    ]
    for number, _text, rect in overview_items:
        draw_number_marker(pdf, overview, number, rect)
    columns = 5
    legend_width = (page_width - 50 - 8 * (columns - 1)) / columns
    for index, (number, text, _rect) in enumerate(overview_items):
        row = 2 - index // columns
        column = index % columns
        x = 25 + column * (legend_width + 8)
        y = 27 + row * 35
        draw_legend_item(pdf, number, text, x, y, legend_width, 28)
    pdf.setFillColor(SLATE)
    pdf.setFont(FONT_REGULAR, 8.5)
    pdf.drawString(25, 15, "提示：按鍵呈灰色時，代表必須先完成前一階段。")
    pdf.drawRightString(page_width - 25, 15, "2 / 8")
    pdf.showPage()

    # Page 3 - import.
    page_width, page_height = landscape(A4)
    pdf.setPageSize((page_width, page_height))
    draw_page_header(
        pdf,
        "步驟 1｜選擇模型並輸入圖片或 PDF",
        "可一次選擇多張圖片，也可直接匯入多頁 PDF",
        page_width,
        page_height,
    )
    imported = ScreenshotPlacement(
        pdf,
        SCREENSHOT_DIR / "03_pdf_imported.png",
        22,
        38,
        page_width - 44,
        (page_width - 44) * 950 / 1600,
    )
    draw_callout(pdf, imported, 1, "確認或選擇 YOLO 模型", [59, 48, 1527, 34], (42, 456, 190, 42))
    draw_callout(pdf, imported, 2, "選擇多張圖片或選擇 PDF", [59, 88, 1527, 34], (540, 423, 245, 42))
    draw_callout(pdf, imported, 3, "用上一張、下一張逐頁確認", [85, 128, 144, 36], (55, 350, 220, 42))
    draw_callout(pdf, imported, 4, "方向不正確時按右旋 90°", [235, 128, 80, 36], (285, 350, 210, 42), color=AMBER)
    draw_footer(pdf, 3, total_pages, page_width)
    pdf.showPage()

    # Page 4 - recognition.
    draw_page_header(
        pdf,
        "步驟 2｜開始圖片數字英文辨識",
        "完成後先人工確認，不要立即進行第二階段",
        page_width,
        page_height,
    )
    recognized = ScreenshotPlacement(
        pdf,
        SCREENSHOT_DIR / "04_recognition_complete.png",
        22,
        38,
        page_width - 44,
        (page_width - 44) * 950 / 1600,
    )
    draw_callout(pdf, recognized, 1, "按第一步開始辨識", button_rects["第一步.圖片數字英文辨識"], (565, 454, 220, 42))
    draw_callout(pdf, recognized, 2, "綠框為一般結果；紅框為低信心", fields["image_preview"], (50, 70, 260, 46), color=GREEN, fill_alpha=0.035)
    draw_callout(pdf, recognized, 3, "低信心標號會自動置頂", fields["review_table"], (535, 340, 245, 43), color=RED, fill_alpha=0.07)
    draw_callout(pdf, recognized, 4, "右下先顯示各圖片辨識結果", fields["result_text"], (536, 76, 245, 43))
    draw_footer(pdf, 4, total_pages, page_width)
    pdf.showPage()

    # Page 5 - inspect low confidence.
    draw_page_header(
        pdf,
        "步驟 3｜檢查低信心與框選位置",
        "紅色代表需要確認；選取清單列後，圖片框會變為粗藍框",
        page_width,
        page_height,
    )
    selected = ScreenshotPlacement(
        pdf,
        SCREENSHOT_DIR / "05_low_confidence_selected.png",
        22,
        38,
        page_width - 44,
        (page_width - 44) * 950 / 1600,
    )
    draw_callout(pdf, selected, 1, "可調整低信心門檻；預設 0.70", fields["confidence_threshold"], (532, 456, 250, 42), color=RED)
    draw_callout(pdf, selected, 2, "先處理置頂的紅色標號列", [969, 244, 604, 30], (532, 351, 245, 42), color=RED)
    draw_callout(pdf, selected, 3, "選取標號列後，左圖對應框變粗藍", [500, 645, 55, 55], (55, 76, 270, 44), color=BLUE)
    draw_callout(pdf, selected, 4, "按此切換全部辨識框顯示", button_rects["隱藏一般辨識框"], (180, 420, 235, 42))
    draw_footer(pdf, 5, total_pages, page_width)
    pdf.showPage()

    # Page 6 - correct, add, delete, export.
    draw_page_header(
        pdf,
        "步驟 4｜修正、補上或刪除標號",
        "錯誤回報會直接傳送至公司區網；傳送失敗時保留本機備份",
        page_width,
        page_height,
    )
    corrected = ScreenshotPlacement(
        pdf,
        SCREENSHOT_DIR / "06_error_corrected.png",
        22,
        38,
        page_width - 44,
        (page_width - 44) * 950 / 1600,
    )
    draw_callout(pdf, corrected, 1, "雙擊標號欄，輸入正確標號", [969, 244, 604, 30], (530, 350, 250, 43), color=BLUE)
    draw_callout(pdf, corrected, 2, "缺漏用新增；誤判先選取再刪除", [969, 480, 244, 31], (515, 258, 270, 44), color=AMBER)
    draw_callout(pdf, corrected, 3, "確認後按此傳送錯誤回報，供後續訓練", button_rects["匯出錯誤回報"], (175, 420, 290, 43))
    draw_footer(pdf, 6, total_pages, page_width)
    pdf.showPage()

    # Page 7 - list and compare.
    draw_page_header(
        pdf,
        "步驟 5｜輸入標號清單並執行比對",
        "完成所有人工確認後，再進行第二步比對",
        page_width,
        page_height,
    )
    comparison = ScreenshotPlacement(
        pdf,
        SCREENSHOT_DIR / "07_comparison_all.png",
        22,
        38,
        page_width - 44,
        (page_width - 44) * 950 / 1600,
    )
    draw_callout(pdf, comparison, 1, "貼上清單；每行可用「標號:說明」", fields["reference_text"], (505, 260, 280, 43))
    draw_callout(pdf, comparison, 2, "按第二步執行清單比對", button_rects["第二步.辨識結果與標號清單比對"], (552, 420, 232, 43))
    draw_callout(pdf, comparison, 3, "先查看 All Pictures 的總差異", fields["result_text"], (505, 75, 280, 43), color=GREEN, fill_alpha=0.055)
    draw_callout(pdf, comparison, 4, "按此切換目前圖片結果", [1465, 629, 108, 30], (532, 207, 248, 42))
    draw_footer(pdf, 7, total_pages, page_width)
    pdf.showPage()

    # Page 8 - result interpretation, using vector-clipped screenshots.
    draw_page_header(
        pdf,
        "步驟 6｜查看全部圖片與目前圖片結果",
        "只保留差異項目，方便快速核對",
        page_width,
        page_height,
    )
    crop = (950, 520, 635, 410)
    left_target = (24, 250, 390, 252)
    right_target = (428, 250, 390, 252)
    left_crop = draw_cropped_image(
        pdf,
        SCREENSHOT_DIR / "07_comparison_all.png",
        crop,
        left_target,
    )
    right_crop = draw_cropped_image(
        pdf,
        SCREENSHOT_DIR / "08_comparison_current.png",
        crop,
        right_target,
    )
    pdf.setFillColor(DEEP_BLUE)
    pdf.setFont(FONT_BOLD, 13)
    pdf.drawCentredString(left_crop[0] + left_crop[2] / 2, 515, "全部圖片結果")
    pdf.drawCentredString(right_crop[0] + right_crop[2] / 2, 515, "目前圖片結果")
    draw_highlight(pdf, (left_crop[0] + 8, left_crop[1] + 8, left_crop[2] - 16, left_crop[3] * 0.61), color=GREEN, fill_alpha=0.04)
    draw_highlight(pdf, (right_crop[0] + 8, right_crop[1] + 8, right_crop[2] - 16, right_crop[3] * 0.61), color=BLUE, fill_alpha=0.04)
    draw_callout_box(
        pdf,
        1,
        "All Pictures：查看清單有但所有圖片沒有，以及任一圖片有但清單沒有的標號。",
        (30, 142, 375, 74),
        color=GREEN,
        font_size=10.5,
    )
    draw_arrow(pdf, (215, 216), (215, 250), color=GREEN)
    draw_callout_box(
        pdf,
        2,
        "目前圖片：只查看左側正在預覽的 Pic_01、Pic_02…差異；可再切回全部結果。",
        (437, 142, 375, 74),
        color=BLUE,
        font_size=10.5,
    )
    draw_arrow(pdf, (625, 216), (625, 250), color=BLUE)
    draw_sequence_strip(
        pdf,
        ["匯入完成", "辨識完成", "人工確認", "清單比對", "核對完成"],
        60,
        75,
        page_width - 120,
        height=32,
    )
    pdf.setFillColor(NAVY)
    pdf.setFont(FONT_BOLD, 11)
    pdf.drawCentredString(page_width / 2, 50, "完成後即可依差異結果回到原圖確認或修正清單。")
    draw_footer(pdf, 8, total_pages, page_width)
    pdf.showPage()

    pdf.save()
    return OUTPUT_PATH


if __name__ == "__main__":
    print(build_pdf())
