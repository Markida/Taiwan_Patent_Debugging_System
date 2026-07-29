"""Render a content-equivalent PDF preview from the synthetic patent DOCX.

This fallback is used when LibreOffice is unavailable and Microsoft Word COM
cannot complete a headless export.  It reads the generated DOCX paragraphs so
the preview content and ordering remain tied to the deliverable.
"""

from argparse import ArgumentParser
from html import escape
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer


SYSTEM_NAME = "Saint-Island_Patent_MDS"
FONT_NAME = "MingLiU"
FONT_BOLD = "MingLiU-Bold"


def _header_footer(canvas, document):
    canvas.saveState()
    canvas.setFont(FONT_NAME, 8.5)
    canvas.setFillColorRGB(0.42, 0.42, 0.42)
    canvas.drawRightString(A4[0] - 2.2 * cm, A4[1] - 1.15 * cm, f"{SYSTEM_NAME} 合成測試文件｜非正式申請")
    canvas.setFillColorRGB(0, 0, 0)
    canvas.drawCentredString(A4[0] / 2, 1.15 * cm, f"第 {canvas.getPageNumber()} 頁")
    canvas.restoreState()


def render_preview(docx_path: Path, pdf_path: Path) -> Path:
    pdfmetrics.registerFont(
        TTFont(FONT_NAME, r"C:\Windows\Fonts\mingliu.ttc", subfontIndex=0)
    )
    pdfmetrics.registerFont(
        TTFont(FONT_BOLD, r"C:\Windows\Fonts\msjhbd.ttc", subfontIndex=0)
    )
    pdfmetrics.registerFontFamily(
        FONT_NAME,
        normal=FONT_NAME,
        bold=FONT_BOLD,
        italic=FONT_NAME,
        boldItalic=FONT_BOLD,
    )
    source = Document(docx_path)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    styles = {
        "body": ParagraphStyle(
            "PatentBody",
            fontName=FONT_NAME,
            fontSize=12,
            leading=20,
            alignment=4,
            spaceAfter=0,
            splitLongWords=True,
            wordWrap="CJK",
        ),
        "heading": ParagraphStyle(
            "PatentSectionHeading",
            fontName=FONT_NAME,
            fontSize=14,
            leading=18,
            spaceBefore=10,
            spaceAfter=4,
            keepWithNext=True,
            wordWrap="CJK",
        ),
        "title": ParagraphStyle(
            "PatentTitle",
            fontName=FONT_NAME,
            fontSize=18,
            leading=23,
            alignment=1,
            spaceAfter=12,
            wordWrap="CJK",
        ),
        "center": ParagraphStyle(
            "PatentCentered",
            fontName=FONT_NAME,
            fontSize=13,
            leading=20,
            alignment=1,
            wordWrap="CJK",
        ),
        "claim": ParagraphStyle(
            "PatentClaim",
            parent=None,
            fontName=FONT_NAME,
            fontSize=12,
            leading=20,
            alignment=4,
            leftIndent=0.9 * cm,
            firstLineIndent=-0.9 * cm,
            spaceAfter=4,
            wordWrap="CJK",
        ),
        "symbol": ParagraphStyle(
            "PatentSymbol",
            fontName=FONT_NAME,
            fontSize=12,
            leading=20,
            leftIndent=1.5 * cm,
            firstLineIndent=-1.5 * cm,
            wordWrap="CJK",
        ),
    }
    story = []
    major_titles = {"新型摘要", "新型專利說明書", "申請專利範圍"}
    paragraph_number = 0
    for paragraph in source.paragraphs:
        text = paragraph.text.strip()
        if not text:
            story.append(Spacer(1, 5))
            continue
        # The claims already flow to a new page in this synthetic fixture.  A
        # second ReportLab page break at an automatic boundary creates a blank
        # preview page even though Word itself does not.
        if (
            paragraph.paragraph_format.page_break_before
            and "申請專利範圍" not in text
        ):
            story.append(PageBreak())
        num_pr = paragraph._p.get_or_add_pPr().find(qn("w:numPr"))
        if num_pr is not None and paragraph.style.name == "PatentBody":
            paragraph_number += 1
            text = f"〖{paragraph_number:04d}〗{text}"
        safe_text = escape(text).replace("\n", "<br/>")
        if text in major_titles:
            story.append(Paragraph(f"<b>{safe_text}</b>", styles["title"]))
        elif paragraph.style.name == "PatentSectionHeading":
            story.append(Paragraph(f"<b>{safe_text}</b>", styles["heading"]))
        elif paragraph.style.name == "PatentClaim":
            story.append(Paragraph(safe_text, styles["claim"]))
        elif paragraph.style.name == "PatentSymbol":
            story.append(Paragraph(safe_text, styles["symbol"]))
        elif paragraph.alignment == WD_ALIGN_PARAGRAPH.CENTER:
            story.append(Paragraph(safe_text, styles["center"]))
        else:
            story.append(Paragraph(safe_text, styles["body"]))

    output = SimpleDocTemplate(
        str(pdf_path),
        pagesize=A4,
        leftMargin=2.2 * cm,
        rightMargin=2.2 * cm,
        topMargin=2.2 * cm,
        bottomMargin=2.2 * cm,
        title="Saint-Island_Patent_MDS 合成專利樣本預覽",
        author=SYSTEM_NAME,
    )
    output.build(story, onFirstPage=_header_footer, onLaterPages=_header_footer)
    return pdf_path


def main() -> int:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("docx", type=Path)
    parser.add_argument("pdf", type=Path)
    args = parser.parse_args()
    print(render_preview(args.docx.resolve(), args.pdf.resolve()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
