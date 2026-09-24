"""Build a TIPO-checker-compatible, drawing-free patent DOCX test fixture.

The document mimics the dense A4 structure seen in Saint-Island represented
Taiwan utility-model gazettes, but all technical prose is synthetic.  It is
safe to use for parser development and must not be treated as a real filing.
"""

from argparse import ArgumentParser
from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION_START
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor


SYSTEM_NAME = "Saint-Island_Patent_MDS"
DEFAULT_OUTPUT = (
    Path(__file__).resolve().parents[1]
    / "output"
    / "patent_review_stage1"
    / "synthetic_reference"
    / "Saint-Island_Patent_MDS_合成專利樣本_TIPO相容測試版_無圖式.docx"
)

FONT_NAME = "PMingLiU"
BODY_SIZE = 12


def add_patent_paragraph_numbering(document):
    """Create real Word numbering rendered as 〖0001〗, 〖0002〗, ... .

    The TIPO checker converts DOCX to HTML with Mammoth and only accepts an
    actual ``<ol>`` immediately after several section headings.  Plain text
    that merely looks like a paragraph number is therefore insufficient.
    """

    numbering = document.part.numbering_part.element
    abstract_ids = [
        int(node.get(qn("w:abstractNumId")))
        for node in numbering.findall(qn("w:abstractNum"))
        if node.get(qn("w:abstractNumId"), "").isdigit()
    ]
    num_ids = [
        int(node.get(qn("w:numId")))
        for node in numbering.findall(qn("w:num"))
        if node.get(qn("w:numId"), "").isdigit()
    ]
    abstract_num_id = max(abstract_ids, default=0) + 1
    num_id = max(num_ids, default=0) + 1

    abstract_num = OxmlElement("w:abstractNum")
    abstract_num.set(qn("w:abstractNumId"), str(abstract_num_id))
    multi_level = OxmlElement("w:multiLevelType")
    multi_level.set(qn("w:val"), "singleLevel")
    abstract_num.append(multi_level)

    level = OxmlElement("w:lvl")
    level.set(qn("w:ilvl"), "0")
    start = OxmlElement("w:start")
    start.set(qn("w:val"), "1")
    number_format = OxmlElement("w:numFmt")
    number_format.set(qn("w:val"), "decimalZero")
    level_text = OxmlElement("w:lvlText")
    level_text.set(qn("w:val"), "〖%1〗")
    level_justification = OxmlElement("w:lvlJc")
    level_justification.set(qn("w:val"), "left")
    level.extend([start, number_format, level_text, level_justification])

    paragraph_properties = OxmlElement("w:pPr")
    tabs = OxmlElement("w:tabs")
    tab = OxmlElement("w:tab")
    tab.set(qn("w:val"), "num")
    tab.set(qn("w:pos"), "900")
    tabs.append(tab)
    indent = OxmlElement("w:ind")
    indent.set(qn("w:left"), "900")
    indent.set(qn("w:hanging"), "900")
    paragraph_properties.extend([tabs, indent])
    level.append(paragraph_properties)

    run_properties = OxmlElement("w:rPr")
    fonts = OxmlElement("w:rFonts")
    fonts.set(qn("w:ascii"), FONT_NAME)
    fonts.set(qn("w:hAnsi"), FONT_NAME)
    fonts.set(qn("w:eastAsia"), FONT_NAME)
    run_properties.append(fonts)
    level.append(run_properties)
    abstract_num.append(level)
    numbering.append(abstract_num)

    num = OxmlElement("w:num")
    num.set(qn("w:numId"), str(num_id))
    abstract_num_reference = OxmlElement("w:abstractNumId")
    abstract_num_reference.set(qn("w:val"), str(abstract_num_id))
    num.append(abstract_num_reference)
    numbering.append(num)
    return num_id


def apply_numbering(paragraph, num_id):
    paragraph_properties = paragraph._p.get_or_add_pPr()
    number_properties = paragraph_properties.find(qn("w:numPr"))
    if number_properties is None:
        number_properties = OxmlElement("w:numPr")
        paragraph_properties.insert(0, number_properties)
    level = OxmlElement("w:ilvl")
    level.set(qn("w:val"), "0")
    number_id = OxmlElement("w:numId")
    number_id.set(qn("w:val"), str(num_id))
    number_properties.extend([level, number_id])


def set_run_font(run, size=BODY_SIZE, bold=None, italic=None, color=None):
    run.font.name = FONT_NAME
    run.font.size = Pt(size)
    run._element.get_or_add_rPr().rFonts.set(qn("w:ascii"), FONT_NAME)
    run._element.get_or_add_rPr().rFonts.set(qn("w:hAnsi"), FONT_NAME)
    run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), FONT_NAME)
    if bold is not None:
        run.bold = bold
    if italic is not None:
        run.italic = italic
    if color is not None:
        run.font.color.rgb = (
            RGBColor.from_string(color) if isinstance(color, str) else color
        )


def set_cell_margins(cell, top=80, start=120, bottom=80, end=120):
    tc = cell._tc
    tc_pr = tc.get_or_add_tcPr()
    tc_mar = tc_pr.first_child_found_in("w:tcMar")
    if tc_mar is None:
        tc_mar = OxmlElement("w:tcMar")
        tc_pr.append(tc_mar)
    for margin, value in (("top", top), ("start", start), ("bottom", bottom), ("end", end)):
        node = tc_mar.find(qn(f"w:{margin}"))
        if node is None:
            node = OxmlElement(f"w:{margin}")
            tc_mar.append(node)
        node.set(qn("w:w"), str(value))
        node.set(qn("w:type"), "dxa")


def add_page_field(paragraph):
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = paragraph.add_run("第 ")
    set_run_font(run, size=10)
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instruction = OxmlElement("w:instrText")
    instruction.set(qn("xml:space"), "preserve")
    instruction.text = " PAGE "
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    value = OxmlElement("w:t")
    value.text = "1"
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    field_run = paragraph.add_run()
    field_run._r.extend([begin, instruction, separate, value, end])
    set_run_font(field_run, size=10)
    run = paragraph.add_run(" 頁")
    set_run_font(run, size=10)


def configure_document(document):
    section = document.sections[0]
    section.start_type = WD_SECTION_START.NEW_PAGE
    section.page_width = Cm(21)
    section.page_height = Cm(29.7)
    section.top_margin = Cm(2.2)
    section.bottom_margin = Cm(2.2)
    section.left_margin = Cm(2.2)
    section.right_margin = Cm(2.2)
    section.header_distance = Cm(1.0)
    section.footer_distance = Cm(1.0)

    normal = document.styles["Normal"]
    normal.font.name = FONT_NAME
    normal.font.size = Pt(BODY_SIZE)
    normal._element.rPr.rFonts.set(qn("w:ascii"), FONT_NAME)
    normal._element.rPr.rFonts.set(qn("w:hAnsi"), FONT_NAME)
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), FONT_NAME)
    normal.paragraph_format.space_before = Pt(0)
    normal.paragraph_format.space_after = Pt(0)
    normal.paragraph_format.line_spacing = 1.5
    normal.paragraph_format.widow_control = True

    heading = document.styles.add_style("PatentSectionHeading", WD_STYLE_TYPE.PARAGRAPH)
    heading.base_style = normal
    heading.font.name = FONT_NAME
    heading.font.size = Pt(14)
    heading.font.bold = True
    heading._element.rPr.rFonts.set(qn("w:eastAsia"), FONT_NAME)
    heading.paragraph_format.space_before = Pt(10)
    heading.paragraph_format.space_after = Pt(3)
    heading.paragraph_format.line_spacing = 1.25
    heading.paragraph_format.keep_with_next = True

    body = document.styles.add_style("PatentBody", WD_STYLE_TYPE.PARAGRAPH)
    body.base_style = normal
    body.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    body.paragraph_format.line_spacing = 1.5
    body.paragraph_format.space_after = Pt(0)

    claim = document.styles.add_style("PatentClaim", WD_STYLE_TYPE.PARAGRAPH)
    claim.base_style = body
    claim.paragraph_format.left_indent = Cm(0.9)
    claim.paragraph_format.first_line_indent = Cm(-0.9)
    claim.paragraph_format.space_after = Pt(4)

    symbol = document.styles.add_style("PatentSymbol", WD_STYLE_TYPE.PARAGRAPH)
    symbol.base_style = body
    symbol.paragraph_format.left_indent = Cm(1.5)
    symbol.paragraph_format.first_line_indent = Cm(-1.5)

    header = section.header.paragraphs[0]
    header.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    header_run = header.add_run(f"{SYSTEM_NAME} 合成測試文件｜非正式申請")
    set_run_font(header_run, size=9, color="777777")
    header.paragraph_format.space_after = Pt(0)

    add_page_field(section.footer.paragraphs[0])

    settings = document.settings._element
    update_fields = OxmlElement("w:updateFields")
    update_fields.set(qn("w:val"), "true")
    settings.append(update_fields)


def add_centered_title(document, text, size=16, after=10):
    paragraph = document.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    paragraph.paragraph_format.space_after = Pt(after)
    run = paragraph.add_run(text)
    set_run_font(run, size=size, bold=True)
    return paragraph


def add_section_heading(document, text, page_break=False):
    paragraph = document.add_paragraph(style="PatentSectionHeading")
    if page_break:
        paragraph.paragraph_format.page_break_before = True
    run = paragraph.add_run(f"【{text}】")
    set_run_font(run, size=14, bold=True)
    return paragraph


def add_labeled_value(document, label, value, page_break=False):
    paragraph = document.add_paragraph(style="PatentSectionHeading")
    if page_break:
        paragraph.paragraph_format.page_break_before = True
    label_run = paragraph.add_run(f"【{label}】")
    set_run_font(label_run, size=14, bold=True)
    value_run = paragraph.add_run(value)
    set_run_font(value_run, size=14, bold=False)
    return paragraph


def add_body(document, number, text, num_id):
    paragraph = document.add_paragraph(style="PatentBody")
    apply_numbering(paragraph, num_id)
    text_run = paragraph.add_run(text)
    set_run_font(text_run)
    return paragraph


def add_plain_body(document, text):
    paragraph = document.add_paragraph(style="PatentBody")
    run = paragraph.add_run(text)
    set_run_font(run)
    return paragraph


def add_claim(document, number, text):
    paragraph = document.add_paragraph(style="PatentClaim")
    run = paragraph.add_run(f"【請求項{number}】{text}")
    set_run_font(run)
    return paragraph


def add_symbol(document, symbol, name):
    paragraph = document.add_paragraph(style="PatentSymbol")
    run = paragraph.add_run(f"{symbol}:{name}")
    set_run_font(run)
    return paragraph


def build_document(output_path: Path) -> Path:
    document = Document()
    configure_document(document)
    paragraph_num_id = add_patent_paragraph_numbering(document)
    document.core_properties.title = "模組化收納裝置－合成專利測試文件"
    document.core_properties.subject = "Saint-Island_Patent_MDS DOCX parser fixture"
    document.core_properties.author = SYSTEM_NAME
    document.core_properties.comments = (
        "依智財局公開填表規則與輔助偵錯系統解析規則建立；內容完全合成；不含圖式；不得作為正式申請文件。"
    )

    # Major part 1: abstract.  This follows the submission-document structure,
    # not the later gazette cover layout.
    add_centered_title(document, "新型摘要", size=18, after=12)
    add_labeled_value(document, "中文新型名稱", "模組化收納裝置")
    add_labeled_value(document, "英文新型名稱", "MODULAR STORAGE DEVICE")
    add_section_heading(document, "中文摘要")
    add_plain_body(
        document,
        "本新型提供一種模組化收納裝置，包含主箱體、分隔模組、上蓋及識別模組。主箱體具有數個定位孔，分隔模組的彈性扣件可選擇地卡入定位孔，使分隔板能不使用工具而調整位置。識別模組具有可抽換的標示片，以利辨識不同收納區域。本新型可提升收納配置彈性及內容物辨識效率。",
    )
    add_labeled_value(document, "指定代表圖", ":圖1")
    add_section_heading(document, "代表圖之符號簡單說明")
    for symbol, name in (
        ("1", "模組化收納裝置"),
        ("10", "主箱體"),
        ("20", "分隔模組"),
        ("30", "上蓋"),
        ("40", "識別模組"),
    ):
        add_symbol(document, symbol, name)

    # Major part 2: complete specification body.
    add_centered_title(document, "新型專利說明書", size=18, after=14)
    document.paragraphs[-1].paragraph_format.page_break_before = True
    add_labeled_value(document, "新型名稱", "模組化收納裝置")

    add_section_heading(document, "技術領域")
    add_body(
        document,
        1,
        "本新型涉及一種收納設備，特別是涉及一種可依物品尺寸調整內部分隔位置，並可供使用者快速辨識所收納物品的模組化收納裝置。",
        paragraph_num_id,
    )

    add_section_heading(document, "先前技術")
    add_body(
        document,
        2,
        "習知收納箱通常具有固定的容置空間。當不同尺寸的物品共同放置時，物品容易在搬運過程中相互碰撞，使用者亦不易迅速確認各物品的位置。",
        paragraph_num_id,
    )
    add_body(
        document,
        3,
        "部分收納箱雖設有活動隔板，但隔板常需要額外工具鎖固，且調整後缺乏可重複定位的結構。因此，如何兼顧分隔位置調整、定位穩定性及內容物辨識便利性，仍有改善空間。",
        paragraph_num_id,
    )

    add_section_heading(document, "新型內容")
    add_body(
        document,
        4,
        "本新型之目的，在於提供一種不需工具即可調整分隔位置，且能以可替換標示片識別收納區域的模組化收納裝置。",
        paragraph_num_id,
    )
    add_body(
        document,
        5,
        "本新型模組化收納裝置包含一主箱體、一分隔模組、一上蓋及一識別模組。該主箱體包括一底板、數個側板及數個沿排列方向間隔設置的定位孔。",
        paragraph_num_id,
    )
    add_body(
        document,
        6,
        "該分隔模組包括至少一分隔板、二形成於該分隔板相反側的滑接部，及二分別設於所述滑接部的彈性扣件。所述滑接部可沿該等側板滑動，所述彈性扣件可選擇地卡入對應的定位孔。",
        paragraph_num_id,
    )
    add_body(
        document,
        7,
        "該上蓋包括一樞接部及一卡合部，該樞接部連接其中一側板，該卡合部可分離地扣合相對的另一側板。該識別模組包括一標示片及一設於該上蓋外側的插槽，該標示片可抽換地插設於該插槽。",
        paragraph_num_id,
    )
    add_body(
        document,
        8,
        "藉由上述構造，使用者可按壓彈性扣件以釋放分隔板，將分隔板移至適當位置後再使彈性扣件卡入定位孔，因而快速改變收納空間的配置。",
        paragraph_num_id,
    )

    add_section_heading(document, "圖式簡單說明")
    add_body(
        document,
        9,
        "圖1為本新型模組化收納裝置的一立體示意圖。\n"
        "圖2為圖1之分解立體示意圖。\n"
        "圖3為分隔模組與主箱體的局部剖視示意圖。\n"
        "圖4為分隔板調整至另一定位位置的使用狀態示意圖。\n"
        "圖5為識別模組的局部放大示意圖。",
        paragraph_num_id,
    )

    add_section_heading(document, "實施方式")
    add_body(
        document,
        14,
        "參閱圖1及圖2，本實施例之模組化收納裝置1包含一主箱體10、一分隔模組20、一上蓋30及一識別模組40。",
        paragraph_num_id,
    )
    add_body(
        document,
        15,
        "該主箱體10大致呈矩形，包括一底板11、四個自該底板11周緣向上延伸的側板12，及數個形成於其中二相對側板12內側面的定位孔13。所述定位孔13沿一排列方向等距分布。",
        paragraph_num_id,
    )
    add_body(
        document,
        16,
        "參閱圖2及圖3，該分隔模組20包括一分隔板21、二滑接部22及二彈性扣件23。所述滑接部22分別形成於該分隔板21的相反側，並與所述側板12形成可滑動配合。",
        paragraph_num_id,
    )
    add_body(
        document,
        17,
        "每一彈性扣件23包括一可受壓位移的按壓段及一朝對應側板12突伸的定位凸部。當按壓段未受力時，定位凸部卡入其中一定位孔13，使該分隔板21保持在選定位置。",
        paragraph_num_id,
    )
    add_body(
        document,
        18,
        "欲調整收納空間時，使用者同時按壓所述彈性扣件23，使定位凸部離開定位孔13，再沿排列方向推移該分隔板21。到達另一位置後，釋放彈性扣件23即可重新定位。",
        paragraph_num_id,
    )
    add_body(
        document,
        19,
        "參閱圖1，該上蓋30藉由該樞接部31可轉動地連接該主箱體10，並可藉由該卡合部32保持在關閉位置。該插槽42設於該上蓋30的外表面，並具有一供該標示片41抽換的開口。",
        paragraph_num_id,
    )
    add_body(
        document,
        20,
        "該標示片41可記載收納類別、編號或日期。當收納內容改變時，使用者只需更換標示片41，不需在主箱體10或上蓋30表面重複黏貼標籤。",
        paragraph_num_id,
    )
    add_body(
        document,
        21,
        "在其他實施態樣中，定位孔13可改為定位凹槽，彈性扣件23亦可設於主箱體10並與形成於分隔板21的卡合結構配合。凡未偏離本新型精神的等效變化，均應包含於本新型之範圍。",
        paragraph_num_id,
    )

    add_section_heading(document, "符號說明")
    for symbol, name in (
        ("1", "模組化收納裝置"),
        ("10", "主箱體"),
        ("11", "底板"),
        ("12", "側板"),
        ("13", "定位孔"),
        ("20", "分隔模組"),
        ("21", "分隔板"),
        ("22", "滑接部"),
        ("23", "彈性扣件"),
        ("30", "上蓋"),
        ("31", "樞接部"),
        ("32", "卡合部"),
        ("40", "識別模組"),
        ("41", "標示片"),
        ("42", "插槽"),
    ):
        add_symbol(document, symbol, name)

    # Major part 3: patent claims.
    add_section_heading(document, "申請專利範圍", page_break=True)
    claims = (
        "一種模組化收納裝置，包含：一主箱體，包括一底板、數個側板及數個間隔設置的定位孔；一分隔模組，包括至少一分隔板、二滑接部及二彈性扣件，所述滑接部可沿所述側板滑動，所述彈性扣件可選擇地卡入對應的該定位孔；一上蓋，包括一樞接部及一卡合部；及一識別模組，包括一標示片及一供該標示片插設的插槽。",
        "如請求項1所述的模組化收納裝置，其中，所述定位孔沿一排列方向等距設置於二相對的該側板。",
        "如請求項1所述的模組化收納裝置，其中，每一該彈性扣件包括一按壓段及一可卡入該定位孔的定位凸部。",
        "如請求項3所述的模組化收納裝置，其中，該按壓段受壓時帶動該定位凸部離開該定位孔。",
        "如請求項1所述的模組化收納裝置，其中，該樞接部可轉動地連接其中一該側板，該卡合部可分離地扣合另一該側板。",
        "如請求項1所述的模組化收納裝置，其中，該插槽設於該上蓋的外表面，並具有一供該標示片抽換的開口。",
        "如請求項1至6中任一項所述的模組化收納裝置，其中，該主箱體包括至少二個該分隔模組。",
        "如請求項1所述的模組化收納裝置，其中，該底板的內表面設有一止滑層。",
    )
    for index, claim_text in enumerate(claims, start=1):
        add_claim(document, index, claim_text)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    document.save(output_path)
    return output_path


def main() -> int:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    output = build_document(args.output.resolve())
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
