"""Deterministic Stage 2 text checks with source-character localization."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import re
import unicodedata
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .custom_rules import (
    CUSTOM_RULE_BLACKLIST,
    CUSTOM_RULE_WHITELIST,
    CustomTextRule,
)
from .models import (
    PatentDocument,
    PatentIssue,
    PatentParagraph,
    PatentTextReview,
    RuleDefinition,
)


RULE_CATALOG: Tuple[RuleDefinition, ...] = (
    RuleDefinition(
        "STR001",
        "缺少必要章節",
        "structure",
        "error",
        "依專利類型確認名稱、摘要、說明書主要章節及申請專利範圍是否存在。",
    ),
    RuleDefinition(
        "STR002",
        "必要章節沒有內容",
        "structure",
        "error",
        "章節標題存在，但標題後沒有可供檢核的文字。",
    ),
    RuleDefinition(
        "STR003",
        "章節重複",
        "structure",
        "error",
        "固定章節只能在規定的大章節內出現一次。",
    ),
    RuleDefinition(
        "PNO001",
        "說明書段落缺少段號",
        "paragraph_numbering",
        "error",
        "說明書正文必須使用 Word 自動產生的連續段號。",
    ),
    RuleDefinition(
        "PNO002",
        "說明書段號格式異常",
        "paragraph_numbering",
        "error",
        "說明書段號可顯示為「【1】」或「【0001】」等一至四位數格式。",
    ),
    RuleDefinition(
        "PNO003",
        "說明書段號不連續",
        "paragraph_numbering",
        "error",
        "說明書正文段號應由1開始，依文件順序連續編排；是否補零不影響段號值。",
    ),
    RuleDefinition(
        "ORD001",
        "說明書章節順序異常",
        "structure",
        "error",
        "說明書主要章節通常應依技術領域、先前技術、發明／新型內容、圖式簡單說明、實施方式及符號說明排列。",
    ),
    RuleDefinition(
        "SYM001",
        "符號說明缺少內容",
        "symbols",
        "warning",
        "可辨識的符號項目必須具有元件名稱；符號的排版與分隔格式不檢查。",
    ),
    RuleDefinition(
        "SYM003",
        "相同符號對應不同名稱",
        "symbols",
        "error",
        "同一符號在同一份符號說明中不應對應互相衝突的元件名稱。",
    ),
    RuleDefinition(
        "SYM004",
        "代表圖符號未列入符號說明",
        "symbols",
        "error",
        "代表圖之符號簡單說明所列符號，應能在完整符號說明中找到。",
    ),
    RuleDefinition(
        "SYM005",
        "代表圖與完整符號說明名稱不同",
        "symbols",
        "warning",
        "相同符號在代表圖與完整符號說明中應使用一致名稱。",
    ),
    RuleDefinition(
        "FIG001",
        "圖式編號不連續",
        "drawings",
        "error",
        "圖式簡單說明中的圖號應由圖1開始並依序列出。",
    ),
    RuleDefinition(
        "FIG002",
        "指定代表圖未列入圖式簡單說明",
        "drawings",
        "error",
        "指定代表圖的圖號應可在圖式簡單說明中找到。",
    ),
    RuleDefinition(
        "REF001",
        "實施方式標號未列入符號說明",
        "references",
        "warning",
        "實施方式使用的元件標號應列入完整符號說明。",
    ),
    RuleDefinition(
        "REF002",
        "實施方式元件名稱與標號不一致",
        "references",
        "error",
        "已列入完整符號說明的元件名稱，在實施方式中應搭配相同標號。",
    ),
    RuleDefinition(
        "REF004",
        "實施方式元件名稱後缺少標號",
        "references",
        "error",
        "實施方式中具有冠詞或數量詞的元件名稱，必須於名稱或括號補充說明後標示符號。",
    ),
    RuleDefinition(
        "REF005",
        "疑似元件名稱錯字",
        "references",
        "warning",
        "將全文中與符號說明元件名稱僅差一個中文字的詞列為可能的輸入錯誤。",
    ),
    RuleDefinition(
        "REF006",
        "完整標的名稱中誤插元件標號",
        "references",
        "error",
        "完整發明／新型名稱必須保持連續，不得在其中的元件名稱後插入符號說明標號。",
    ),
    RuleDefinition(
        "REF007",
        "實施方式段落參閱圖式過多",
        "references",
        "error",
        "實施方式的單一段落最多可參閱四張不同圖式；圖號範圍會展開後計算。",
    ),
    RuleDefinition(
        "CLM001",
        "請求項缺少明確編號",
        "claims",
        "error",
        "申請專利範圍必須使用 Word 自動產生的「【請求項N】」編號。",
    ),
    RuleDefinition(
        "CLM002",
        "請求項編號不連續",
        "claims",
        "error",
        "請求項應由1開始並依文件順序連續編號。",
    ),
    RuleDefinition(
        "CLM003",
        "請求項依附關係無效",
        "claims",
        "error",
        "附屬項只能引用已存在且編號較小的請求項。",
    ),
    RuleDefinition(
        "CLM004",
        "多項附屬項未以選擇式記載",
        "claims",
        "error",
        "多項附屬項引用二項以上請求項時，應以「任一項」或「或」等選擇式記載。",
    ),
    RuleDefinition(
        "CLM005",
        "多項附屬項依附另一多項附屬項",
        "claims",
        "error",
        "多項附屬項不得直接或間接依附於另一多項附屬項。",
    ),
    RuleDefinition(
        "TXT001",
        "全形英數字",
        "typography",
        "info",
        "標示可安全轉換為半形的全形英文字母或數字。",
    ),
    RuleDefinition(
        "TXT002",
        "非標準 prime mark",
        "typography",
        "info",
        "將相似的 prime／全形撇號統一為半形 apostrophe。",
    ),
)

RULE_CATALOG += (
    RuleDefinition("STR004", "專利類型無法確定", "structure", "error", "文件必須具有可辨識的發明或新型章節名稱。"),
    RuleDefinition("STR005", "三大章節缺漏或順序錯誤", "structure", "error", "摘要、說明書及申請專利範圍必須各出現一次並依固定順序排列。"),
    RuleDefinition("STR006", "中型章節位置或順序錯誤", "structure", "error", "中型章節必須位於正確的大章節並依固定順序排列。"),
    RuleDefinition("STR007", "不允許的中括號標題", "structure", "error", "正式文件不得加入固定清單以外的中括號章節。"),
    RuleDefinition("STR009", "大章節分節符號（選用）", "structure", "info", "大章節可使用下一頁分節符號，但未使用時不列為錯誤。"),
    RuleDefinition("STR010", "章節名稱文字不符", "structure", "error", "忽略括號、空白及字型後，章節名稱文字仍須與所屬專利類型相符。"),
    RuleDefinition("FMT004", "頁面設定不符", "formatting", "error", "頁面大小、邊界及頁首頁尾距離必須符合正式範本。"),
    RuleDefinition("TTL001", "中文名稱前後不一致", "titles", "error", "摘要與說明書的中文名稱必須逐字完全相同。"),
    RuleDefinition("TTL002", "英文名稱出現位置或內容不一致", "titles", "error", "英文名稱為選填，但必須兩處同時存在且逐字相同。"),
    RuleDefinition("PNO004", "手動輸入說明書段號", "paragraph_numbering", "error", "說明書段號不得直接鍵入本文，必須使用 Word 自動編號。"),
    RuleDefinition("PNO005", "空的說明書段號", "paragraph_numbering", "error", "具有說明書段號的段落必須有正文內容。"),
    RuleDefinition("PNO006", "段號出現在不允許的位置", "paragraph_numbering", "error", "說明書段號只能出現在發明／新型說明書。"),
    RuleDefinition("FIG003", "圖說未使用獨立段落", "drawings", "error", "每個主要圖號必須位於獨立 Word 段落且每段只能有一個行首主要圖號。"),
    RuleDefinition("SYM006", "符號項目重複", "symbols", "error", "同一份符號說明不得重複列出相同符號。"),
    RuleDefinition("REF003", "發明與新型用語混用", "references", "error", "發明與新型文件必須使用對應的固定用語。"),
    RuleDefinition("CLM006", "請求項不是 Word 自動編號", "claims", "error", "請求項編號不得手動鍵入。"),
    RuleDefinition("CLM007", "空白請求項", "claims", "error", "每個請求項都必須具有實質文字內容。"),
    RuleDefinition("CLM008", "請求項使用權利要求用語", "claims", "error", "附屬項應使用申請專利範圍或請求項用語。"),
    RuleDefinition("CLM009", "請求項句號數量或位置錯誤", "claims", "error", "同一請求項只能出現一個全形句號「。」，且必須位於句尾；換行或跨 Word 段落仍合併計算。"),
    RuleDefinition("CLM011", "附屬項標的名稱不一致", "claims", "warning", "附屬項標的名稱應與其依附請求項一致。"),
    RuleDefinition(
        "CLM012",
        "構件先行基礎或單複數不符",
        "claims",
        "error",
        "構件第一次出現須有數量詞；後續單數使用「該」、複數使用「該等」；「對應的該A」仍屬單數指稱。",
    ),
    RuleDefinition("CLM013", "主要構件缺少關係敘述", "claims", "warning", "獨立項的主要構件間應記載連結、位置或對應關係。"),
    RuleDefinition("CLM014", "名稱與請求項標的不一致", "claims", "warning", "中文發明／新型名稱應與獨立項標的名稱相符。"),
    RuleDefinition("CLM015", "請求項內容無法可靠判別", "claims", "warning", "系統無法可靠拆解此請求項，必須人工確認。"),
    RuleDefinition("CLM016", "構件缺少先行揭露", "claims", "error", "請求項使用「該」、「該等」或其成員指稱前，前文或依附項必須先建立該構件。"),
    RuleDefinition("CLM017", "獨立項分行結尾標點錯誤", "claims", "error", "跨行獨立項的第一行應以全形冒號結尾，中間各行應以全形分號結尾，倒數第二行應以「；及」結尾。"),
    RuleDefinition("CLM018", "重複請求項提醒", "claims", "warning", "不同請求項的完整內文在忽略自身項次、排版空白與等價全半形標點後完全相同時，合併提醒人工確認；不推論法律上的權利範圍是否相同。"),
    RuleDefinition("CLM019", "請求項1與發明／新型內容對應提醒", "claims", "warning", "逐項查找請求項1在發明／新型內容的文字與有限結構對應證據；疑似遺漏或不一致列為警告，無法可靠解析列為資訊待確認，不直接判斷法律支持性。"),
    RuleDefinition("ABS001", "中文摘要字數提醒", "abstract", "warning", "中文摘要以 250 字為原則；分別提供非空白字元數與中英文字數估計，僅因計數口徑可能超過時列為資訊提醒。"),
    RuleDefinition("PCT001", "中文摘要結尾標點錯誤", "punctuation", "error", "發明／新型摘要的中文段落必須以全形句號結尾。"),
    RuleDefinition("PCT002", "說明書段落結尾標點錯誤", "punctuation", "error", "發明／新型說明書的數字段落原則上以全形句號結尾；實施方式非末段可用冒號引出下段，表格、圖式簡單說明與符號說明另行處理。"),
    RuleDefinition("PCT003", "圖式簡單說明導言結尾標點錯誤", "punctuation", "error", "圖式簡單說明的導言必須以全形冒號結尾。"),
    RuleDefinition("PCT004", "圖式簡單說明圖說結尾標點錯誤", "punctuation", "error", "各圖說中間以全形分號結尾，倒數第二圖以「；及」結尾，最後一圖以全形句號結尾。"),
    RuleDefinition("TXT003", "原住民族相關用語", "typography", "info", "偵測到相關用語時提示人工確認，不直接判定內容錯誤。"),
    RuleDefinition("TXT004", "連續重複中文字", "typography", "error", "任意兩個以上相鄰且相同的中文字視為可能的重複輸入錯誤。"),
    RuleDefinition("TBL001", "表格內請求項需人工確認", "tables", "warning", "表格儲存格會逐格進行文字檢查，但不跨欄拼接或判定請求項語法。"),
    RuleDefinition(
        "OCR001",
        "圖式缺失標號",
        "drawing_ocr",
        "error",
        "OCR 已完成時，實施方式段落出現的標號必須能在其參閱圖式中找到；圖式額外出現的標號不偵錯。",
    ),
    RuleDefinition(
        "OCR002",
        "剖視圖與羅馬剖切線不一致",
        "drawing_ocr",
        "error",
        "圖式簡單說明記載剖視／剖面關係時，來源圖必須偵測到對應羅馬剖切線，且羅馬數字換算後須與剖視圖圖號一致。",
    ),
    RuleDefinition(
        "OCR003",
        "圖式未被文件使用",
        "drawing_ocr",
        "error",
        "每張已載入 OCR 的圖式，至少必須在全文的引用、參閱文字或圖式簡單說明中出現一次。",
    ),
)

_RULE_BY_ID = {definition.rule_id: definition for definition in RULE_CATALOG}
_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}
_SYMBOL_SECTIONS = {
    "drawing_symbol_description",
    "representative_drawing_symbols",
}
_NON_SYMBOL_SEPARATORS = {
    "新型專利說明書",
    "發明專利說明書",
    "新型摘要",
    "發明摘要",
}
_SYMBOL_LINE = re.compile(
    r"^\s*[（(]?(?P<symbol>[^:：.．\r\n]+?)[）)]?"
    r"\s*[:：.．]\s*(?P<name>.*?)\s*$"
)
_SYMBOL_SPACE_LINE = re.compile(
    r"^\s*[（(]?(?P<symbol>[^\s:：.．,，、~～\-()（）]+)[）)]?"
    r"\s+(?P<name>\S(?:.*\S)?)\s*$"
)
_CLAIM_TOKEN = re.compile(
    r"[【〖\[]?[ \t]*請求項[ \t]*(?P<number>\d+)[ \t]*[】〗\]]?"
)
_CLAIM_PREFIX = re.compile(
    r"^[ \t]*[【〖\[]?[ \t]*請求項[ \t]*(?P<number>\d+)[ \t]*[】〗\]]?"
)
_AUTO_CLAIM_NUMBER = re.compile(
    r"^[【〖\[]?[ \t]*請求項[ \t]*(?P<number>\d+)[ \t]*[】〗\]]?$"
)
_CLAIM_RANGE = re.compile(
    r"(?:申請專利範圍)?請求項\s*(?P<start>\d+)\s*(?:至|到|~|－|-)\s*"
    r"(?:請求項\s*)?(?P<end>\d+)"
)
_CLAIM_LIST = re.compile(
    r"(?:申請專利範圍)?請求項\s*(?P<numbers>\d+(?:\s*(?:、|,|，|及|或)\s*\d+)+)"
)
_CLAIM_SINGLE = re.compile(r"(?:申請專利範圍)?請求項\s*(?P<number>\d+)")
_CLAIM_AS_DESCRIBED = re.compile(
    r"(?<![例諸])如(?P<citation>[^。；;\r\n]{0,240}?)所述"
)
_PARAGRAPH_NUMBER = re.compile(
    r"^\s*(?P<display>[【〖\[]\s*(?P<number>\d+)\s*[】〗\]])"
)
_VALID_PARAGRAPH_NUMBER = re.compile(r"^【\d{1,4}】$")
_NARRATIVE_SECTIONS = {
    "technical_field",
    "background_art",
    "disclosure",
    "brief_description_of_drawings",
    "embodiments",
    "drawing_symbol_description",
}
_DESCRIPTION_SECTION_ORDER = (
    "technical_field",
    "background_art",
    "disclosure",
    "brief_description_of_drawings",
    "embodiments",
    "drawing_symbol_description",
)
_FIGURE_AT_LINE_START = re.compile(
    r"^\s*圖\s*(?P<start>\d+)(?:\s*(?:至|到|~|～|－|-)\s*(?P<end>\d+))?"
)
_FIGURE_REFERENCE = re.compile(r"圖\s*(?P<number>\d+)")
_DESCRIPTION_REFERENCE_SECTIONS = {
    "technical_field",
    "background_art",
    "disclosure",
    "brief_description_of_drawings",
    "embodiments",
}
_COMPONENT_REFERENCE = re.compile(
    r"(?P<name>[\u4e00-\u9fff]{1,12})(?P<label>\d{1,3}[A-Za-z]?(?:['′])?)"
)
_COMPONENT_NAME_SUFFIXES = (
    "元件", "模組", "構件", "組件", "本體", "箱體", "殼體", "板", "蓋",
    "件", "片", "孔", "槽", "桿", "軸", "座", "箱", "端", "臂", "部",
)
_COMPONENT_QUANTIFIER_EXPRESSION = (
    r"至少(?:一|[二三四五六七八九十百兩]+)|"
    r"以下|下列|幾個|數個|多個|複數|[一二三四五六七八九十百兩]+"
)
_CLAIM_QUANTITY_EXEMPT_COMPONENT_NAMES = frozenset({"步驟"})
_QUANTIFIED_AS_DESCRIBED_TARGET = re.compile(
    rf"^(?P<quantity>{_COMPONENT_QUANTIFIER_EXPRESSION})"
    r"如(?P<citation>[^。；;\r\n]{1,240}?)所述(?:的|之)?"
    r"(?P<subject>[\u3400-\u9fffA-Za-z0-9]+?)"
    r"(?=\s*(?:，|,|；|;|：|:|。|包含|包括|具有|其特徵|$))"
)
# Singular references to a component disclosed as plural are errors except
# inside the existing distributive/member scopes (for example「每一該送料輥的
# 該輥本體」).  Keeping the strict path enabled prevents a plain「該元件」from
# silently collapsing a plural set to one unspecified member.
_SUSPEND_SINGULAR_REFERENCE_AFTER_PLURAL_CHECK = False
_COMPONENT_DISTRIBUTIVE_REFERENCES = (
    "各自的該",
    "每一該",
    "各自該",
    "各該",
)
_COMPONENT_SINGULAR_SELECTION_REFERENCES = (
    "其中任一該",
    "其中一該",
    "任一該",
    "任意該",
)
_COMPONENT_PLURAL_SELECTION_REFERENCES = ("其中複數該",)
_COMPONENT_MEMBER_REFERENCES = tuple(
    sorted(
        set(_COMPONENT_DISTRIBUTIVE_REFERENCES)
        | set(_COMPONENT_SINGULAR_SELECTION_REFERENCES),
        key=len,
        reverse=True,
    )
)
_COMPONENT_REFERENCE_TOKENS = tuple(
    sorted(
        set(_COMPONENT_MEMBER_REFERENCES)
        | set(_COMPONENT_PLURAL_SELECTION_REFERENCES)
        | {"該等", "該"},
        key=len,
        reverse=True,
    )
)
_COMPONENT_REFERENCE_EXPRESSION = "|".join(
    re.escape(token) for token in _COMPONENT_REFERENCE_TOKENS
)
_COMPONENT_DISTRIBUTIVE_REFERENCE_EXPRESSION = "|".join(
    re.escape(token) for token in _COMPONENT_DISTRIBUTIVE_REFERENCES
)
_COMPONENT_HIERARCHY_PLURAL_REFERENCES = frozenset(
    set(_COMPONENT_DISTRIBUTIVE_REFERENCES)
    | set(_COMPONENT_PLURAL_SELECTION_REFERENCES)
    | {"該等"}
)
_FULLWIDTH_ALNUMERIC = re.compile(r"[０-９Ａ-Ｚａ-ｚ]+")
_NONSTANDARD_PRIME = {"′": "'", "＇": "'"}
_REPEATED_CJK_CHARACTER = re.compile(
    r"(?P<character>[\u3400-\u9fff])(?P=character)+"
)
_EMBODIMENT_INSTANCE_PATTERN = re.compile(
    r"(?:本發明|本新型)[^，,；;。\r\n]{0,80}?"
    r"(?:一?第[一二三四五六七八九十百兩0-9]+|一較佳|較佳)?實施例"
)
_LOCAL_COMPONENT_SUFFIXES = tuple(
    sorted(
        set(_COMPONENT_NAME_SUFFIXES)
        | {
            "部件", "單元", "裝置", "機構", "系統", "總成", "表面",
            "空間", "方向", "軸線", "區域", "開口", "通道", "腔室",
            "螺絲", "螺孔", "齒輪", "按鍵", "導軌", "側壁", "接點",
        },
        key=len,
        reverse=True,
    )
)
_LOCAL_COMPONENT_SUFFIX_EXPRESSION = "(?:" + "|".join(
    re.escape(suffix) for suffix in _LOCAL_COMPONENT_SUFFIXES
) + ")"
_LOCAL_COMPONENT_REFERENCE_PATTERN = re.compile(
    rf"(?:{_COMPONENT_REFERENCE_EXPRESSION})"
    rf"(?P<name>[\u3400-\u9fff]{{1,18}}?{_LOCAL_COMPONENT_SUFFIX_EXPRESSION})"
    rf"(?=係|為|是|可|能|會|具有|包含|包括|設有|配置|形成|設置|"
    rf"位於|連接|結合|對應|抵接|鄰接|朝|沿|用以|用於|與|及|之|的|"
    rf"[，,；;。:：、])"
)
_INDIGENOUS_TERM = re.compile(r"原住民(?:族)?|部落|族語")
_BRACKETED_HEADING = re.compile(r"^\s*(?:【([^】]+)】|〖([^〗]+)〗)")
_MAJOR_ORDER = ("major_abstract", "major_description", "claims")
_SAME_LINE_SECTIONS = {
    "invention_title",
    "utility_model_title",
    "english_title",
    "designated_representative_drawing",
}
_MEDIUM_LAYOUT = {
    "major_abstract": (
        ("title", True),
        ("english_title", False),
        ("abstract_zh", True),
        ("abstract_en", False),
        ("designated_representative_drawing", True),
        ("representative_drawing_symbols", True),
    ),
    "major_description": (
        ("title", True),
        ("english_title", False),
        ("technical_field", True),
        ("background_art", True),
        ("disclosure", True),
        ("brief_description_of_drawings", True),
        ("embodiments", True),
        ("drawing_symbol_description", True),
    ),
}


def _title_key(patent_type: str) -> str:
    return "utility_model_title" if patent_type == "utility_model" else "invention_title"


def _expected_heading_text(
    patent_type: str,
    major_key: str,
    section_key: str,
) -> str:
    is_utility = patent_type == "utility_model"
    if section_key == "major_abstract":
        return "【新型摘要】" if is_utility else "【發明摘要】"
    if section_key == "major_description":
        return "【新型說明書】" if is_utility else "【發明說明書】"
    if section_key == "claims":
        return "【新型申請專利範圍】" if is_utility else "【發明申請專利範圍】"
    if section_key in {"invention_title", "utility_model_title", "title"}:
        return "【中文新型名稱】" if is_utility else "【中文發明名稱】"
    if section_key == "english_title":
        return "【英文新型名稱】" if is_utility else "【英文發明名稱】"
    if section_key == "abstract_zh":
        return "【中文】"
    if section_key == "abstract_en":
        return "【英文】"
    if section_key == "designated_representative_drawing":
        return "【指定代表圖】"
    if section_key == "representative_drawing_symbols":
        return "【代表圖之符號簡單說明】"
    if section_key == "technical_field":
        return "【技術領域】"
    if section_key == "background_art":
        return "【先前技術】"
    if section_key == "disclosure":
        return "【新型內容】" if is_utility else "【發明內容】"
    if section_key == "brief_description_of_drawings":
        return "【圖式簡單說明】"
    if section_key == "embodiments":
        return "【實施方式】"
    if section_key == "drawing_symbol_description":
        return "【符號說明】"
    return ""


def _paragraph_by_index(document: PatentDocument) -> Dict[int, PatentParagraph]:
    return {paragraph.index: paragraph for paragraph in document.paragraphs}


def _run_indices(paragraph: PatentParagraph, start: int, end: int) -> List[int]:
    return [
        span.run_index
        for span in paragraph.run_spans
        if span.start < end and span.end > start
    ]


def _issue(
    rule_id: str,
    message: str,
    suggestion: str,
    *,
    paragraph: Optional[PatentParagraph] = None,
    section_key: str = "",
    section_title: str = "",
    start: Optional[int] = None,
    end: Optional[int] = None,
    safe_auto_fix: bool = False,
    replacement: Optional[str] = None,
    details: Optional[Dict[str, object]] = None,
    rule_definition: Optional[RuleDefinition] = None,
) -> PatentIssue:
    definition = rule_definition or _RULE_BY_ID[rule_id]
    issue_details = dict(details or {})
    if paragraph is not None:
        section_key = section_key or paragraph.section_key or ""
        section_title = section_title or paragraph.section_title
        if start is not None and end is not None:
            start = max(0, min(start, len(paragraph.text)))
            end = max(start, min(end, len(paragraph.text)))
            matched_text = paragraph.text[start:end]
            runs = _run_indices(paragraph, start, end)
        else:
            matched_text = ""
            runs = []
        paragraph_index = paragraph.index
        source_path = paragraph.source_path
        if paragraph.source_kind == "table":
            issue_details.setdefault("source_kind", "table")
            issue_details.setdefault("table_index", paragraph.table_index)
            issue_details.setdefault("row_index", paragraph.row_index)
            issue_details.setdefault("cell_index", paragraph.cell_index)
    else:
        matched_text = ""
        runs = []
        paragraph_index = None
        source_path = ""

    identity = "|".join(
        [
            rule_id,
            str(paragraph_index),
            str(start),
            str(end),
            section_key,
            message,
        ]
    )
    digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:12]
    return PatentIssue(
        issue_id=f"{rule_id}-{digest}",
        rule_id=rule_id,
        severity=definition.default_severity,
        category=definition.category,
        message=message,
        suggestion=suggestion,
        section_key=section_key,
        section_title=section_title,
        paragraph_index=paragraph_index,
        source_path=source_path,
        char_start=start,
        char_end=end,
        matched_text=matched_text,
        run_indices=runs,
        safe_auto_fix=safe_auto_fix,
        replacement=replacement,
        details=issue_details,
    )


def _raw_remainder(paragraph: PatentParagraph, expected_heading: str) -> str:
    if paragraph.content_text.strip():
        return paragraph.content_text.strip()
    if paragraph.text.startswith(expected_heading):
        return paragraph.text[len(expected_heading):]
    match = _BRACKETED_HEADING.match(paragraph.text)
    if match is not None:
        return paragraph.text[match.end():].lstrip(" ：:")
    colon = re.match(r"^.*?[:：]\s*(.*)$", paragraph.text)
    return colon.group(1).strip() if colon is not None else ""


def _heading_label(text: str) -> str:
    stripped = (text or "").strip()
    match = _BRACKETED_HEADING.match(stripped)
    if match is not None:
        label = match.group(1) or match.group(2) or ""
    else:
        colon = re.match(r"^([^:：]+?)\s*[:：]", stripped)
        label = colon.group(1) if colon is not None else stripped
    label = re.sub(r"\s+", "", label)
    label = re.sub(r"^[一二三四五六七八九十百0-9]+[、.．]", "", label)
    return label.strip("：:【】〖〗[]")


def _expected_heading_label(expected_heading: str) -> str:
    return expected_heading.strip("【】〖〗").replace(" ", "")


def _section_has_content(
    section,
    paragraph_map: Dict[int, PatentParagraph],
    expected_heading: str,
) -> bool:
    heading = paragraph_map.get(section.heading_paragraph_index)
    if heading is None:
        return False
    if section.key in _SAME_LINE_SECTIONS:
        return bool(_raw_remainder(heading, expected_heading))
    return any(
        paragraph_map[index].content_text.strip()
        for index in section.paragraph_indices
        if index in paragraph_map
    )


def _formal_structure_issues(document: PatentDocument) -> Iterable[PatentIssue]:
    paragraph_map = _paragraph_by_index(document)
    patent_type = document.patent_type
    if patent_type not in {"invention", "utility_model"}:
        yield _issue(
            "STR004",
            "文件無法由章節名稱確定為發明或新型。",
            "請確認三大章節名稱包含正確的發明或新型文字。",
            section_title="整份文件",
            details={"detected_patent_type": patent_type},
        )
        # Use invention names only to keep the remaining diagnostics useful.
        patent_type = "invention"

    title_key = _title_key(patent_type)
    major_sections = [
        section for section in document.sections if section.level == "major"
    ]
    major_by_key: Dict[str, List[object]] = defaultdict(list)
    for section in major_sections:
        major_by_key[section.key].append(section)

    selected_majors = []
    for major_key in _MAJOR_ORDER:
        expected_heading = _expected_heading_text(
            patent_type, major_key, major_key
        )
        occurrences = major_by_key.get(major_key, [])
        if not occurrences:
            yield _issue(
                "STR001",
                f"找不到必要大章節「{expected_heading}」。",
                "請依正式範本補齊三大章節。",
                section_key=major_key,
                section_title=expected_heading,
                details={"required_section": major_key},
            )
            continue
        selected_majors.append(occurrences[0])
        for duplicate in occurrences[1:]:
            paragraph = paragraph_map.get(duplicate.heading_paragraph_index)
            yield _issue(
                "STR003",
                f"大章節「{expected_heading}」重複出現。",
                "每個大章節只能出現一次。",
                paragraph=paragraph,
                start=0 if paragraph else None,
                end=len(paragraph.text) if paragraph else None,
            )
        heading = paragraph_map.get(occurrences[0].heading_paragraph_index)
        if heading is not None and (
            _heading_label(heading.text) != _expected_heading_label(expected_heading)
        ):
            yield _issue(
                "STR010",
                f"大章節名稱文字應為「{_expected_heading_label(expected_heading)}」。",
                "請只確認章節名稱文字；括號、空白、字型與字體大小不列入判斷。",
                paragraph=heading,
                start=0,
                end=len(heading.text),
                details={"expected_heading": expected_heading},
            )

    actual_major_order = [section.key for section in major_sections]
    if actual_major_order != list(_MAJOR_ORDER):
        anchor = (
            paragraph_map.get(major_sections[0].heading_paragraph_index)
            if major_sections
            else None
        )
        yield _issue(
            "STR005",
            "三大章節缺漏、重複或未依摘要、說明書、申請專利範圍排列。",
            "請依固定順序重新安排三大章節。",
            paragraph=anchor,
            start=0 if anchor else None,
            end=len(anchor.text) if anchor else None,
            details={"actual_order": actual_major_order},
        )

    # The three major sections are ordered by their heading text. A Word
    # next-page section break remains permitted but is no longer mandatory.

    sections_by_major: Dict[str, List[object]] = defaultdict(list)
    for section in document.sections:
        if section.level == "medium":
            sections_by_major[section.major_section_key].append(section)

    for major_key, layout in _MEDIUM_LAYOUT.items():
        expected_keys = [title_key if key == "title" else key for key, _ in layout]
        required_by_key = {
            title_key if key == "title" else key: required
            for key, required in layout
        }
        actual = sections_by_major.get(major_key, [])
        actual_keys = [section.key for section in actual]
        by_key: Dict[str, List[object]] = defaultdict(list)
        for section in actual:
            by_key[section.key].append(section)

        for key in expected_keys:
            expected_heading = _expected_heading_text(
                patent_type, major_key, key
            )
            occurrences = by_key.get(key, [])
            if required_by_key[key] and not occurrences:
                yield _issue(
                    "STR001",
                    f"「{_expected_heading_text(patent_type, major_key, major_key)}」缺少必要章節「{expected_heading}」。",
                    "請依正式範本補上必要章節。",
                    section_key=key,
                    section_title=expected_heading,
                    details={"major_section": major_key, "required_section": key},
                )
                continue
            for duplicate in occurrences[1:]:
                paragraph = paragraph_map.get(duplicate.heading_paragraph_index)
                yield _issue(
                    "STR003",
                    f"章節「{expected_heading}」在同一大章節中重複出現。",
                    "請移除重複標題或合併內容。",
                    paragraph=paragraph,
                    start=0 if paragraph else None,
                    end=len(paragraph.text) if paragraph else None,
                )
            if not occurrences:
                continue
            section = occurrences[0]
            heading = paragraph_map.get(section.heading_paragraph_index)
            if heading is None:
                continue
            if (
                _heading_label(heading.text)
                != _expected_heading_label(expected_heading)
            ):
                yield _issue(
                    "STR010",
                    f"章節名稱文字應為「{_expected_heading_label(expected_heading)}」。",
                    "請只確認章節名稱文字；括號、空白、字型與字體大小不列入判斷。",
                    paragraph=heading,
                    start=0,
                    end=len(heading.text),
                    details={"expected_heading": expected_heading},
                )
            if not _section_has_content(section, paragraph_map, expected_heading):
                yield _issue(
                    "STR002",
                    f"章節「{expected_heading}」沒有內容。",
                    "請補入內容；若為名稱或指定代表圖，內容必須與標題位於同一行。",
                    paragraph=heading,
                    start=0,
                    end=len(heading.text),
                )

        unexpected = [key for key in actual_keys if key not in expected_keys]
        positions = [
            expected_keys.index(key)
            for key in actual_keys
            if key in expected_keys
        ]
        if unexpected or positions != sorted(positions):
            anchor_section = actual[0] if actual else None
            anchor = (
                paragraph_map.get(anchor_section.heading_paragraph_index)
                if anchor_section is not None
                else None
            )
            yield _issue(
                "STR006",
                f"「{_expected_heading_text(patent_type, major_key, major_key)}」的中型章節位置或順序不符。",
                "請依固定中型章節順序排列，且不要加入其他章節。",
                paragraph=anchor,
                start=0 if anchor else None,
                end=len(anchor.text) if anchor else None,
                details={"actual_sections": actual_keys, "unexpected": unexpected},
            )

    claim_medium_sections = sections_by_major.get("claims", [])
    for section in claim_medium_sections:
        paragraph = paragraph_map.get(section.heading_paragraph_index)
        yield _issue(
            "STR006",
            "申請專利範圍內不得出現中型章節標題。",
            "請移除該中型標題。",
            paragraph=paragraph,
            start=0 if paragraph else None,
            end=len(paragraph.text) if paragraph else None,
        )

    for paragraph in document.paragraphs:
        if (
            paragraph.source_kind == "table"
            or paragraph.is_heading
            or not paragraph.text.strip()
        ):
            continue
        match = _BRACKETED_HEADING.match(paragraph.text)
        if match is None:
            continue
        label = (match.group(1) or match.group(2) or "").strip()
        if label.isdigit() or re.fullmatch(r"請求項\s*\d+", label):
            continue
        yield _issue(
            "STR007",
            f"偵測到不在固定清單內的中括號標題「{label}」。",
            "請確認並移除非正式章節標題。",
            paragraph=paragraph,
            start=match.start(),
            end=match.end(),
        )

    chinese_sections = [
        section
        for section in document.sections
        if section.key == title_key
        and section.major_section_key in {"major_abstract", "major_description"}
    ]
    if len(chinese_sections) == 2:
        headings = [
            paragraph_map[section.heading_paragraph_index]
            for section in chinese_sections
        ]
        expected = _expected_heading_text(patent_type, "", title_key)
        names = [_raw_remainder(paragraph, expected) for paragraph in headings]
        if names[0] != names[1]:
            yield _issue(
                "TTL001",
                "摘要與說明書中的中文名稱並非逐字完全相同。",
                "請回到 Word 統一兩處中文名稱。",
                paragraph=headings[1],
                start=len(expected),
                end=len(headings[1].text),
                details={"abstract_title": names[0], "description_title": names[1]},
            )

    english_by_major = {
        major: next(
            (
                section
                for section in document.sections
                if section.key == "english_title"
                and section.major_section_key == major
            ),
            None,
        )
        for major in ("major_abstract", "major_description")
    }
    if bool(english_by_major["major_abstract"]) != bool(
        english_by_major["major_description"]
    ):
        present = next(section for section in english_by_major.values() if section)
        paragraph = paragraph_map.get(present.heading_paragraph_index)
        yield _issue(
            "TTL002",
            "英文名稱只出現在摘要或說明書其中一處。",
            "英文名稱可省略；若使用，兩個大章節都必須存在。",
            paragraph=paragraph,
            start=0 if paragraph else None,
            end=len(paragraph.text) if paragraph else None,
        )
    elif all(english_by_major.values()):
        expected = _expected_heading_text(patent_type, "", "english_title")
        first = paragraph_map[english_by_major["major_abstract"].heading_paragraph_index]
        second = paragraph_map[english_by_major["major_description"].heading_paragraph_index]
        names = (_raw_remainder(first, expected), _raw_remainder(second, expected))
        if names[0] != names[1]:
            yield _issue(
                "TTL002",
                "摘要與說明書中的英文名稱並非逐字完全相同。",
                "請統一兩處英文名稱，或同時刪除兩處英文名稱章節。",
                paragraph=second,
                start=len(expected),
                end=len(second.text),
                details={"abstract_title": names[0], "description_title": names[1]},
            )


def _format_issues(document: PatentDocument) -> Iterable[PatentIssue]:
    # Heading and body typography are intentionally ignored.  The checker
    # only retains document-level page-layout validation here.
    expected_layout = {
        "width": 11906,
        "height": 16838,
        "margin_top": 1134,
        "margin_right": 1134,
        "margin_bottom": 1134,
        "margin_left": 1134,
        "header": 851,
        "footer": 992,
        "gutter": 0,
    }
    for layout in document.page_layouts:
        mismatches = {
            key: {"expected": expected, "actual": layout.get(key)}
            for key, expected in expected_layout.items()
            if layout.get(key) != expected
        }
        if mismatches:
            yield _issue(
                "FMT004",
                f"第{int(layout.get('index', 0)) + 1}個 Word 節的頁面設定與正式範本不同。",
                "請套用正式範本的A4頁面、邊界及頁首頁尾距離。",
                section_title="整份文件",
                details={"mismatches": mismatches},
            )


def _paragraph_number_issues(
    document: PatentDocument,
) -> Iterable[PatentIssue]:
    """Validate numbering throughout the second major description section."""

    numbered_paragraphs: List[Tuple[PatentParagraph, int, str]] = []
    for paragraph in document.paragraphs:
        if paragraph.source_kind == "table":
            continue
        explicit = _PARAGRAPH_NUMBER.match(paragraph.text)
        has_patent_number = (
            paragraph.numbering_value is not None
            and bool(paragraph.numbering_text.strip())
            and re.fullmatch(r"[【〖\[]\d+[】〗\]]", paragraph.numbering_text.strip())
            is not None
        )
        in_description = (
            paragraph.major_section_key == "major_description"
            or (
                paragraph.major_section_key is None
                and paragraph.section_key in _NARRATIVE_SECTIONS
            )
        )

        if (explicit is not None or has_patent_number) and not in_description:
            display = (
                paragraph.numbering_text.strip()
                if has_patent_number
                else re.sub(r"\s+", "", explicit.group("display"))
            )
            yield _issue(
                "PNO006",
                f"說明書段號「{display}」出現在發明／新型說明書以外的位置。",
                "請移除該段號或將內容移回說明書大章節。",
                paragraph=paragraph,
                start=0,
                end=(explicit.end("display") if explicit else min(len(paragraph.text), 16)),
            )

        if paragraph.is_heading or not in_description:
            continue

        if paragraph.section_key == "drawing_symbol_description":
            # Symbol-description labels have their own semantic checks.  Their
            # paragraph-number display, sequence, and Word-list implementation
            # are deliberately outside the paragraph-numbering rules.
            continue

        if explicit is not None:
            display = re.sub(r"\s+", "", explicit.group("display"))
            yield _issue(
                "PNO004",
                f"段號「{display}」是直接鍵入本文的文字，不是 Word 自動編號。",
                "請刪除手動文字並套用正式說明書段號清單。",
                paragraph=paragraph,
                start=explicit.start("display"),
                end=explicit.end("display"),
            )
            continue

        if has_patent_number:
            value = int(paragraph.numbering_value or 0)
            display = paragraph.numbering_text.strip()
            numbered_paragraphs.append((paragraph, value, display))
            if not paragraph.content_text.strip():
                yield _issue(
                    "PNO005",
                    f"段號「{display}」後沒有正文內容。",
                    "請補入本段內容或刪除空的自動編號段落。",
                    paragraph=paragraph,
                    start=0,
                    end=0,
                    details={"numbering_value": value},
                )
            if not _VALID_PARAGRAPH_NUMBER.fullmatch(display):
                yield _issue(
                    "PNO002",
                    f"段號顯示為「{display}」，不是合法的「【1】」至「【9999】」格式。",
                    f"請確認 Word 段號是否使用全形方括號；可顯示為「【{value}】」或「【{value:04d}】」。",
                    paragraph=paragraph,
                    start=0,
                    end=min(len(paragraph.text), 36),
                    details={"actual_display": display, "numbering_value": value},
                )
            continue

        if not paragraph.content_text.strip():
            continue
        if paragraph.section_key == "brief_description_of_drawings" and _FIGURE_AT_LINE_START.match(paragraph.text):
            continue
        if paragraph.numbering_id is not None:
            # A non-patent Word list is a legal subordinate/bullet list.
            continue
        if (paragraph.left_indent or 0) >= 360 or paragraph.hanging_indent is not None:
            continue

        yield _issue(
            "PNO001",
            f"「{paragraph.section_title}」的正文段落缺少可辨識段號。",
            "請回到 Word 將此段設為說明書的連續編號清單；段號可選擇是否補零。",
            paragraph=paragraph,
            start=0,
            end=min(len(paragraph.text), 36),
        )

    for expected, (paragraph, value, display) in enumerate(
        numbered_paragraphs, start=1
    ):
        if value is None or value == expected:
            continue
        explicit = _PARAGRAPH_NUMBER.match(paragraph.text)
        yield _issue(
            "PNO003",
            f"此段為「{display or value}」，依文件順序預期段號值應為「{expected}」。",
            "請檢查前後段落的 Word 自動編號，並由1起連續編排；是否補零不影響順序。",
            paragraph=paragraph,
            start=0,
            end=min(len(paragraph.text), 36),
            details={"expected_number": expected, "actual_number": value},
        )


def _section_order_issues(document: PatentDocument) -> Iterable[PatentIssue]:
    order = {key: position for position, key in enumerate(_DESCRIPTION_SECTION_ORDER)}
    first_sections = {}
    for section in document.sections:
        if section.key in order and section.key not in first_sections:
            first_sections[section.key] = section
    present = sorted(
        first_sections.values(), key=lambda section: section.heading_paragraph_index
    )
    previous_position = -1
    paragraph_map = _paragraph_by_index(document)
    for section in present:
        current_position = order[section.key]
        if current_position < previous_position:
            heading = paragraph_map.get(section.heading_paragraph_index)
            expected_titles = "、".join(
                first_sections[key].title
                for key in _DESCRIPTION_SECTION_ORDER
                if key in first_sections
            )
            yield _issue(
                "ORD001",
                f"章節「{section.title}」出現在不符合一般格式的順序位置。",
                f"請人工確認主要章節順序是否應為：{expected_titles}。",
                paragraph=heading,
                section_key=section.key,
                section_title=section.title,
                start=0 if heading else None,
                end=len(heading.text) if heading else None,
            )
            return
        previous_position = current_position


def _paragraph_visible_content(paragraph: PatentParagraph) -> str:
    """Return the section content without discarding its original punctuation."""

    source = paragraph.text or ""
    if paragraph.is_heading and paragraph.content_text.strip():
        bracketed = _BRACKETED_HEADING.match(source)
        if bracketed is not None:
            return source[bracketed.end():].lstrip(" ：:").strip()
        colon = re.match(r"^.*?[:：]\s*(.*)$", source)
        if colon is not None:
            return colon.group(1).strip()
    return source.strip()


def _ending_highlight_span(paragraph: PatentParagraph) -> Tuple[int, int]:
    """Highlight the visible tail where a missing/wrong terminator is repaired."""

    visible_end = len(paragraph.text.rstrip())
    if visible_end <= 0:
        return 0, 0
    return max(0, visible_end - 3), visible_end


_ABSTRACT_HAN = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U000323af]"
)
_ABSTRACT_WORD = re.compile(r"[^\W_]+(?:['’′-][^\W_]+)*", re.UNICODE)


def _abstract_length_issues(document: PatentDocument) -> Iterable[PatentIssue]:
    """Count Chinese abstract content only, with an explicit estimate boundary."""

    paragraph_map = _paragraph_by_index(document)
    for section in document.sections:
        if section.key != "abstract_zh":
            continue
        contents: List[Tuple[PatentParagraph, str]] = []
        for index in section.paragraph_indices:
            paragraph = paragraph_map.get(index)
            if paragraph is None:
                continue
            # A heading-only paragraph must never contribute its title.
            content = (
                paragraph.content_text.strip()
                if paragraph.is_heading
                else paragraph.text.strip()
            )
            if content:
                contents.append((paragraph, content))
        if not contents:
            continue
        text = "\n".join(content for _paragraph, content in contents)
        non_whitespace_count = sum(not char.isspace() for char in text)
        if non_whitespace_count <= 250:
            continue
        estimated_word_count = len(_ABSTRACT_HAN.findall(text)) + len(
            _ABSTRACT_WORD.findall(_ABSTRACT_HAN.sub(" ", text))
        )
        over_estimate = estimated_word_count > 250
        paragraph = contents[0][0]
        issue = _issue(
            "ABS001",
            (
                f"中文摘要估計 {estimated_word_count} 字，超過 250 字原則"
                f"（非空白字元共 {non_whitespace_count} 個）。"
                if over_estimate
                else f"中文摘要非空白字元共 {non_whitespace_count} 個，"
                f"中英文字數估計 {estimated_word_count} 字；"
                "依計數口徑可能超過 250 字，請人工確認。"
            ),
            "請確認摘要是否需精簡。估計方式：每個中文字計一字，連續英文／數字詞計一字，"
            "不計標點與空白；非空白字元數則包含標點。此為輔助提醒，非官方計數結果。",
            paragraph=paragraph,
            section_key=section.key,
            section_title=section.title,
            start=0,
            end=len(paragraph.text),
            details={
                "limit": 250,
                "non_whitespace_count": non_whitespace_count,
                "estimated_word_count": estimated_word_count,
                "paragraph_indices": [item.index for item, _text in contents],
                "counting_method": "han_characters_plus_alphanumeric_words",
                "manual_count_confirmation": not over_estimate,
            },
        )
        if not over_estimate:
            issue.severity = "info"
        yield issue


def _ending_punctuation_issues(
    document: PatentDocument,
) -> Iterable[PatentIssue]:
    """Validate section-aware terminal punctuation without changing Word text."""

    paragraph_map = _paragraph_by_index(document)

    # The Chinese abstract may span more than one Word paragraph.  Only the
    # end of the whole Chinese subsection needs the terminal full stop.
    for section in document.sections:
        if section.key != "abstract_zh":
            continue
        candidates = [
            paragraph_map[index]
            for index in section.paragraph_indices
            if index in paragraph_map
            and _paragraph_visible_content(paragraph_map[index])
        ]
        if not candidates:
            continue
        paragraph = candidates[-1]
        if _paragraph_visible_content(paragraph).endswith("。"):
            continue
        start, end = _ending_highlight_span(paragraph)
        yield _issue(
            "PCT001",
            "中文摘要最後一段沒有以全形句號「。」結尾。",
            "請在中文摘要末尾補上全形句號「。」。",
            paragraph=paragraph,
            start=start,
            end=end,
            details={"expected_ending": "。"},
        )

    embodiment_content_indices = [
        paragraph.index
        for paragraph in document.paragraphs
        if paragraph.section_key == "embodiments"
        and not paragraph.is_heading
        and _paragraph_visible_content(paragraph)
    ]
    last_embodiment_content_index = (
        max(embodiment_content_indices)
        if embodiment_content_indices
        else None
    )

    # General numbered description paragraphs use a full stop.  Drawing
    # captions have their own sequence below, while symbol descriptions are
    # intentionally exempt from all terminal-punctuation checks.
    for paragraph in document.paragraphs:
        if (
            paragraph.source_kind == "table"
            or paragraph.is_heading
            or paragraph.major_section_key != "major_description"
            or paragraph.section_key
            in {"brief_description_of_drawings", "drawing_symbol_description"}
        ):
            continue
        is_numbered = (
            paragraph.numbering_value is not None
            or _PARAGRAPH_NUMBER.match(paragraph.text) is not None
        )
        content = _paragraph_visible_content(paragraph)
        embodiment_colon_is_valid = (
            paragraph.section_key == "embodiments"
            and content.endswith(("：", ":"))
            and paragraph.index != last_embodiment_content_index
        )
        if (
            not is_numbered
            or not content
            or content.endswith("。")
            or embodiment_colon_is_valid
        ):
            continue
        start, end = _ending_highlight_span(paragraph)
        yield _issue(
            "PCT002",
            f"段號「{paragraph.numbering_text or '手動段號'}」的內容沒有以全形句號「。」結尾。",
            (
                "請將實施方式最後一小段改為全形句號「。」；冒號僅可用於引出後續小段。"
                if paragraph.section_key == "embodiments"
                and content.endswith(("：", ":"))
                else "請將本數字段落的結尾改為全形句號「。」。"
            ),
            paragraph=paragraph,
            start=start,
            end=end,
            details={"expected_ending": "。"},
        )

    # Apply the drawing-introduction and ordered-caption rules independently
    # to every occurrence of a brief-description section.
    for section in document.sections:
        if section.key != "brief_description_of_drawings":
            continue
        content_paragraphs = [
            paragraph_map[index]
            for index in section.paragraph_indices
            if index in paragraph_map
            and _paragraph_visible_content(paragraph_map[index])
        ]
        figure_paragraphs = [
            paragraph
            for paragraph in content_paragraphs
            if _FIGURE_AT_LINE_START.match(
                _paragraph_visible_content(paragraph)
            )
            is not None
        ]
        introduction = next(
            (
                paragraph
                for paragraph in content_paragraphs
                if _FIGURE_AT_LINE_START.match(
                    _paragraph_visible_content(paragraph)
                )
                is None
            ),
            None,
        )
        if (
            introduction is not None
            and not _paragraph_visible_content(introduction).endswith("：")
        ):
            start, end = _ending_highlight_span(introduction)
            yield _issue(
                "PCT003",
                "圖式簡單說明的導言沒有以全形冒號「：」結尾。",
                "請將圖式簡單說明第一段導言的結尾改為全形冒號「：」。",
                paragraph=introduction,
                start=start,
                end=end,
                details={"expected_ending": "："},
            )

        figure_count = len(figure_paragraphs)
        for position, paragraph in enumerate(figure_paragraphs):
            if position == figure_count - 1:
                expected = "。"
            elif figure_count >= 3 and position == figure_count - 2:
                expected = "；及"
            elif figure_count == 2 and position == 0:
                # With only two drawings, the opening caption is also the
                # penultimate caption; the joining form takes precedence.
                expected = "；及"
            else:
                expected = "；"
            content = re.sub(r"\s+", "", _paragraph_visible_content(paragraph))
            if content.endswith(expected):
                continue
            start, end = _ending_highlight_span(paragraph)
            figure_match = _FIGURE_AT_LINE_START.match(
                _paragraph_visible_content(paragraph)
            )
            figure_label = (
                figure_match.group(0).strip()
                if figure_match is not None
                else f"第{position + 1}則圖說"
            )
            yield _issue(
                "PCT004",
                f"{figure_label}的結尾標點不符圖說順序，預期為「{expected}」。",
                f"請將此圖說的結尾改為「{expected}」。",
                paragraph=paragraph,
                start=start,
                end=end,
                details={
                    "figure_position": position + 1,
                    "figure_count": figure_count,
                    "expected_ending": expected,
                },
            )


def _drawing_issues(document: PatentDocument) -> Iterable[PatentIssue]:
    figure_records: List[Tuple[int, PatentParagraph, int, int]] = []
    for paragraph in document.paragraphs:
        if (
            paragraph.is_heading
            or paragraph.section_key != "brief_description_of_drawings"
        ):
            continue
        paragraph_records = []
        for line, line_start, _line_end in _line_spans(paragraph.text):
            match = _FIGURE_AT_LINE_START.match(line)
            if match is None:
                continue
            paragraph_records.append((line_start, match))
            start_number = int(match.group("start"))
            end_text = match.group("end")
            end_number = int(end_text) if end_text else start_number
            low, high = sorted((start_number, end_number))
            for number in range(low, high + 1):
                figure_records.append(
                    (
                        number,
                        paragraph,
                        line_start + match.start(),
                        line_start + match.end(),
                    )
                )
        if paragraph_records and (
            len(paragraph_records) > 1 or paragraph.numbering_value is not None
        ):
            yield _issue(
                "FIG003",
                "主要圖號必須各自位於未編說明書段號的獨立 Word 段落。",
                "請把每個圖N說明拆成獨立段落，並移除該圖說的說明書段號。",
                paragraph=paragraph,
                start=0,
                end=len(paragraph.text),
                details={"main_figure_lines": len(paragraph_records)},
            )

    actual_numbers = [record[0] for record in figure_records]
    for expected, record in enumerate(figure_records, start=1):
        number, paragraph, start, end = record
        if number == expected:
            continue
        yield _issue(
            "FIG001",
            f"圖式簡單說明依序出現圖{number}，此處預期為圖{expected}。",
            "請確認圖號是否由圖1開始且沒有跳號、重複或順序顛倒。",
            paragraph=paragraph,
            start=start,
            end=end,
            details={"expected_number": expected, "actual_number": number},
        )
        break

    described = set(actual_numbers)
    for paragraph in document.paragraphs:
        if paragraph.section_key != "designated_representative_drawing":
            continue
        matches = list(_FIGURE_REFERENCE.finditer(paragraph.text))
        if len(matches) != 1:
            yield _issue(
                "FIG002",
                "指定代表圖必須在同一行明確指定且只能指定一個圖號。",
                "請使用固定格式「【指定代表圖】圖N」。",
                paragraph=paragraph,
                start=0,
                end=len(paragraph.text),
                details={"detected_figure_count": len(matches)},
            )
        for match in matches:
            number = int(match.group("number"))
            if number in described:
                continue
            yield _issue(
                "FIG002",
                f"指定代表圖為圖{number}，但圖式簡單說明沒有列出該圖號。",
                "請確認指定代表圖圖號，或在圖式簡單說明補上對應圖號。",
                paragraph=paragraph,
                start=match.start(),
                end=match.end(),
                details={"representative_figure": number},
            )


def _line_spans(text: str) -> Iterable[Tuple[str, int, int]]:
    offset = 0
    parts = text.splitlines(keepends=True) or ([text] if text else [])
    for part in parts:
        line = part.rstrip("\r\n")
        left = len(line) - len(line.lstrip())
        right = len(line.rstrip())
        if right > left:
            yield line[left:right], offset + left, offset + right
        offset += len(part)


def _canonical_symbol(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return re.sub(r"\s+", "", normalized).replace("，", ",").replace("′", "'")


def _canonical_name(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))


def _phrase_spans_ignoring_whitespace(
    text: str,
    phrase: str,
) -> List[Tuple[int, int]]:
    canonical = _canonical_name(phrase)
    if not canonical:
        return []
    pattern = re.compile(r"\s*".join(re.escape(character) for character in canonical))
    return [(match.start(), match.end()) for match in pattern.finditer(text)]


def _patent_target_names(title: str) -> set[str]:
    """Expand a Chinese title into the target aliases used in embodiments."""

    canonical = _canonical_name(title)
    if not canonical:
        return set()
    targets = {canonical}

    # Coordinated targets are routinely referenced separately, e.g.
    # 「培林及前叉裝置」->「培林」and「前叉裝置」.
    targets.update(
        part
        for part in re.split(r"(?:以及|及|與|和|、)", canonical)
        if part
    )

    # A「具有 X 的 Y」title may later refer to X and Y independently.
    # Keep literal phrases only; exact component-list names are handled by the
    # caller's component-priority exception.
    modifier_match = re.fullmatch(
        r"(?:具有|具|含有)(?P<inner>.+?)(?:的|之)(?P<head>.+)",
        canonical,
    )
    if modifier_match is not None:
        targets.add(modifier_match.group("inner"))
        targets.add(modifier_match.group("head"))
    return {target for target in targets if target}


def _target_name_label_intrusion_issues(
    document: PatentDocument,
    labels_by_name: Dict[str, set[str]],
) -> Iterable[PatentIssue]:
    """Detect a registered label inserted into the complete patent title.

    A component can be used legally as ``該平板5`` while the complete target
    must remain ``平板觸控裝置``.  Reconstructing the known title after
    removing one registered label distinguishes those two cases without
    disabling ordinary component-label checking.
    """

    title = _canonical_name(document.patent_title)
    if not title or not labels_by_name:
        return

    def spaced_literal(value: str) -> str:
        return r"\s*".join(re.escape(character) for character in value)

    patterns: List[Tuple[re.Pattern[str], str, str]] = []
    for component_name in sorted(labels_by_name, key=len, reverse=True):
        # Preserve the established component-priority rule when the patent
        # title itself is exactly the registered component name.  REF006 only
        # protects a larger complete title containing that component.
        if not component_name or component_name == title:
            continue
        for occurrence in re.finditer(re.escape(component_name), title):
            prefix = title[:occurrence.start()]
            suffix = title[occurrence.end():]
            for label in sorted(
                labels_by_name[component_name],
                key=len,
                reverse=True,
            ):
                canonical_label = _canonical_symbol(label)
                if not canonical_label:
                    continue
                label_literal = spaced_literal(canonical_label)
                pattern = re.compile(
                    spaced_literal(prefix)
                    + (r"\s*" if prefix else "")
                    + spaced_literal(component_name)
                    + r"\s*"
                    + rf"(?P<label>(?:{label_literal})|"
                    + rf"(?:[（(]\s*{label_literal}\s*[）)]))"
                    + (r"\s*" if suffix else "")
                    + spaced_literal(suffix)
                )
                patterns.append((pattern, component_name, canonical_label))

    if not patterns:
        return

    excluded_sections = {
        "invention_title",
        "utility_model_title",
        "english_title",
        *_SYMBOL_SECTIONS,
    }
    emitted: set[Tuple[int, int, int]] = set()
    for paragraph in document.paragraphs:
        if paragraph.is_heading or paragraph.section_key in excluded_sections:
            continue
        text = unicodedata.normalize("NFKC", paragraph.text).replace("′", "'")
        candidates = []
        for pattern, component_name, label in patterns:
            for match in pattern.finditer(text):
                candidates.append(
                    (
                        match.start(),
                        match.end(),
                        -len(component_name),
                        match.start("label"),
                        match.end("label"),
                        component_name,
                        label,
                    )
                )
        for (
            _target_start,
            _target_end,
            _negative_name_length,
            label_start,
            label_end,
            component_name,
            label,
        ) in sorted(candidates):
            key = (paragraph.index, label_start, label_end)
            if key in emitted:
                continue
            emitted.add(key)
            yield _issue(
                "REF006",
                (
                    f"完整標的名稱「{document.patent_title}」中插入了元件"
                    f"「{component_name}」的標號「{label}」。"
                ),
                (
                    "請刪除完整標的名稱中的元件標號；只有在獨立指稱"
                    f"該元件（例如「該{component_name}{label}」）時才保留標號。"
                ),
                paragraph=paragraph,
                start=label_start,
                end=label_end,
                details={
                    "patent_title": document.patent_title,
                    "component_name": component_name,
                    "inserted_label": label,
                },
            )


def _match_symbol_line(text: str):
    return _SYMBOL_LINE.match(text) or _SYMBOL_SPACE_LINE.match(text)


def _symbol_issues(document: PatentDocument) -> Iterable[PatentIssue]:
    from .symbol_transfer import _expand_expression, _table_symbol_candidates

    records: Dict[str, List[Dict[str, object]]] = defaultdict(list)
    skipped_numbered_symbol_intro = False
    table_candidates, consumed_table_paragraphs = _table_symbol_candidates(
        document,
        _SYMBOL_SECTIONS,
    )
    for paragraph in document.paragraphs:
        if paragraph.is_heading or paragraph.section_key not in _SYMBOL_SECTIONS:
            continue
        if paragraph.index in consumed_table_paragraphs:
            continue
        if (
            paragraph.section_key == "drawing_symbol_description"
            and paragraph.numbering_value is not None
            and not skipped_numbered_symbol_intro
            and not records["drawing_symbol_description"]
        ):
            # The first numbered paragraph introduces the symbol list; the
            # following unnumbered paragraphs contain the actual entries.
            skipped_numbered_symbol_intro = True
            continue
        if paragraph.text.strip() in _NON_SYMBOL_SEPARATORS:
            continue
        lines = list(_line_spans(paragraph.text))
        valid_line_matches = [
            _match_symbol_line(line)
            for line, _line_start, _line_end in lines
        ]
        for (line, line_start, line_end), match in zip(lines, valid_line_matches):
            if match is None:
                # Number/separator formatting is deliberately not reviewed.
                continue
            symbol_expression = _canonical_symbol(match.group("symbol"))
            name = match.group("name").strip()
            symbol_start = line_start + match.start("symbol")
            symbol_end = line_start + match.end("symbol")
            if not name:
                yield _issue(
                    "SYM001",
                    f"符號「{symbol_expression}」沒有元件名稱內容。",
                    "請確認此符號所代表的元件名稱並補入內容。",
                    paragraph=paragraph,
                    start=line_start,
                    end=line_end,
                )
                continue
            expanded, error = _expand_expression(symbol_expression)
            if error:
                continue
            for symbol, _raw_symbol in expanded:
                records[paragraph.section_key].append(
                    {
                        "symbol": symbol,
                        "name": name,
                        "canonical_name": _canonical_name(name),
                        "paragraph": paragraph,
                        "start": symbol_start,
                        "end": symbol_end,
                    }
                )

    for candidate in table_candidates:
        expanded, error = _expand_expression(candidate.expression)
        if error:
            continue
        for symbol, _raw_symbol in expanded:
            records[candidate.section_key].append(
                {
                    "symbol": symbol,
                    "name": candidate.name,
                    "canonical_name": _canonical_name(candidate.name),
                    "paragraph": candidate.symbol_paragraph,
                    "start": candidate.char_start,
                    "end": candidate.char_end,
                }
            )

    for section_key, section_records in records.items():
        seen: Dict[str, Dict[str, object]] = {}
        for record in section_records:
            symbol = str(record["symbol"])
            previous = seen.get(symbol)
            if previous is None:
                seen[symbol] = record
                continue
            paragraph = record["paragraph"]
            if previous["canonical_name"] == record["canonical_name"]:
                yield _issue(
                    "SYM006",
                    f"符號「{symbol}」在同一份符號說明中重複列出。",
                    "請保留一筆正確的符號說明並刪除重複項目。",
                    paragraph=paragraph,
                    start=int(record["start"]),
                    end=int(record["end"]),
                    details={"previous_paragraph_index": previous["paragraph"].index},
                )
            else:
                yield _issue(
                    "SYM003",
                    f"符號「{symbol}」同時對應「{previous['name']}」與「{record['name']}」。",
                    "請確認正確元件名稱，並統一所有相同符號的說明。",
                    paragraph=paragraph,
                    start=int(record["start"]),
                    end=int(record["end"]),
                    details={
                        "previous_name": previous["name"],
                        "previous_paragraph_index": previous["paragraph"].index,
                    },
                )

    full_map = {
        str(record["symbol"]): record
        for record in records.get("drawing_symbol_description", [])
    }
    for representative in records.get("representative_drawing_symbols", []):
        symbol = str(representative["symbol"])
        paragraph = representative["paragraph"]
        full = full_map.get(symbol)
        if full is None:
            yield _issue(
                "SYM004",
                f"代表圖符號「{symbol}」未出現在完整符號說明。",
                "請在符號說明補入該符號，或修正代表圖符號。",
                paragraph=paragraph,
                start=int(representative["start"]),
                end=int(representative["end"]),
            )
        elif full["canonical_name"] != representative["canonical_name"]:
            yield _issue(
                "SYM005",
                f"符號「{symbol}」在代表圖寫作「{representative['name']}」，完整符號說明則寫作「{full['name']}」。",
                "請確認並統一兩處元件名稱。",
                paragraph=paragraph,
                start=int(representative["start"]),
                end=int(representative["end"]),
                details={"full_description_name": full["name"]},
            )


def _embodiment_local_component_names(
    document: PatentDocument,
    registered_names: Iterable[str],
) -> set[str]:
    """Return conservatively proven embodiment-only component terms."""

    registered = set(registered_names)
    embodiment_text = "\n".join(
        unicodedata.normalize("NFKC", paragraph.text).replace("′", "'")
        for paragraph in document.paragraphs
        if paragraph.section_key == "embodiments" and not paragraph.is_heading
    )
    local_names: set[str] = set()
    for reference in _LOCAL_COMPONENT_REFERENCE_PATTERN.finditer(embodiment_text):
        local_name = reference.group("name")
        if local_name in registered:
            continue
        introduction_pattern = re.compile(
            rf"(?<![第該])(?:{_COMPONENT_QUANTIFIER_EXPRESSION})"
            rf"(?:[^，,；;。\r\n]{{0,48}}?(?:的|之))?"
            rf"{re.escape(local_name)}"
        )
        if introduction_pattern.search(embodiment_text, 0, reference.start()):
            local_names.add(local_name)
    return local_names


def _reference_issues(
    document: PatentDocument,
    protected_phrases: Sequence[str] = (),
) -> Iterable[PatentIssue]:
    """Compare implementation name/label pairs with the complete list."""

    # Import locally to keep the rule model independent during package startup.
    from .symbol_transfer import extract_document_symbols

    transfer = extract_document_symbols(document)
    expected_by_name: Dict[str, set[str]] = defaultdict(set)
    defined_labels = set()
    for entry in transfer.full_entries:
        canonical_name = _canonical_name(entry.name)
        if canonical_name:
            expected_by_name[canonical_name].add(entry.label)
        defined_labels.add(entry.label)
    if not defined_labels:
        return

    yield from _target_name_label_intrusion_issues(
        document,
        expected_by_name,
    )

    emitted: set[Tuple[str, int, int, str]] = set()

    def emit_once(
        rule_id: str,
        paragraph: PatentParagraph,
        start: int,
        end: int,
        message: str,
        suggestion: str,
        **details,
    ) -> Optional[PatentIssue]:
        key = (rule_id, paragraph.index, start, message)
        if key in emitted:
            return None
        emitted.add(key)
        return _issue(
            rule_id,
            message,
            suggestion,
            paragraph=paragraph,
            start=start,
            end=end,
            details=details,
        )

    known_names = sorted(expected_by_name, key=len, reverse=True)
    patent_target_names = _patent_target_names(document.patent_title)
    protected_target_names = sorted(
        (
            target
            for target in patent_target_names
            # Component-list identity has priority over target protection.
            if target not in expected_by_name
        ),
        key=len,
        reverse=True,
    )
    defined_label_pattern = "|".join(
        re.escape(label)
        for label in sorted(defined_labels, key=len, reverse=True)
    )
    component_determiner_pattern = re.compile(
        rf"(?:(?:{_COMPONENT_REFERENCE_EXPRESSION})|"
        rf"(?<!第)(?:{_COMPONENT_QUANTIFIER_EXPRESSION}))"
        rf"(?:[^，,；;。\r\n]{{0,48}}?(?:的|之))?\s*$"
    )

    def has_component_determiner(text: str, name_start: int) -> bool:
        prefix = text[max(0, name_start - 80):name_start]
        return component_determiner_pattern.search(prefix) is not None
    # An embodiment may introduce a local term that is intentionally absent
    # from both the claims and symbol list.  Detect it conservatively only when
    # the same longer term is first introduced with a quantity and later used
    # with「該／該等／每一該」.  This mainly prevents a registered short name
    # embedded inside that local term from producing a false REF004.
    local_component_names = _embodiment_local_component_names(
        document,
        expected_by_name,
    )

    for paragraph in document.paragraphs:
        if (
            paragraph.section_key != "embodiments"
            or paragraph.is_heading
        ):
            continue
        text = unicodedata.normalize("NFKC", paragraph.text).replace("′", "'")
        patent_target_spans = [
            span
            for target in protected_target_names
            for span in _phrase_spans_ignoring_whitespace(text, target)
        ]
        protected_instance_spans = [
            match.span() for match in _EMBODIMENT_INSTANCE_PATTERN.finditer(text)
        ]
        local_component_spans = [
            (match.start(), match.end(), local_name)
            for local_name in local_component_names
            for match in re.finditer(re.escape(local_name), text)
        ]
        protected_phrase_spans = [
            span
            for phrase in protected_phrases
            for span in _phrase_spans_ignoring_whitespace(text, phrase)
        ]

        # Strong comparison: an exact name from the list is followed by a
        # label.  Resolve overlapping names by longest complete name first, so
        # ``第一齒輪11`` belongs to ``第一齒輪`` instead of also producing a
        # false ``齒輪 -> 11`` mismatch.
        candidates: List[Tuple[int, int, int, str, re.Match[str]]] = []
        for name in known_names:
            pattern = re.compile(
                re.escape(name)
                + r"\s*(?:[（(][^（）()\r\n]{1,40}[）)]\s*)?"
                + rf"[（(]?\s*(?P<label>{defined_label_pattern})"
                + r"(?![0-9A-Za-z'])\s*[）)]?"
            )
            for match in pattern.finditer(text):
                candidates.append(
                    (match.start(), match.end(), len(name), name, match)
                )

        selected: List[Tuple[int, int, int, str, re.Match[str]]] = []
        for candidate in sorted(
            candidates,
            key=lambda item: (-item[2], item[0], item[1]),
        ):
            start, end = candidate[0], candidate[1]
            if any(
                chosen_start < end and chosen_end > start
                for chosen_start, chosen_end, _length, _name, _match in selected
            ):
                continue
            selected.append(candidate)

        for _start, _end, _length, name, match in sorted(
            selected, key=lambda item: item[0]
        ):
            label = _canonical_symbol(match.group("label"))
            expected = expected_by_name[name]
            label_start = match.start("label")
            label_end = match.end("label")
            if label not in defined_labels:
                issue = emit_once(
                    "REF001",
                    paragraph,
                    label_start,
                    label_end,
                    f"內文使用標號「{label}」，但完整符號說明沒有此標號。",
                    "請確認標號是否正確；若為圖式元件，請補入完整符號說明。",
                    label=label,
                    component_name=name,
                )
                if issue is not None:
                    yield issue
            if label not in expected:
                issue = emit_once(
                    "REF002",
                    paragraph,
                    match.start(),
                    match.end(),
                    f"元件「{name}」在完整符號說明對應「{'、'.join(sorted(expected))}」，此處卻寫作「{label}」。",
                    "請回到 Word 確認此處的元件名稱與標號是否配對正確。",
                    label=label,
                    component_name=name,
                    expected_labels=sorted(expected),
                )
                if issue is not None:
                    yield issue

        labelled_occurrences = {
            (match.start(), name)
            for _start, _end, _length, name, match in selected
        }
        name_candidates: List[Tuple[int, int, int, str]] = []
        for name in known_names:
            for match in re.finditer(re.escape(name), text):
                name_candidates.append(
                    (match.start(), match.end(), len(name), name)
                )
        selected_names: List[Tuple[int, int, int, str]] = []
        for candidate in sorted(
            name_candidates,
            key=lambda item: (-item[2], item[0], item[1]),
        ):
            start, end = candidate[0], candidate[1]
            if any(
                chosen_start < end and chosen_end > start
                for chosen_start, chosen_end, _length, _name in selected_names
            ):
                continue
            selected_names.append(candidate)

        for start, end, _length, name in sorted(
            selected_names, key=lambda item: item[0]
        ):
            if (start, name) in labelled_occurrences:
                continue
            if not has_component_determiner(text, start):
                continue
            if name not in patent_target_names and any(
                title_start <= start and end <= title_end
                for title_start, title_end in patent_target_spans
            ):
                continue
            if any(
                protected_start <= start and end <= protected_end
                for protected_start, protected_end in protected_instance_spans
            ):
                continue
            if any(
                local_start <= start
                and end <= local_end
                and local_name != name
                for local_start, local_end, local_name in local_component_spans
            ):
                continue
            if any(
                phrase_start <= start and end <= phrase_end
                for phrase_start, phrase_end in protected_phrase_spans
            ):
                # A document-specific fuzzy-match whitelist protects the
                # complete phrase, not merely an exact candidate name.  Thus
                # a registered short component such as「建築」embedded in the
                # whitelisted phrase「建築物結構檢查報告」does not create a
                # missing-label error.  Occurrences outside the phrase remain
                # subject to the normal REF004 rule.
                continue
            issue = emit_once(
                "REF004",
                paragraph,
                start,
                end,
                f"實施方式中的元件「{name}」後方沒有標號。",
                "請在此元件名稱後補上完整符號說明所列的標號。",
                component_name=name,
                expected_labels=sorted(expected_by_name[name]),
            )
            if issue is not None:
                yield issue

        # Low-risk fallback for previously undefined component-like names.
        for match in _COMPONENT_REFERENCE.finditer(text):
            name = match.group("name")
            label = _canonical_symbol(match.group("label"))
            if label in defined_labels or not name.endswith(_COMPONENT_NAME_SUFFIXES):
                continue
            issue = emit_once(
                "REF001",
                paragraph,
                match.start("label"),
                match.end("label"),
                f"內文使用標號「{label}」，但完整符號說明沒有此標號。",
                "此規則採保守文字比對；請人工確認它是否為圖式元件標號。",
                label=label,
                component_name=name,
                confidence="conservative_text_match",
            )
            if issue is not None:
                yield issue


def _component_name_typo_issues(
    document: PatentDocument,
    similarity_whitelist: Sequence[str] = (),
) -> Iterable[PatentIssue]:
    """Warn about one-character substitutions of registered component names."""

    from .symbol_transfer import extract_document_symbols

    transfer = extract_document_symbols(document)
    labels_by_name: Dict[str, set[str]] = defaultdict(set)
    for entry in transfer.full_entries:
        name = _canonical_name(entry.name)
        if len(name) >= 2 and re.fullmatch(r"[\u3400-\u9fff]+", name):
            labels_by_name[name].add(entry.label)
    registered_names = set(labels_by_name)
    if not registered_names:
        return
    protected_target_names = sorted(
        (
            target
            for target in _patent_target_names(document.patent_title)
            if target not in registered_names
        ),
        key=len,
        reverse=True,
    )
    ignored_names = {
        _canonical_name(value)
        for value in similarity_whitelist
        if _canonical_name(value)
    }
    local_component_names = _embodiment_local_component_names(
        document,
        registered_names,
    )
    expected_metadata = [
        (name, len(name), Counter(name))
        for name in sorted(registered_names, key=len, reverse=True)
        if name not in ignored_names
    ]

    @lru_cache(maxsize=16384)
    def candidate_is_eligible(candidate: str) -> bool:
        # These checks depend only on the candidate and this review's fixed
        # symbol/whitelist sets, not its paragraph or expected comparison name.
        # Repeated windows previously scanned every registered name again.
        # Keep the cache bounded and local so edited documents/rules never
        # inherit stale eligibility from a previous review.
        if candidate in ignored_names:
            return False
        return not (
            candidate in registered_names
            or any(candidate in registered for registered in registered_names)
            or not all("\u3400" <= character <= "\u9fff" for character in candidate)
        )

    for paragraph in document.paragraphs:
        if (
            paragraph.is_heading
            or not paragraph.text.strip()
        ):
            continue
        text = unicodedata.normalize("NFKC", paragraph.text)
        compact_positions = [
            index for index, character in enumerate(text) if not character.isspace()
        ]
        compact_text = "".join(text[index] for index in compact_positions)
        protected_spans = [
            span
            for target in protected_target_names
            for span in _phrase_spans_ignoring_whitespace(compact_text, target)
        ]
        if paragraph.section_key == "embodiments":
            protected_spans.extend(
                match.span()
                for match in _EMBODIMENT_INSTANCE_PATTERN.finditer(compact_text)
            )
            protected_spans.extend(
                match.span()
                for local_name in local_component_names
                for match in re.finditer(re.escape(local_name), compact_text)
            )
        exact_spans = [
            (match.start(), match.end())
            for name in registered_names
            for match in re.finditer(re.escape(name), compact_text)
        ]
        # These two lookups replace repeated linear interval scans for every
        # candidate window while preserving the exact containment/overlap
        # semantics used by the original implementation.
        enclosing_end = [-1] * (len(compact_text) + 1)
        for left, right in protected_spans:
            if 0 <= left <= len(compact_text):
                enclosing_end[left] = max(enclosing_end[left], right)
        for position in range(1, len(enclosing_end)):
            enclosing_end[position] = max(
                enclosing_end[position],
                enclosing_end[position - 1],
            )

        exact_delta = [0] * (len(compact_text) + 1)
        for left, right in exact_spans:
            exact_delta[left] += 1
            exact_delta[right] -= 1
        exact_prefix = [0] * (len(compact_text) + 1)
        coverage = 0
        for position in range(len(compact_text)):
            coverage += exact_delta[position]
            exact_prefix[position + 1] = (
                exact_prefix[position] + (1 if coverage else 0)
            )

        findings: Dict[Tuple[int, int, str], set[str]] = defaultdict(set)
        finding_modes: Dict[Tuple[int, int, str], set[str]] = defaultdict(set)
        for expected, length, expected_counts in expected_metadata:
            for start in range(0, len(compact_text) - length + 1):
                end = start + length
                if enclosing_end[start] >= end:
                    continue
                if exact_prefix[end] != exact_prefix[start]:
                    continue
                candidate = compact_text[start:end]
                if not candidate_is_eligible(candidate):
                    continue

                difference_count = 0
                difference_index = -1
                for index, (actual, wanted) in enumerate(
                    zip(candidate, expected)
                ):
                    if actual == wanted:
                        continue
                    difference_count += 1
                    difference_index = index
                positional_one_character = difference_count == 1

                if positional_one_character:
                    missing_count = 1
                    additional_count = 1
                    missing_character = expected[difference_index]
                    additional_character = candidate[difference_index]
                else:
                    character_delta = dict(expected_counts)
                    for character in candidate:
                        character_delta[character] = (
                            character_delta.get(character, 0) - 1
                        )
                    missing_count = sum(
                        count for count in character_delta.values() if count > 0
                    )
                    additional_count = -sum(
                        count for count in character_delta.values() if count < 0
                    )
                    missing_character = next(
                        (
                            character
                            for character, count in character_delta.items()
                            if count > 0
                        ),
                        "",
                    )
                    additional_character = next(
                        (
                            character
                            for character, count in character_delta.items()
                            if count < 0
                        ),
                        "",
                    )
                reordered_exact = (
                    candidate != expected
                    and missing_count == 0
                    and additional_count == 0
                )
                reordered_one_character = (
                    missing_count == 1
                    and additional_count == 1
                )
                if (
                    not (
                        positional_one_character
                        or reordered_exact
                        or reordered_one_character
                    )
                ):
                    continue
                legal_variant_characters = (
                    missing_count == 1
                    and additional_count == 1
                    and {
                        missing_character,
                        additional_character,
                    }
                    == {"主", "副"}
                )
                if legal_variant_characters:
                    continue
                before = compact_text[max(0, start - 8):start]
                after = compact_text[end:end + 12]
                has_reference_context = re.search(
                    rf"(?:{_COMPONENT_REFERENCE_EXPRESSION}|"
                    rf"{_COMPONENT_QUANTIFIER_EXPRESSION})$",
                    before,
                ) is not None
                has_label_context = any(
                    re.match(
                        rf"\s*[（(]?\s*{re.escape(label)}(?![0-9A-Za-z'])",
                        after,
                    )
                    for label in labels_by_name[expected]
                )
                has_left_component_boundary = start == 0 or re.search(
                    r"(?:[，,；;。:：、]|包含|包括|具有|以及|及|與|和|或|且|並)$",
                    before,
                ) is not None
                has_right_component_boundary = end == len(compact_text) or re.match(
                    r"(?:係|為|是|可|能|會|具有|包含|包括|設有|配置|形成|"
                    r"設置|位於|連接|結合|對應|抵接|鄰接|朝|沿|用以|用於|"
                    r"與|及|之|的|[，,；;。:：、])",
                    after,
                ) is not None
                if (
                    (reordered_exact or reordered_one_character)
                    and not positional_one_character
                    and not (
                        has_reference_context
                        or has_label_context
                        or (
                            has_left_component_boundary
                            and has_right_component_boundary
                        )
                    )
                ):
                    continue
                if length == 2:
                    if not (has_reference_context or has_label_context):
                        continue
                original_start = compact_positions[start]
                original_end = compact_positions[end - 1] + 1
                finding_key = (original_start, original_end, candidate)
                findings[finding_key].add(expected)
                if reordered_exact:
                    finding_modes[finding_key].add("reordered_exact")
                elif reordered_one_character and not positional_one_character:
                    finding_modes[finding_key].add("reordered_one_character")
                else:
                    finding_modes[finding_key].add("one_character")

        for (start, end, candidate), expected_names in sorted(findings.items()):
            expected_text = "／".join(sorted(expected_names))
            modes = finding_modes[(start, end, candidate)]
            if modes & {"reordered_exact", "reordered_one_character"}:
                message = (
                    f"「{candidate}」與元件名稱「{expected_text}」的字序疑似調換，"
                    "或重新排列後僅差一個字，可能是輸入錯誤。"
                )
            else:
                message = (
                    f"「{candidate}」與元件名稱「{expected_text}」僅差一個字，"
                    "可能是輸入錯誤。"
                )
            yield _issue(
                "REF005",
                message,
                "請回到 Word 核對符號說明及此處的元件名稱。",
                paragraph=paragraph,
                start=start,
                end=end,
                details={
                    "candidate": candidate,
                    "expected_component_names": sorted(expected_names),
                    "similarity_modes": sorted(modes),
                },
            )


def _claim_prefix(text: str) -> Optional[re.Match[str]]:
    return _CLAIM_PREFIX.match(text)


def _claim_dependencies(text: str, body_start: int) -> List[Tuple[List[int], int, int]]:
    body = text[body_start:]

    def parse_fragment(
        fragment: str,
        fragment_offset: int,
    ) -> List[Tuple[List[int], int, int]]:
        dependencies: List[Tuple[List[int], int, int]] = []
        occupied: List[Tuple[int, int]] = []
        for match in _CLAIM_RANGE.finditer(fragment):
            start_number = int(match.group("start"))
            end_number = int(match.group("end"))
            low, high = sorted((start_number, end_number))
            dependencies.append(
                (
                    list(range(low, high + 1)),
                    fragment_offset + match.start(),
                    fragment_offset + match.end(),
                )
            )
            occupied.append((match.start(), match.end()))
        for match in _CLAIM_LIST.finditer(fragment):
            if any(start <= match.start() < end for start, end in occupied):
                continue
            numbers = [
                int(value) for value in re.findall(r"\d+", match.group("numbers"))
            ]
            dependencies.append(
                (
                    numbers,
                    fragment_offset + match.start(),
                    fragment_offset + match.end(),
                )
            )
            occupied.append((match.start(), match.end()))
        for match in _CLAIM_SINGLE.finditer(fragment):
            if any(start <= match.start() < end for start, end in occupied):
                continue
            dependencies.append(
                (
                    [int(match.group("number"))],
                    fragment_offset + match.start(),
                    fragment_offset + match.end(),
                )
            )
        return dependencies

    # The legally operative dependency is normally the citation between
    # 「如」and「所述」.  Prefer these bounded citations so later explanatory
    # mentions of claim numbers do not become false dependencies.
    cited_dependencies: List[Tuple[List[int], int, int]] = []
    for citation in _CLAIM_AS_DESCRIBED.finditer(body):
        fragment = citation.group("citation")
        cited_dependencies.extend(
            parse_fragment(
                fragment,
                body_start + citation.start("citation"),
            )
        )
    if cited_dependencies:
        return cited_dependencies

    # Retain the older fallback for valid forms such as「依據請求項1」that do
    # not contain an explicit「如…所述」construction.
    return parse_fragment(body, body_start)


_CLAIM_DUPLICATE_WIDTH_MAP = {
    code: chr(code - 0xFEE0) for code in range(0xFF01, 0xFF5F)
}
_CLAIM_DUPLICATE_WIDTH_MAP.update({0x3000: " ", 0x2032: "'", 0x2019: "'"})


def _duplicate_claim_key(body: str) -> str:
    """Normalize layout, not technical meaning (case, powers, numbers, etc.)."""

    # Full NFKC would collapse e.g. m² into m2; restrict normalization to width.
    text = body.translate(_CLAIM_DUPLICATE_WIDTH_MAP)

    def layout_space(match: re.Match) -> str:
        left = text[match.start() - 1] if match.start() else ""
        right = text[match.end()] if match.end() < len(text) else ""
        # Preserve token boundaries in English/technical values: a b != ab.
        if left.isascii() and left.isalnum() and right.isascii() and right.isalnum():
            return " "
        return ""

    return re.sub(r"\s+", layout_space, text)


def _duplicate_claim_issues(entries: Sequence[Dict[str, object]]) -> Iterable[PatentIssue]:
    """Compare already parsed full claim bodies, grouping identical items once."""

    groups: Dict[str, List[Dict[str, object]]] = defaultdict(list)
    for entry in entries:
        key = _duplicate_claim_key(str(entry["body"]))
        if key:
            groups[key].append(entry)
    for duplicates in groups.values():
        if len(duplicates) < 2:
            continue
        numbers = [int(entry["number"]) for entry in duplicates]
        # Locate the first repeated item rather than flagging the original.
        entry = duplicates[1]
        paragraph = entry["paragraph"]
        prefix = None if entry["auto_numbered"] else _claim_prefix(paragraph.text)
        start = prefix.end() if prefix is not None else 0
        separator = re.match(r"\s*[:：、.．]\s*", paragraph.text[start:])
        if separator is not None:
            start += separator.end()
        yield _issue(
            "CLM018",
            f"請求項 {'、'.join(map(str, numbers))} 的完整內文重複"
            "（已忽略自身項次、排版空白及等價全半形字元）。",
            "請回原始 Word 確認是否為重複貼上或漏寫差異；"
            "本提醒不判定各請求項的法律範圍是否相同。",
            paragraph=paragraph,
            start=start,
            end=len(paragraph.text),
            details={
                "claim_number": int(entry["number"]),
                "duplicate_claim_numbers": numbers,
                "paragraph_indices": [
                    source.index for source in entry["source_paragraphs"]
                ],
                "highlight_text": str(entry["body"]),
                "body_offset": 0,
            },
        )


def _claim_disclosure_issues(
    document: PatentDocument,
    entries: Sequence[Dict[str, object]],
    component_names: Sequence[str],
    subjects: Dict[int, str],
    report_out: Optional[Dict[str, object]] = None,
) -> Iterable[PatentIssue]:
    """Adapt evidence-based coverage results to the existing review UI."""

    from .claim_coverage import analyze_claim_disclosure

    first = next((entry for entry in entries if int(entry["number"]) == 1), None)
    paragraphs = [p for p in document.paragraphs if p.section_key == "disclosure"]
    body = str(first["body"]) if first is not None else ""
    report = analyze_claim_disclosure(
        body,
        paragraphs,
        component_names=component_names,
        subject_names=[name for name in (subjects.get(1, ""), document.patent_title) if name],
    )
    report["claim_number"] = 1
    report["section_key"] = "disclosure"
    if report_out is not None:
        report_out.update(report)
    # Existing structure/empty-claim rules already handle an absent claim 1.
    if first is None or not body.strip():
        return
    anchor = first["paragraph"]
    if report.get("status") == "unavailable":
        limitations = report.get("limitations", [])
        explanation = str(limitations[-1]) if limitations else "請確認兩個章節的文字均可擷取。"
        issue = _issue(
            "CLM019",
            "請求項1與發明／新型內容尚未完成對應檢核。" + explanation,
            "請確認發明／新型內容章節及請求項1均可擷取；未完成檢核不代表已涵蓋。",
            paragraph=anchor,
            start=0,
            end=len(anchor.text),
            details={"claim_number": 1, "coverage_status": "unavailable", "coverage_limitations": report.get("limitations", [])},
        )
        issue.severity = "info"
        yield issue
        return
    status_titles = {
        "possible_gap": "未找到足夠對應內容",
        "conflict": "對應敘述可能不一致",
        "uncertain": "改寫或語意需人工確認",
    }
    uncertain_issues: List[PatentIssue] = []
    for item in report.get("items", []):
        status = item.get("status", "uncertain")
        if status == "covered":
            continue
        start = max(0, min(len(body), int(item.get("claim_start", 0))))
        end = max(start, min(len(body), int(item.get("claim_end", len(body)))))
        fragment = body[start:end]
        preview = re.sub(r"\s+", "", fragment)
        if len(preview) > 70:
            preview = preview[:67] + "…"
        reason = str(item.get("reason", "請對照原文確認此技術內容是否已記載。"))
        source_paragraph = anchor
        source_start, source_end = 0, len(anchor.text)
        for span in first.get("body_source_spans", []):
            if span["body_start"] <= start < span["body_end"]:
                source_paragraph = span["paragraph"]
                source_start = span["source_start"] + start - span["body_start"]
                source_end = span["source_start"] + min(end, span["body_end"]) - span["body_start"]
                break
        issue = _issue(
            "CLM019",
            f"請求項1「{preview}」：{status_titles.get(status, status_titles['uncertain'])}。{reason}",
            "請對照下方請求項1原文及發明／新型內容的候選證據，回原始 Word 確認；"
            "其他章節的敘述不會抵銷本項提醒，程式不自動判斷法律支持性。",
            paragraph=source_paragraph,
            start=source_start,
            end=source_end,
            details={
                "claim_number": 1,
                "coverage_status": status,
                "coverage_item": item,
                "coverage_limitations": report.get("limitations", []),
                "highlight_text": fragment,
                "body_offset": len(re.sub(r"\s+", "", body[:start])),
            },
        )
        if status == "uncertain":
            issue.severity = "info"
            uncertain_issues.append(issue)
        else:
            yield issue
    if len(uncertain_issues) == 1:
        yield uncertain_issues[0]
    elif uncertain_issues:
        first_issue = uncertain_issues[0]
        details = dict(first_issue.details)
        details.pop("coverage_item", None)
        details["coverage_items"] = [
            dict(issue.details["coverage_item"], body_offset=issue.details["body_offset"])
            for issue in uncertain_issues
        ]
        grouped = _issue(
            "CLM019",
            f"請求項1有 {len(uncertain_issues)} 處改寫或語意需人工確認；點選可查看全部待確認片段與候選原文。",
            first_issue.suggestion,
            paragraph=next((p for p in document.paragraphs if p.index == first_issue.paragraph_index), anchor),
            start=first_issue.char_start,
            end=first_issue.char_end,
            details=details,
        )
        grouped.severity = "info"
        yield grouped


def _claim_issues(
    document: PatentDocument,
    detected_subjects: Optional[List[str]] = None,
    coverage_report: Optional[Dict[str, object]] = None,
) -> Iterable[PatentIssue]:
    from .symbol_transfer import extract_document_symbols

    claim_paragraphs = [
        paragraph
        for paragraph in document.paragraphs
        if paragraph.source_kind != "table"
        and not paragraph.is_heading
        and (
            paragraph.major_section_key == "claims"
            or paragraph.section_key == "claims"
        )
    ]
    chunks: List[Dict[str, object]] = []
    stream_parts: List[str] = []
    stream_offset = 0
    for paragraph in claim_paragraphs:
        numbering = unicodedata.normalize(
            "NFKC", paragraph.numbering_text.strip()
        )
        auto = _AUTO_CLAIM_NUMBER.fullmatch(numbering)
        synthetic = f"請求項{auto.group('number')}" if auto is not None else ""
        chunk_text = synthetic + paragraph.text
        chunks.append(
            {
                "paragraph": paragraph,
                "start": stream_offset,
                "text_start": stream_offset + len(synthetic),
                "end": stream_offset + len(chunk_text),
                "synthetic_end": stream_offset + len(synthetic),
                "auto_number": int(auto.group("number")) if auto is not None else None,
            }
        )
        stream_parts.append(chunk_text)
        stream_offset += len(chunk_text)
        stream_parts.append("\n")
        stream_offset += 1
    stream = "".join(stream_parts)

    marker_candidates: List[Dict[str, object]] = []
    for match in _CLAIM_TOKEN.finditer(stream):
        chunk = next(
            (
                item
                for item in chunks
                if int(item["start"]) <= match.start() <= int(item["end"])
            ),
            None,
        )
        if chunk is None:
            continue
        paragraph = chunk["paragraph"]
        is_auto = (
            chunk["auto_number"] is not None
            and match.start() < int(chunk["synthetic_end"])
        )
        local_start = max(0, match.start() - int(chunk["text_start"]))
        local_end = max(local_start, match.end() - int(chunk["text_start"]))
        is_text_prefix = (
            not is_auto
            and _claim_prefix(paragraph.text) is not None
            and local_start == _claim_prefix(paragraph.text).start()
        )
        preceding_text = stream[:match.start()].rstrip()
        is_inline_boundary = bool(preceding_text) and preceding_text.endswith("。")
        marker_candidates.append(
            {
                "number": int(match.group("number")),
                "start": match.start(),
                "end": match.end(),
                "paragraph": paragraph,
                "number_start": max(
                    0, match.start("number") - int(chunk["text_start"])
                ),
                "number_end": max(
                    0, match.end("number") - int(chunk["text_start"])
                ),
                "local_start": local_start,
                "local_end": min(len(paragraph.text), local_end),
                "auto_numbered": is_auto,
                "strong_boundary": is_auto or is_text_prefix,
                "inline_boundary": is_inline_boundary,
            }
        )

    accepted_markers: List[Dict[str, object]] = []
    seen_numbers: set[int] = set()
    for marker in marker_candidates:
        number = int(marker["number"])
        if number in seen_numbers:
            # Later occurrences are dependency references, not new boundaries.
            continue
        if not accepted_markers:
            accept = number == 1 or bool(marker["strong_boundary"])
        else:
            previous = int(accepted_markers[-1]["number"])
            accept = (
                number == previous + 1
                and (
                    bool(marker["strong_boundary"])
                    or bool(marker["inline_boundary"])
                )
            ) or (
                bool(marker["strong_boundary"]) and number > previous
            )
        if not accept:
            continue
        seen_numbers.add(number)
        accepted_markers.append(marker)

    entries: List[Dict[str, object]] = []
    if accepted_markers:
        leading = stream[: int(accepted_markers[0]["start"])].strip()
        if leading and claim_paragraphs:
            paragraph = claim_paragraphs[0]
            yield _issue(
                "CLM001",
                "申請專利範圍開頭存在不屬於任何請求項的文字。",
                "請確認這段內容所屬的請求項。",
                paragraph=paragraph,
                start=0,
                end=min(len(paragraph.text), 36),
            )
    elif any(paragraph.text.strip() for paragraph in claim_paragraphs):
        paragraph = next(
            item for item in claim_paragraphs if item.text.strip()
        )
        yield _issue(
            "CLM001",
            "申請專利範圍內找不到「請求項N」項次文字。",
            "請確認每個請求項均具有可辨識的請求項項次。",
            paragraph=paragraph,
            start=0,
            end=min(len(paragraph.text), 36),
        )

    for position, marker in enumerate(accepted_markers):
        segment_end = (
            int(accepted_markers[position + 1]["start"])
            if position + 1 < len(accepted_markers)
            else len(stream)
        )
        source_paragraphs: List[PatentParagraph] = []
        seen_source_indices: set[int] = set()
        for chunk in chunks:
            if (
                int(chunk["end"]) <= int(marker["end"])
                or int(chunk["start"]) >= segment_end
            ):
                continue
            source_paragraph = chunk["paragraph"]
            if source_paragraph.index in seen_source_indices:
                continue
            seen_source_indices.add(source_paragraph.index)
            source_paragraphs.append(source_paragraph)
        raw_body = stream[int(marker["end"]):segment_end]
        body = re.sub(r"^\s*[:：、.．]\s*", "", raw_body).strip()
        body_start = int(marker["end"]) + (raw_body.find(body) if body else 0)
        body_source_spans = []
        for chunk in chunks:
            left = max(body_start, int(chunk["text_start"]))
            right = min(body_start + len(body), int(chunk["end"]))
            if left < right:
                body_source_spans.append({
                    "paragraph": chunk["paragraph"],
                    "body_start": left - body_start,
                    "body_end": right - body_start,
                    "source_start": left - int(chunk["text_start"]),
                })
        paragraph = marker["paragraph"]
        if not marker["auto_numbered"]:
            yield _issue(
                "CLM006",
                "請求項編號是直接鍵入的文字，不是 Word 自動編號。",
                "請確認此項次是否應套用正式請求項自動編號。",
                paragraph=paragraph,
                start=int(marker["local_start"]),
                end=int(marker["local_end"]),
            )
        entries.append(
            {
                "number": int(marker["number"]),
                "paragraph": paragraph,
                "body": body,
                "stream_body_start": int(marker["end"]),
                "stream_body_end": segment_end,
                "number_start": int(marker["number_start"]),
                "number_end": int(marker["number_end"]),
                "auto_numbered": bool(marker["auto_numbered"]),
                "source_paragraphs": source_paragraphs or [paragraph],
                "body_source_spans": body_source_spans,
            }
        )

    yield from _duplicate_claim_issues(entries)

    first_claim_is_multiline = bool(
        entries and "\n" in str(entries[0]["body"]).strip()
    )
    for entry in entries:
        entry["analysis_body"] = re.sub(r"\s+", "", str(entry["body"]))
        raw_dependencies = _claim_dependencies(str(entry["analysis_body"]), 0)
        raw_dependency_numbers = sorted(
            {
                reference
                for references, _start, _end in raw_dependencies
                for reference in references
            }
        )
        analysis_body = str(entry["analysis_body"])
        explicit_dependent_opening = bool(
            re.match(
                r"^(?:如|依據|根據)?(?:申請專利範圍)?請求項\d+",
                analysis_body,
            )
        )
        starts_with_named_independent_target = analysis_body.startswith("一種")
        quantified_cited_target = _QUANTIFIED_AS_DESCRIBED_TARGET.match(
            analysis_body
        )
        starts_with_independent_target = bool(
            starts_with_named_independent_target
            or quantified_cited_target is not None
        )
        follows_first_claim_layout = (
            first_claim_is_multiline
            and "\n" in str(entry["body"]).strip()
            and not explicit_dependent_opening
        )
        inherits_claim_components = bool(
            int(entry["number"]) > 1
            and starts_with_independent_target
            and raw_dependency_numbers
            and _CLAIM_AS_DESCRIBED.search(analysis_body) is not None
        )
        entry["is_independent"] = bool(
            int(entry["number"]) == 1
            or starts_with_independent_target
            or follows_first_claim_layout
            or not raw_dependency_numbers
        )
        entry["independent_reason"] = (
            "independent_target_importing_prior_claim"
            if inherits_claim_components
            else "starts_with_一種"
            if starts_with_named_independent_target
            else "quantified_prior_claim_target"
            if quantified_cited_target is not None
            else "same_multiline_layout"
            if follows_first_claim_layout
            else "no_dependency"
            if not raw_dependency_numbers
            else "claim_one"
        )
        entry["inherits_claim_components"] = inherits_claim_components
        entry["dependency_groups"] = raw_dependencies
        entry["dependencies"] = (
            raw_dependency_numbers
            if inherits_claim_components or not entry["is_independent"]
            else []
        )
        entry["is_multiple"] = len(entry["dependencies"]) > 1

    def claim_line_location(
        entry: Dict[str, object], line: str
    ) -> Tuple[PatentParagraph, int, int]:
        """Map one logical claim line back to its source Word paragraph."""

        for source_paragraph in entry.get("source_paragraphs", []):
            start = source_paragraph.text.find(line)
            if start < 0:
                continue
            line_end = start + len(line.rstrip())
            return source_paragraph, max(start, line_end - 3), line_end
        fallback = entry["paragraph"]
        start, end = _ending_highlight_span(fallback)
        return fallback, start, end

    actual_numbers = {int(entry["number"]) for entry in entries}
    entries_by_number = {int(entry["number"]): entry for entry in entries}
    for position, entry in enumerate(entries, start=1):
        number = int(entry["number"])
        paragraph = entry["paragraph"]
        body = str(entry["body"])
        analysis_body = str(entry["analysis_body"])
        if number != position:
            yield _issue(
                "CLM002",
                f"文件中的第{position}個請求項標為請求項{number}。",
                f"請確認是否應改為請求項{position}，並同步檢查所有依附關係。",
                paragraph=paragraph,
                start=int(entry["number_start"]),
                end=int(entry["number_end"]),
                details={"expected_number": position, "actual_number": number},
            )
        if not body:
            yield _issue(
                "CLM007",
                f"請求項{number}沒有內容。",
                "請補入請求項內容或刪除空白請求項。",
                paragraph=paragraph,
                start=0,
                end=0,
            )
            continue

        invalid_dependencies: List[int] = []
        dependency_spans: List[Tuple[int, int]] = []
        for references, start, end in entry["dependency_groups"]:
            invalid = [
                reference
                for reference in references
                if reference not in actual_numbers or reference >= number
            ]
            if invalid:
                invalid_dependencies.extend(invalid)
                dependency_spans.append((start, end))
        if invalid_dependencies:
            values = sorted(set(invalid_dependencies))
            yield _issue(
                "CLM003",
                f"請求項{number}引用無效或尚未出現的請求項：{', '.join(map(str, values))}。",
                "請將依附對象改為已存在且編號較小的請求項。",
                paragraph=paragraph,
                start=0,
                end=min(len(paragraph.text), 48),
                details={"invalid_dependencies": values},
            )

        dependencies = list(entry["dependencies"])
        if (
            entry["is_multiple"]
            and not entry["is_independent"]
            and not re.search(
                r"(?:任一(?:請求項|項)?|或者|或)", analysis_body
            )
        ):
            yield _issue(
                "CLM004",
                f"請求項{number}引用多個請求項，但未辨識到選擇式文字。",
                "請確認此多項附屬項是否以任一項或或等選擇式記載。",
                paragraph=paragraph,
                start=0,
                end=min(len(paragraph.text), 64),
                details={"dependencies": dependencies},
            )
        for match in re.finditer("權利要求", analysis_body):
            yield _issue(
                "CLM008",
                "請求項使用了「權利要求」用語。",
                "請依正式用語改為申請專利範圍或請求項。",
                paragraph=paragraph,
                start=0,
                end=len(paragraph.text),
            )
        full_stop_count = analysis_body.count("。")
        if full_stop_count != 1 or not analysis_body.endswith("。"):
            if full_stop_count > 1:
                message = (
                    f"請求項{number}共出現{full_stop_count}個句號「。」；"
                    "同一請求項只能出現一次句號。"
                )
            elif full_stop_count == 0:
                message = f"請求項{number}沒有全形句號「。」；請於句尾補上一個句號。"
            else:
                message = f"請求項{number}的句號「。」不在句尾；句號只能出現一次並位於句尾。"

            # Highlight the first internal full stop (or the final character
            # when missing), within this claim's exact source-stream bounds.
            # Claims can span Word paragraphs or share one paragraph, so a
            # paragraph-wide search could point into a different claim.
            stream_start = int(entry["stream_body_start"])
            stream_end = int(entry["stream_body_end"])
            focus = stream.find("。", stream_start, stream_end)
            if focus < 0:
                focus = stream_start + len(stream[stream_start:stream_end].rstrip()) - 1
            source_paragraph = paragraph
            start, end = _ending_highlight_span(paragraph)
            for chunk in chunks:
                if int(chunk["text_start"]) <= focus < int(chunk["end"]):
                    source_paragraph = chunk["paragraph"]
                    start = focus - int(chunk["text_start"])
                    end = start + 1
                    break
            yield _issue(
                "CLM009",
                message,
                "請人工確認請求項是否為單一句，並僅在句尾使用一個句號。",
                paragraph=source_paragraph,
                start=start,
                end=end,
                details={"claim_number": number, "full_stop_count": full_stop_count},
            )

        # A dependent claim remains a single sentence regardless of its Word
        # layout.  Only independent claims use the prescribed colon/semicolon
        # sequence for deliberately separated constituent lines.  The final
        # line's full stop is already enforced for every claim by CLM009.
        claim_lines = [
            line.strip()
            for line in body.splitlines()
            if line.strip()
        ]
        if entry["is_independent"] and len(claim_lines) >= 2:
            for line_position, line in enumerate(claim_lines[:-1]):
                if line_position == 0:
                    expected_ending = "："
                elif (
                    len(claim_lines) >= 3
                    and line_position == len(claim_lines) - 2
                ):
                    expected_ending = "；及"
                else:
                    expected_ending = "；"
                canonical_line = re.sub(r"\s+", "", line)
                if canonical_line.endswith(expected_ending):
                    continue
                source_paragraph, start, end = claim_line_location(entry, line)
                if line_position == 0:
                    line_role = "第一行"
                elif line_position == len(claim_lines) - 2:
                    line_role = "倒數第二行"
                else:
                    line_role = f"第{line_position + 1}行"
                yield _issue(
                    "CLM017",
                    f"請求項{number}獨立項的{line_role}預期以「{expected_ending}」結尾。",
                    f"請將此行的結尾標點改為「{expected_ending}」。",
                    paragraph=source_paragraph,
                    start=start,
                    end=end,
                    details={
                        "claim_number": number,
                        "line_position": line_position + 1,
                        "line_count": len(claim_lines),
                        "expected_ending": expected_ending,
                    },
                )

    def reaches_multiple_claim(claim_number: int, visited: set[int]) -> set[int]:
        if claim_number in visited:
            return set()
        visited = visited | {claim_number}
        claim = entries_by_number.get(claim_number)
        if claim is None:
            return set()
        hits = (
            {claim_number}
            if claim["is_multiple"] and not claim["is_independent"]
            else set()
        )
        for dependency in claim["dependencies"]:
            hits.update(reaches_multiple_claim(dependency, visited))
        return hits

    for entry in entries:
        if not entry["is_multiple"] or entry["is_independent"]:
            continue
        forbidden = set()
        for dependency in entry["dependencies"]:
            forbidden.update(reaches_multiple_claim(dependency, set()))
        if forbidden:
            paragraph = entry["paragraph"]
            number = int(entry["number"])
            yield _issue(
                "CLM005",
                f"多項附屬項請求項{number}直接或間接依附另一多項附屬項：{', '.join(map(str, sorted(forbidden)))}。",
                "請重新安排依附關係。",
                paragraph=paragraph,
                start=0,
                end=min(len(paragraph.text), 64),
                details={
                    "dependencies": list(entry["dependencies"]),
                    "multiple_claim_ancestors": sorted(forbidden),
                },
            )

    transfer = extract_document_symbols(document)
    known_names = sorted(
        {_canonical_name(entry.name) for entry in transfer.full_entries if entry.name},
        key=len,
        reverse=True,
    )
    known_component_expression = (
        "(?:" + "|".join(re.escape(name) for name in known_names) + ")"
        if known_names
        else r"(?!)"
    )
    distributive_ownership_verb_expression = (
        r"包含|包括|包括有|具有|設有|配置有|形成有|設置有|"
        r"裝設有|安裝有|附設有|配備有|界定有|夾有"
    )
    distributive_owner_header_pattern = re.compile(
        rf"(?:"
        rf"(?:{_COMPONENT_DISTRIBUTIVE_REFERENCE_EXPRESSION})"
        rf"{known_component_expression}"
        rf"[^，,；;。]{{0,48}}?"
        rf"(?:{distributive_ownership_verb_expression})"
        rf"|"
        rf"該等{known_component_expression}"
        rf"[^，,；;。]{{0,48}}?(?:各自|分別)"
        rf"[^，,；;。]{{0,48}}?"
        rf"(?:{distributive_ownership_verb_expression})"
        rf")"
    )
    distributive_continuation_pattern = re.compile(
        r"^(?:以及|並且|及|與|和|或|且|並)"
    )

    def active_distributive_header(
        body: str,
        position: int,
    ) -> Optional[re.Match[str]]:
        """Locate an active「每一該 parent has child」description scope."""

        prefix = body[:position]
        for header in reversed(list(distributive_owner_header_pattern.finditer(prefix))):
            tail = prefix[header.end():]
            if re.search(r"[；;。]", tail):
                continue
            comma_segments = re.split(r"[，,]", tail)
            if all(
                not segment
                or distributive_continuation_pattern.match(segment) is not None
                for segment in comma_segments[1:]
            ):
                return header
        return None

    def active_distributive_scope(
        body: str,
        position: int,
    ) -> Optional[int]:
        header = active_distributive_header(body, position)
        return header.start() if header is not None else None

    def component_occurrences(body: str) -> List[Tuple[int, int, str]]:
        candidates: List[Tuple[int, int, int, str]] = []
        for name in occurrence_names:
            for match in re.finditer(re.escape(name), body):
                candidates.append(
                    (match.start(), match.end(), len(name), name)
                )
        selected: List[Tuple[int, int, int, str]] = []
        for candidate in sorted(
            candidates, key=lambda item: (-item[2], item[0], item[1])
        ):
            start, end = candidate[0], candidate[1]
            if any(
                chosen_start < end and chosen_end > start
                for chosen_start, chosen_end, _length, _name in selected
            ):
                continue
            selected.append(candidate)
        return [
            (start, end, name)
            for start, end, _length, name in sorted(
                selected, key=lambda item: item[0]
            )
        ]

    quantifier_expression = _COMPONENT_QUANTIFIER_EXPRESSION
    possessive_hierarchy_pattern = re.compile(
        rf"(?:"
        rf"(?P<reference>{_COMPONENT_REFERENCE_EXPRESSION})"
        rf"|(?<![第該])(?P<quantity>{quantifier_expression})"
        rf")\s*"
        rf"(?P<parent>{known_component_expression})\s*"
        rf"(?:(?:的|之)\s*{known_component_expression}\s*)*"
        rf"(?:的|之)\s*$"
    )
    distributive_hierarchy_singular_member_pattern = re.compile(
        rf"(?:{_COMPONENT_DISTRIBUTIVE_REFERENCE_EXPRESSION})\s*"
        rf"{known_component_expression}\s*"
        rf"(?:的|之)\s*"
        rf"(?:"
        rf"(?:該)?{known_component_expression}\s*"
        rf"(?:的|之|以及|及|與|和|或|、)\s*"
        rf")*"
        rf"該\s*$"
    )
    anchored_modifier_reference_pattern = re.compile(
        rf"(?P<reference>{_COMPONENT_REFERENCE_EXPRESSION})"
    )
    quantifier_pattern = re.compile(
        rf"^(?P<quantifier>{quantifier_expression})"
    )
    quantified_ordinal_modifier_pattern = re.compile(
        rf"(?<![第該])(?P<quantifier>{quantifier_expression})"
        r"(?:第[一二三四五六七八九十百兩0-9A-Za-z]+)$"
    )
    trailing_quantifier_pattern = re.compile(
        rf"(?<![第該])(?P<quantifier>{quantifier_expression})$"
    )
    quantified_as_described_component_pattern = re.compile(
        rf"(?<![第該])(?P<quantifier>{quantifier_expression})"
        r"如[^，,；;。\r\n]{1,240}?所述(?:的|之)?$"
    )
    additive_member_pattern = re.compile(
        r"(?:"
        r"(?:還|另|又|再|額外|此外|復)(?:包含|包括|具有|設有|增設|配置|形成)?"
        r"(?:另(?:外)?一|又一|再一|額外一|一)"
        r"|(?:包含|包括|具有|設有|增設|配置|形成)"
        r"(?:另(?:外)?一|又一|再一|額外一)"
        r"|(?:及|以及|與|和)(?:另(?:外)?一|又一|再一|額外一)"
        r")(?:該)?$"
    )
    premodified_quantity_pattern = re.compile(
        rf"(?=(?P<boundary>^|包含|包括|具有|設有|增設|配置|形成|其中|供|"
        rf"以及|及|與|和|或|，|,|；|;|：|:|、)"
        rf"(?<![第該])(?P<quantifier>{quantifier_expression})(?!第))"
    )
    premodifier_tail_pattern = re.compile(
        r"[^，,；;。:：、]*(?:的|之)$"
    )
    relationship_modifier_expression = (
        r"與|及|相對|相反|垂直|平行|連通|連接|耦接|相鄰|鄰接|"
        r"位於|設置於|形成於|配置於|沿|朝|面向|對應|間隔|遠離|靠近|"
        r"穿過|橫跨|圍繞|繞|覆蓋|接觸|供|用以"
    )
    relationship_modifier_head_pattern = re.compile(
        rf"^(?:{relationship_modifier_expression})"
    )
    relational_premodifier_quantity_pattern = re.compile(
        rf"(?<![第該])(?P<quantifier>{quantifier_expression})(?!第)"
        r"(?P<modifier>"
        rf"(?:{relationship_modifier_expression})"
        r"[^，,；;。:：]*(?:的|之))$"
    )
    nearby_modifier_quantity_pattern = re.compile(
        rf"(?<![第該])(?P<quantifier>{quantifier_expression})(?!第)"
    )
    nested_quantity_prefixes = (
        "相對於", "垂直於", "平行於", "連通於", "設置於", "配置於",
        "沿", "於", "朝", "向", "往", "由", "供", "與", "及", "和", "或",
    )
    strong_quantity_boundaries = {
        "", "包含", "包括", "具有", "設有", "增設", "配置", "形成",
        "其中", "供", "，", ",", "；", ";", "：", ":",
    }
    introduction_boundaries = (
        "，", ",", "；", ";", "：", ":", "、",
        "包含", "包括", "具有", "以及", "及", "與",
    )

    def introduction_kind(body: str, name_start: int) -> str:
        def kind_for_quantifier(quantifier: str) -> str:
            if quantifier == "一":
                return "singular"
            if quantifier == "至少一":
                return "flexible"
            return "plural"

        prefix = body[:name_start]

        def apply_distributive_ownership(kind: str) -> str:
            # 「每一該按鍵具有一接點」describes one child per member of a
            # plural parent set.  Although the local child quantifier is「一」,
            # the claim-level set of children is plural.  Selective references
            # such as「其中一該／任一該」are intentionally not distributors.
            if kind in {"singular", "flexible"} and (
                active_distributive_scope(body, name_start) is not None
            ):
                return "plural"
            return kind

        immediate_prefix = prefix[-4:]
        if immediate_prefix.endswith(("該", "該等", "所述的", "所述之")):
            cited_quantity = quantified_as_described_component_pattern.search(
                prefix
            )
            if cited_quantity is None:
                return ""
            return apply_distributive_ownership(
                kind_for_quantifier(cited_quantity.group("quantifier"))
            )
        direct_match = trailing_quantifier_pattern.search(prefix)
        if direct_match is not None:
            return apply_distributive_ownership(
                kind_for_quantifier(direct_match.group("quantifier"))
            )
        ordinal_match = quantified_ordinal_modifier_pattern.search(prefix)
        if ordinal_match is not None:
            # A symbol-list name may be the shared base「PTC元件」while the
            # claim introduces「一第一PTC元件」and「一第二PTC元件」.  The
            # ordinal is a modifier; the earlier「一」remains the quantity.
            return apply_distributive_ownership(
                kind_for_quantifier(ordinal_match.group("quantifier"))
            )
        # A quantity can introduce a component through a long prenominal
        # modifier, for example「一沿一頂底方向設置的基座」.  A simple
        # nearest-quantity rule would incorrectly bind the inner「一」to
        # 「基座」.  Score every complete「數量詞＋…的／之＋元件」candidate:
        # construction/list boundaries are stronger, a relationship head
        # supports an outer modifier, and a tail beginning with another known
        # component is evidence that the candidate belongs to that component.
        # If two candidates remain equally plausible, prefer the earlier
        # (outer) candidate rather than the nearer nested one.
        modified_candidates: List[Tuple[int, int, str]] = []
        for candidate in premodified_quantity_pattern.finditer(prefix):
            tail = prefix[candidate.end("quantifier"):]
            if premodifier_tail_pattern.fullmatch(tail) is None:
                continue
            if tail.startswith("者"):
                # 「其中兩者……」counts the previously mentioned subjects;
                # 「兩」is not the quantity of a component introduced later in
                # the same clause.
                continue
            boundary = candidate.group("boundary")
            score = 40 if boundary in strong_quantity_boundaries else 10
            if relationship_modifier_head_pattern.match(tail) is not None:
                score += 40
            if any(tail.startswith(name) for name in known_names):
                continue
            modified_candidates.append(
                (score, candidate.start("quantifier"), candidate.group("quantifier"))
            )
        if modified_candidates:
            quantifier = max(
                modified_candidates,
                key=lambda item: (item[0], -item[1]),
            )[2]
            return apply_distributive_ownership(kind_for_quantifier(quantifier))
        # Some patent sentences use a construction verb that is not itself a
        # list boundary, e.g.「界定一連通該開口的電池空間」and
        # 「沿一繞該軸線的第一周向」.  Accept only a constrained relationship
        # modifier here; the negative guards keep the「一／二」inside
        # 「第一／第二」from becoming a quantity.
        relational_match = relational_premodifier_quantity_pattern.search(prefix)
        if relational_match is not None:
            return apply_distributive_ownership(
                kind_for_quantifier(relational_match.group("quantifier"))
            )
        # A plural parent can distribute an unquantified child to each member,
        # e.g.「該等公轉子齒的齒型相同並各自具有以下公齒型段」.
        # 「以下／下列」belongs to the disclosure phrase rather than the
        # component name, and the claim-level set of those children is plural.
        distributive_header = active_distributive_header(body, name_start)
        if distributive_header is not None:
            distributive_tail = re.sub(
                r"\s+",
                "",
                prefix[distributive_header.end():],
            )
            if distributive_tail in {"", "以下", "下列", "如下", "如下所列"}:
                return "plural"
        # A possessive hierarchy omits repeated articles on lower levels, e.g.
        # 「該安裝座的開槽的缺槽」.  Evaluate this after explicit outer
        # quantity candidates but before the nearby fallback, so an「一」in an
        # earlier「每一該」cannot override the hierarchy's plural root.
        hierarchy_match = possessive_hierarchy_pattern.search(prefix)
        if hierarchy_match is not None:
            reference = hierarchy_match.group("reference") or ""
            if reference in _COMPONENT_HIERARCHY_PLURAL_REFERENCES:
                return "plural"
            if reference:
                return "singular"
            return kind_for_quantifier(hierarchy_match.group("quantity"))
        # Conservative 16-character fallback requested for claims such as
        # 「複數經由該開口向外延伸的導流件」.  It is intentionally applied
        # only after the structural rules above.  Quantities nested after
        # 「沿／於／朝／供」or belonging to another known component are
        # excluded, so「沿一頂底方向設置的基座」does not borrow the「一」.
        compact_prefix = re.sub(r"\s+", "", prefix)
        if compact_prefix.endswith("的"):
            lookback = compact_prefix[max(0, len(compact_prefix) - 17):-1]
            boundary = 0
            for token in ("，", ",", "；", ";", "。", "：", ":", "、"):
                position = lookback.rfind(token)
                if position >= 0:
                    boundary = max(boundary, position + len(token))
            lookback = lookback[boundary:]
            nearby_candidates: List[Tuple[int, str]] = []
            for candidate in nearby_modifier_quantity_pattern.finditer(lookback):
                before = lookback[:candidate.start("quantifier")]
                tail = lookback[candidate.end("quantifier"):]
                if before.endswith(nested_quantity_prefixes):
                    continue
                if tail.startswith("者"):
                    continue
                if any(tail.startswith(known_name) for known_name in known_names):
                    continue
                nearby_candidates.append(
                    (candidate.start("quantifier"), candidate.group("quantifier"))
                )
            if nearby_candidates:
                quantifier = min(nearby_candidates, key=lambda item: item[0])[1]
                return apply_distributive_ownership(
                    kind_for_quantifier(quantifier)
                )
        boundary = 0
        for token in introduction_boundaries:
            position = prefix.rfind(token)
            if position >= 0:
                boundary = max(boundary, position + len(token))
        phrase = prefix[boundary:]
        match = quantifier_pattern.match(phrase)
        if match is None:
            return ""
        return apply_distributive_ownership(
            kind_for_quantifier(match.group("quantifier"))
        )

    def anchored_modifier_reference(
        body: str,
        name_start: int,
    ) -> str:
        """Return the outer article in「article + modifier + 的 + name」."""

        prefix = re.sub(r"\s+", "", body[:name_start])
        if not prefix.endswith(("的", "之")):
            return ""
        window = prefix[max(0, len(prefix) - 80):]
        boundary = 0
        for token in ("，", ",", "；", ";", "。", "：", ":", "、"):
            position = window.rfind(token)
            if position >= 0:
                boundary = max(boundary, position + len(token))
        window = window[boundary:]
        candidates: List[Tuple[int, str]] = []
        for reference in anchored_modifier_reference_pattern.finditer(window):
            tail = window[reference.end("reference"):]
            if re.fullmatch(r"[^，,；;。:：、]{1,64}(?:的|之)", tail) is None:
                continue
            modifier = tail[:-1]
            # 「位於該基座的外殼」contains an article for「基座」,
            # not for「外殼」.  An outer article such as「該位於…的外殼」
            # starts with a modifier instead of another registered component.
            if any(modifier.startswith(name) for name in known_names):
                continue
            candidates.append(
                (reference.start("reference"), reference.group("reference"))
            )
        if not candidates:
            return ""
        return min(candidates, key=lambda item: item[0])[1]

    def reference_kind_for_token(token: str) -> str:
        if token == "該":
            return "singular"
        if token == "該等" or token in _COMPONENT_PLURAL_SELECTION_REFERENCES:
            return "plural"
        if token in _COMPONENT_MEMBER_REFERENCES:
            return "plural_member"
        return ""

    qualified_reference_pattern = re.compile(
        rf"(?P<reference>{_COMPONENT_REFERENCE_EXPRESSION})"
        r"(?P<modifier>第[一二三四五六七八九十百兩0-9A-Za-z]+)$"
    )
    corresponding_singular_reference_pattern = re.compile(
        r"(?P<modifier>(?:相)?對應(?:的|之))(?P<reference>該)$"
    )
    quantified_plural_reference_pattern = re.compile(
        r"(?<![第該])"
        r"(?P<reference>(?:(?:數|多)個|複數|[二三四五六七八九十百兩]+(?:個)?)該)$"
    )

    subject_name_candidates = sorted(
        set(known_names) | {_canonical_name(document.patent_title)},
        key=len,
        reverse=True,
    )
    subject_application_expression = (
        r"(?:係|可)?(?:應用於|適用於|運用於|使用於|用於)"
    )
    subject_name_expression = "|".join(
        re.escape(name) for name in subject_name_candidates if name
    ) or r"(?!)"
    named_subject_application_pattern = re.compile(
        r"^(?:一種|一)?(?P<subject>"
        + subject_name_expression
        + rf")(?={subject_application_expression})"
    )

    def claim_subject_match(
        body: str,
        is_independent: bool,
    ) -> Optional[re.Match[str]]:
        if is_independent:
            quantified = _QUANTIFIED_AS_DESCRIBED_TARGET.match(body)
            if quantified is not None:
                return quantified
            # A complete known name can be followed by its intended use with
            # no comma:「一種A應用於一B」. Only match a name at the opening;
            # metadata alone cannot establish an unmentioned claim subject.
            named_application = named_subject_application_pattern.match(body)
            if named_application is not None:
                return named_application
            anchored = re.match(
                r"^(?:一種|一)?\s*(?P<subject>[\u4e00-\u9fffA-Za-z0-9]+?)"
                r"(?=\s*(?:，|,|；|;|：|:|。|包含|包括|具有|其特徵|$))",
                body,
            )
            if anchored is not None:
                return anchored
        return re.search(
            r"(?:所述之|所述的)\s*(?P<subject>[\u4e00-\u9fffA-Za-z0-9]+?)"
            r"(?=\s*(?:，|,|；|;|包含|包括|其中|其特徵))",
            body,
        )

    subject_matches = {
        int(entry["number"]): claim_subject_match(
            str(entry["analysis_body"]),
            bool(entry["is_independent"]),
        )
        for entry in entries
    }

    subjects = {
        number: _canonical_name(match.group("subject")) if match else ""
        for number, match in subject_matches.items()
    }
    subject_spans = {
        number: (match.start("subject"), match.end("subject")) if match else None
        for number, match in subject_matches.items()
    }
    # Match full subjects even when they have no symbol-list entry. Otherwise
    #「該螺旋鑽導引裝置」could be reduced to the embedded component「螺旋鑽」.
    occurrence_names = sorted(
        set(known_names) | {subject for subject in subjects.values() if subject},
        key=len,
        reverse=True,
    )
    subject_introductions: Dict[int, str] = {}
    for entry in entries:
        number = int(entry["number"])
        match = subject_matches[number]
        if not entry["is_independent"] or match is None:
            continue
        quantity = match.groupdict().get("quantity") or "一"
        subject_introductions[number] = (
            "singular" if quantity == "一"
            else "flexible" if quantity == "至少一"
            else "plural"
        )

    # An alternative dependency inherits ONE complete ancestor path, not the
    # union of all alternatives. Store the final, sequentially analysed state
    # of each path so local introductions and additive members stay branch-local.
    resolved_claim_states: Dict[
        int, List[Tuple[Tuple[int, ...], Dict[str, set[str]]]]
    ] = defaultdict(list)

    def claim_dependency_contexts():
        # The consumer records each analysed state before requesting the next
        # context; later claims can therefore inherit all completed paths.
        for entry in entries:
            number = int(entry["number"])
            alternatives: List[Tuple[Tuple[int, ...], Dict[str, set[str]]]] = []
            for dependency in entry["dependencies"]:
                dependency = int(dependency)
                # Invalid forward/self dependencies already have CLM003.
                # Never borrow future state or traverse a malformed cycle.
                prior_states = (
                    resolved_claim_states.get(dependency)
                    if dependency < number else None
                )
                alternatives.extend(prior_states or [((dependency,), {})])
            if not alternatives:
                alternatives.append(((), {}))
            for index, (path, state) in enumerate(alternatives):
                yield entry, path, state, len(alternatives) > 1, index == 0

    if detected_subjects is not None:
        for entry in entries:
            if not entry["is_independent"]:
                continue
            subject = subjects.get(int(entry["number"]), "")
            if subject and subject not in detected_subjects:
                detected_subjects.append(subject)
    for (
        entry, dependency_path, inherited_state, multiple_paths, first_path
    ) in claim_dependency_contexts():
        paragraph = entry["paragraph"]
        body = str(entry["body"])
        analysis_body = str(entry["analysis_body"])
        number = int(entry["number"])
        subject = subjects[number]
        if first_path and not subject:
            yield _issue(
                "CLM015",
                f"系統無法可靠判別請求項{number}的標的名稱。",
                "請人工確認此請求項的格式、標的名稱及依附關係。",
                paragraph=paragraph,
                start=0,
                end=min(len(paragraph.text), 64),
            )
        if first_path and not entry["is_independent"]:
            for dependency in entry["dependencies"]:
                dependency_subject = subjects.get(int(dependency), "")
                if subject and dependency_subject and subject != dependency_subject:
                    yield _issue(
                        "CLM011",
                        f"請求項{number}的標的「{subject}」與所依附請求項{dependency}的標的不一致。",
                        "請人工確認直接及間接依附的標的名稱。",
                        paragraph=paragraph,
                        start=0,
                        end=min(len(paragraph.text), 64),
                        details={
                            "dependency": dependency,
                            "dependency_subject": dependency_subject,
                        },
                    )
                    break

        current_state: Dict[str, set[str]] = defaultdict(set)
        if number in subject_introductions:
            current_state[subject].add(subject_introductions[number])
        for name, kinds in inherited_state.items():
            current_state[name].update(kinds)

        # A child introduced while describing "each" member of a plural
        # parent is plural at claim level, but singular inside that same local
        # distributive scope.  Keep the scope origin separately so a later
        # direct singular reference outside the scope is still rejected.
        distributive_scope_components: Dict[str, set[int]] = defaultdict(set)
        emitted_component_issue_messages: set[Tuple[str, str]] = set()

        for start, _end, name in component_occurrences(analysis_body):
            subject_span = subject_spans.get(number)
            if (
                subject_span is not None
                and subject_span[0] <= start
                and _end <= subject_span[1]
            ):
                continue
            if name in _CLAIM_QUANTITY_EXEMPT_COMPONENT_NAMES:
                # 「步驟」is a special procedural item.  Patent claims freely
                # alternate between「步驟／該步驟／該等步驟」and list headers
                # such as「以下步驟」, so it is exempt from claim-level
                # quantity/article agreement.  Embodiment label checks remain
                # independent and continue to require its registered symbol.
                continue
            prefix = analysis_body[max(0, start - 80):start]
            available = current_state.get(name, set())
            additive_match = additive_member_pattern.search(prefix)
            if additive_match is not None and available:
                # 「一開槽」加上「另一該開槽」後已建立至少兩個開槽，
                # 本項後文及依附於本項的請求項都可使用「該等開槽」。
                current_state[name].add("plural")
                continue

            quantified_plural_reference = (
                quantified_plural_reference_pattern.search(prefix)
            )
            if quantified_plural_reference is not None and not available:
                # Patent claims may establish a plural set using a definite
                # plural phrase such as「複數該橫梁」.  Once established,
                # selections such as「二個該橫梁／多個該橫梁」remain plural
                # references even though the phrase ends in the character「該」.
                current_state[name].add("plural")
                continue

            kind = introduction_kind(analysis_body, start)
            if kind:
                scope_start = active_distributive_scope(analysis_body, start)
                if scope_start is not None:
                    distributive_scope_components[name].add(scope_start)
                current_state[name].add(kind)
                continue
            if prefix.endswith("所述的") or prefix.endswith("所述之"):
                # The repeated subject in a dependent-claim opening is a
                # grammatical claim target, not a new component introduction.
                continue

            reference_kind = ""
            reference_text = name
            reference_token = ""
            additive_prefix = (
                additive_match.group(0) if additive_match is not None else ""
            )
            anchored_reference = anchored_modifier_reference(
                analysis_body,
                start,
            )
            immediate_reference = next(
                (
                    token
                    for token in _COMPONENT_REFERENCE_TOKENS
                    if prefix.endswith(token)
                ),
                "",
            )
            qualified_reference = qualified_reference_pattern.search(prefix)
            corresponding_singular_reference = (
                corresponding_singular_reference_pattern.search(prefix)
            )
            if additive_prefix:
                reference_kind = "additive_member"
                reference_text = f"{additive_prefix}{name}"
            elif quantified_plural_reference is not None:
                reference_token = quantified_plural_reference.group("reference")
                reference_kind = "plural"
                reference_text = f"{reference_token}{name}"
            elif corresponding_singular_reference is not None:
                # A relationship modifier does not turn the following singular
                # article into a plural reference.  Thus「複數A……對應的該A」
                # remains a singular-after-plural quantity mismatch.
                reference_token = corresponding_singular_reference.group("reference")
                reference_kind = "singular"
                reference_text = (
                    f"{corresponding_singular_reference.group('modifier')}"
                    f"{reference_token}{name}"
                )
            else:
                reference_token = (
                    anchored_reference
                    or immediate_reference
                    or (
                        qualified_reference.group("reference")
                        if qualified_reference is not None
                        else ""
                    )
                )
                reference_kind = reference_kind_for_token(reference_token)
                if immediate_reference:
                    reference_text = f"{reference_token}{name}"
                elif qualified_reference is not None:
                    reference_text = (
                        f"{reference_token}"
                        f"{qualified_reference.group('modifier')}{name}"
                    )

            current_scope = active_distributive_scope(analysis_body, start)
            introduced_in_current_scope = (
                current_scope is not None
                and current_scope
                in distributive_scope_components.get(name, set())
            )
            temporary_distributive_singular = (
                reference_kind == "singular"
                and bool(available & {"plural", "flexible"})
                and (
                    distributive_hierarchy_singular_member_pattern.search(
                        analysis_body[:start]
                    )
                    is not None
                    or introduced_in_current_scope
                )
            )
            if temporary_distributive_singular:
                # A child set can be plural at claim level while each member of
                # a plural parent owns one temporary singular child.  This
                # covers both possessive paths such as
                # 「每一該按鍵部的該接點」 and references inside the same
                # distributor clause such as 「每一該送料輥具有一輥本體，
                # 及緊貼環繞該輥本體的一防滑層」.
                continue
            if (
                _SUSPEND_SINGULAR_REFERENCE_AFTER_PLURAL_CHECK
                and reference_kind == "singular"
                and bool(available & {"plural"})
            ):
                # Requested temporary suspension: do not report
                # 「先前是以複數或指定多數揭露」when the later reference uses
                # the singular article「該」.  Missing antecedents and the
                # opposite singular-to-plural mismatch remain checked.
                continue
            if reference_kind == "singular" and available & {
                "singular", "flexible"
            }:
                continue
            if reference_kind == "plural" and available & {
                "plural", "flexible"
            }:
                continue
            if reference_kind == "plural_member" and available & {
                "plural", "flexible"
            }:
                continue

            if reference_kind == "additive_member" and not available:
                message = (
                    f"請求項{number}使用「{reference_text}」，"
                    "但前文或依附項尚未建立可供追加的同名構件。"
                )
                suggestion = "請先以一、至少一、複數或明確數量引入此構件。"
            elif reference_kind == "plural_member" and not available:
                message = (
                    f"請求項{number}使用「{reference_text}」，"
                    "但前文或依附項沒有此複數構件的數量詞揭露。"
                )
                suggestion = "請先以複數或明確多數引入此構件。"
            elif reference_kind == "plural_member":
                message = (
                    f"請求項{number}使用「{reference_text}」選取單一成員，"
                    "但此構件先前不是以複數揭露。"
                )
                suggestion = "請確認前文是否應先以複數或明確多數引入此構件。"
            elif reference_kind and not available:
                message = (
                    f"請求項{number}使用「{reference_text}」，"
                    "但前文或依附項沒有此構件的數量詞揭露。"
                )
                suggestion = (
                    "第一次出現時請使用一、複數、二、三或至少一等數量詞。"
                )
            elif reference_kind == "singular":
                message = (
                    f"請求項{number}使用單數指稱「{reference_text}」，"
                    "但此構件先前是以複數或指定多數揭露。"
                )
                suggestion = f"請確認是否應改為「該等{name}」。"
            elif reference_kind == "plural":
                message = (
                    f"請求項{number}使用複數指稱「{reference_text}」，"
                    "但此構件先前只以單數揭露。"
                )
                suggestion = f"請確認是否應改為「該{name}」。"
            elif available:
                message = (
                    f"請求項{number}再次提到元件「{name}」時，"
                    "沒有使用該或該等指稱。"
                )
                suggestion = "單數構件請使用「該」，複數構件請使用「該等」。"
            else:
                message = (
                    f"請求項{number}第一次提到元件「{name}」時，"
                    "沒有辨識到數量詞。"
                )
                suggestion = (
                    "請在第一次出現時使用一、複數、二、三或至少一等數量詞。"
                )
            rule_id = (
                "CLM016"
                if reference_kind and not available
                else "CLM012"
            )
            if multiple_paths:
                path_text = " → ".join(
                    f"請求項{ancestor}" for ancestor in (number, *dependency_path)
                )
                message = f"{message.rstrip('。')}（依附路徑：{path_text}）。"
            issue_signature = (rule_id, message)
            if issue_signature in emitted_component_issue_messages:
                continue
            emitted_component_issue_messages.add(issue_signature)
            yield _issue(
                rule_id,
                message,
                suggestion,
                paragraph=paragraph,
                start=0,
                end=min(len(paragraph.text), 64),
                details={
                    "component_name": name,
                    "reference_kind": reference_kind or "plain",
                    "reference_token": reference_token,
                    "highlight_text": reference_text,
                    "available_kinds": sorted(available),
                    "body_offset": start,
                    "claim_number": number,
                    "dependency_path": list(dependency_path),
                },
            )

        resolved_claim_states[number].append(
            ((number, *dependency_path), current_state)
        )

        if first_path and entry["is_independent"]:
            mentioned = [name for name in known_names if name in _canonical_name(analysis_body)]
            if len(mentioned) >= 2 and not re.search(
                r"連接|相接|接收|傳送|整合|讀取|設置|位於|屬於|結合|耦接|"
                r"對應|安裝|配置|套設|固定|形成",
                analysis_body,
            ):
                yield _issue(
                    "CLM013",
                    f"獨立項請求項{number}列出多個主要構件，但未辨識到構件關係。",
                    "請人工確認是否已記載連結、位置或對應關係。",
                    paragraph=paragraph,
                    start=0,
                    end=len(paragraph.text),
                    details={"mentioned_components": mentioned},
                )

    yield from _claim_disclosure_issues(
        document,
        entries,
        [entry.name for entry in transfer.full_entries if entry.name],
        subjects,
        coverage_report,
    )

    independent_entries = [entry for entry in entries if entry["is_independent"]]
    for independent_entry in independent_entries:
        subject = subjects.get(int(independent_entry["number"]), "")
        title = _canonical_name(document.patent_title)
        if subject and title and subject not in title and title not in subject:
            paragraph = independent_entry["paragraph"]
            yield _issue(
                "CLM014",
                f"中文名稱「{document.patent_title}」與獨立項標的「{subject}」可能不一致。",
                "請人工確認名稱與申請專利範圍標的是否相符。",
                paragraph=paragraph,
                start=0,
                end=min(len(paragraph.text), 64),
                details={"claim_subject": subject},
            )


def _terminology_issues(document: PatentDocument) -> Iterable[PatentIssue]:
    if document.patent_type == "utility_model":
        wrong_terms = ("本發明", "此發明")
        expected = "本新型"
    elif document.patent_type == "invention":
        wrong_terms = ("本新型", "此新型")
        expected = "本發明"
    else:
        return
    for paragraph in document.paragraphs:
        if paragraph.section_key not in {"disclosure", "embodiments"}:
            continue
        for term in wrong_terms:
            start = 0
            while True:
                index = paragraph.text.find(term, start)
                if index < 0:
                    break
                yield _issue(
                    "REF003",
                    f"{document.patent_type == 'utility_model' and '新型' or '發明'}文件中出現不相符用語「{term}」。",
                    f"請確認是否應使用「{expected}」。",
                    paragraph=paragraph,
                    start=index,
                    end=index + len(term),
                )
                start = index + len(term)


def _typography_issues(document: PatentDocument) -> Iterable[PatentIssue]:
    for paragraph in document.paragraphs:
        for match in _REPEATED_CJK_CHARACTER.finditer(paragraph.text):
            yield _issue(
                "TXT004",
                f"發現連續重複的中文字「{match.group(0)}」。",
                "請回到 Word 原始檔確認是否為重複輸入並修正。",
                paragraph=paragraph,
                start=match.start(),
                end=match.end(),
                details={"repeated_character": match.group("character")},
            )
        for match in _FULLWIDTH_ALNUMERIC.finditer(paragraph.text):
            replacement = unicodedata.normalize("NFKC", match.group(0))
            yield _issue(
                "TXT001",
                f"發現可轉為半形的文字「{match.group(0)}」。",
                f"建議改為「{replacement}」。",
                paragraph=paragraph,
                start=match.start(),
                end=match.end(),
                safe_auto_fix=True,
                replacement=replacement,
            )
        for match in _INDIGENOUS_TERM.finditer(paragraph.text):
            yield _issue(
                "TXT003",
                f"偵測到需要人工確認的原住民族相關用語「{match.group(0)}」。",
                "此提示不代表文字錯誤，請依案件語境人工確認。",
                paragraph=paragraph,
                start=match.start(),
                end=match.end(),
            )
        for index, character in enumerate(paragraph.text):
            replacement = _NONSTANDARD_PRIME.get(character)
            if replacement is None:
                continue
            yield _issue(
                "TXT002",
                f"發現非標準 prime mark「{character}」。",
                "建議統一為半形 apostrophe「'」。",
                paragraph=paragraph,
                start=index,
                end=index + 1,
                safe_auto_fix=True,
                replacement=replacement,
            )


def _table_scope_issues(document: PatentDocument) -> Iterable[PatentIssue]:
    reported_tables: set[int] = set()
    for paragraph in document.paragraphs:
        if (
            paragraph.source_kind != "table"
            or not paragraph.text.strip()
            or not (
                paragraph.major_section_key == "claims"
                or paragraph.section_key == "claims"
            )
        ):
            continue
        table_index = paragraph.table_index if paragraph.table_index is not None else -1
        if table_index in reported_tables:
            continue
        reported_tables.add(table_index)
        display_number = table_index + 1 if table_index >= 0 else "?"
        yield _issue(
            "TBL001",
            f"表格{display_number}位於申請專利範圍；目前未執行跨欄請求項語法判定。",
            "表格文字仍會逐儲存格檢查；請人工確認請求項結構，或改用一般 Word 段落撰寫。",
            paragraph=paragraph,
            start=0,
            end=min(len(paragraph.text), 64),
            details={"review_mode": "cell_text_only"},
        )


def _custom_text_definition(rule: CustomTextRule) -> RuleDefinition:
    return RuleDefinition(
        rule.rule_id,
        f"自訂文字：{rule.text}",
        "custom_text",
        "error",
        f"偵測文件中是否出現特定文字「{rule.text}」。",
    )


def _custom_text_issues(
    document: PatentDocument,
    rules: Sequence[CustomTextRule],
) -> Iterable[PatentIssue]:
    for rule in rules:
        if rule.rule_type != CUSTOM_RULE_BLACKLIST:
            continue
        definition = _custom_text_definition(rule)
        for paragraph in document.paragraphs:
            start = 0
            while start < len(paragraph.text):
                index = paragraph.text.find(rule.text, start)
                if index < 0:
                    break
                end = index + len(rule.text)
                yield _issue(
                    rule.rule_id,
                    f"發現自訂規則文字「{rule.text}」。",
                    "請回到 Word 原始檔確認並修正文句。",
                    paragraph=paragraph,
                    start=index,
                    end=end,
                    details={"custom_text": rule.text},
                    rule_definition=definition,
                )
                start = index + 1


def _embodiment_figure_ocr_issues(
    document: PatentDocument,
    ocr_results: Sequence[Dict[str, object]],
) -> Iterable[PatentIssue]:
    from .figure_ocr_checker import find_embodiment_figure_mismatches

    for mismatch in find_embodiment_figure_mismatches(document, ocr_results):
        figure_text = "、".join(
            f"圖{number}" for number in mismatch.referenced_figures
        )
        location = mismatch.anchor_paragraph.numbering_text or "實施方式段落"
        mode = "沿用上一段的" if mismatch.inherited_reference else ""
        differences: List[str] = []
        if mismatch.missing_figures:
            differences.append(
                "OCR 尚未載入「"
                + "、".join(f"圖{number}" for number in mismatch.missing_figures)
                + "」"
            )
        if mismatch.labels_not_in_drawings:
            differences.append(
                "段落有、圖式沒有："
                + "、".join(mismatch.labels_not_in_drawings)
            )
        difference_text = "；".join(differences)
        comparison_note = (
            "；本段涉及多圖頁，已採子集合比對"
            if mismatch.comparison_mode == "page_subset"
            else ""
        )
        yield _issue(
            "OCR001",
            f"{location}{mode}參閱{figure_text}，發現圖式缺失標號"
            f"（{difference_text}{comparison_note}）。",
            "請回到 Word 確認此段落的元件標號及參閱圖號，並在 OCR 頁確認或修正相應圖片的辨識標號。",
            paragraph=mismatch.anchor_paragraph,
            start=mismatch.char_start,
            end=mismatch.char_end,
            details={
                "referenced_figures": mismatch.referenced_figures,
                "paragraph_labels": mismatch.paragraph_labels,
                "drawing_labels": mismatch.drawing_labels,
                "labels_not_in_drawings": mismatch.labels_not_in_drawings,
                "labels_not_in_paragraph": [],
                "missing_figures": mismatch.missing_figures,
                "inherited_reference": mismatch.inherited_reference,
                "comparison_mode": mismatch.comparison_mode,
                "logical_paragraph_indices": mismatch.paragraph_indices,
                "highlight_text": mismatch.highlight_text,
            },
        )


def _embodiment_figure_reference_limit_issues(
    document: PatentDocument,
) -> Iterable[PatentIssue]:
    from .figure_ocr_checker import parse_figure_references

    maximum_figures = 4
    for paragraph in document.paragraphs:
        if paragraph.section_key != "embodiments" or paragraph.is_heading:
            continue
        references = parse_figure_references(paragraph.text)
        if not references:
            continue
        figures = list(
            dict.fromkeys(
                figure
                for reference in references
                for figure in reference.figures
            )
        )
        if len(figures) <= maximum_figures:
            continue
        figure_text = "、".join(f"圖{figure}" for figure in figures)
        location = paragraph.numbering_text or "實施方式段落"
        start = references[0].start
        end = references[-1].end
        yield _issue(
            "REF007",
            f"{location}共參閱{len(figures)}張不同圖式（{figure_text}），"
            f"超過每段最多{maximum_figures}張的上限。",
            "請拆分或調整該實施方式段落，使每一段最多參閱四張不同圖式。",
            paragraph=paragraph,
            start=start,
            end=end,
            details={
                "referenced_figures": figures,
                "figure_count": len(figures),
                "maximum_figure_count": maximum_figures,
                "highlight_text": paragraph.text[start:end],
            },
        )


def _cross_section_figure_ocr_issues(
    document: PatentDocument,
    ocr_results: Sequence[Dict[str, object]],
) -> Iterable[PatentIssue]:
    from .figure_ocr_checker import find_cross_section_mismatches

    for mismatch in find_cross_section_mismatches(document, ocr_results):
        reference = mismatch.reference
        target = f"圖{reference.target_figure}"
        sources = "、".join(f"圖{number}" for number in reference.source_figures)
        roman_pair = f"{reference.roman_text}-{reference.roman_right_text}"
        if mismatch.reason == "roman_pair_mismatch":
            message = f"{target}圖說的羅馬剖切線「{roman_pair}」前後不一致。"
            suggestion = "請確認剖切線兩端是否應使用同一個羅馬數字。"
        elif mismatch.reason == "invalid_roman":
            message = f"{target}圖說的剖切線「{roman_pair}」不是有效羅馬數字。"
            suggestion = "請回到 Word 確認剖切線名稱。"
        elif mismatch.reason == "target_mismatch":
            message = (
                f"{target}記載剖切線「{roman_pair}」，但羅馬數字"
                f"{reference.roman_text}換算後是{reference.roman_value}，與圖號不一致。"
            )
            suggestion = "請確認剖視圖圖號或羅馬剖切線是否誤植。"
        elif mismatch.reason == "source_not_identified":
            message = f"{target}記載剖切線「{roman_pair}」，但無法從圖說辨識其來源圖。"
            suggestion = "請在圖式簡單說明中明確寫出剖切線所在的來源圖號。"
        elif mismatch.reason == "source_not_loaded":
            message = (
                f"{target}記載沿{sources}的剖切線「{roman_pair}」取得，"
                "但來源圖尚未載入 OCR 或尚未正確設定圖號。"
            )
            suggestion = "請在圖式標號頁載入來源圖，並確認該圖片所對應的圖號。"
        else:
            detected = "、".join(mismatch.source_drawing_labels) or "無"
            message = (
                f"{target}記載沿{sources}的剖切線「{roman_pair}」取得，"
                f"但來源圖的 OCR 結果沒有偵測到「{reference.roman_text}」"
                f"（目前偵測：{detected}）。"
            )
            suggestion = "請確認來源圖是否確實標有該羅馬剖切線，或回到圖式標號頁修正 OCR 結果。"
        yield _issue(
            "OCR002",
            message,
            suggestion,
            paragraph=reference.paragraph,
            start=reference.start,
            end=reference.end,
            details={
                "reason": mismatch.reason,
                "target_figure": reference.target_figure,
                "source_figures": list(reference.source_figures),
                "roman_text": reference.roman_text,
                "roman_right_text": reference.roman_right_text,
                "roman_value": reference.roman_value,
                "source_drawing_labels": list(mismatch.source_drawing_labels),
                "caption_text": reference.text,
            },
        )


def _unused_figure_ocr_issues(
    document: PatentDocument,
    ocr_results: Sequence[Dict[str, object]],
) -> Iterable[PatentIssue]:
    from .figure_ocr_checker import find_unused_ocr_figures

    for unused in find_unused_ocr_figures(document, ocr_results):
        yield _issue(
            "OCR003",
            f"OCR 已載入圖{unused.figure}，但全文沒有引用、參閱或圖式簡單說明記載此圖。",
            "請確認該圖是否漏寫於圖式簡單說明或實施方式；若不是本案圖式，請移除或修正圖片圖號。",
            paragraph=unused.anchor_paragraph,
            start=0 if unused.anchor_paragraph is not None else None,
            end=(
                len(unused.anchor_paragraph.text)
                if unused.anchor_paragraph is not None
                else None
            ),
            details={"unused_figure": unused.figure},
        )


def review_document(
    document: PatentDocument,
    custom_rules: Sequence[CustomTextRule] = (),
    *,
    ocr_results: Sequence[Dict[str, object]] = (),
    component_reference_whitelist: Sequence[str] = (),
) -> PatentTextReview:
    """Run all Stage 2 rules without modifying the parsed document or DOCX."""

    custom_rules = tuple(custom_rules or ())
    blacklist_rules = tuple(
        rule
        for rule in custom_rules
        if rule.rule_type == CUSTOM_RULE_BLACKLIST
    )
    similarity_whitelist = tuple(
        rule.text
        for rule in custom_rules
        if rule.rule_type == CUSTOM_RULE_WHITELIST
    )
    issues: List[PatentIssue] = []
    for producer in (
        _formal_structure_issues,
        _format_issues,
        _section_order_issues,
        _paragraph_number_issues,
        _abstract_length_issues,
        _ending_punctuation_issues,
        _symbol_issues,
        _drawing_issues,
        _terminology_issues,
        _typography_issues,
        _table_scope_issues,
    ):
        issues.extend(producer(document))
    issues.extend(
        _reference_issues(
            document,
            protected_phrases=component_reference_whitelist or (),
        )
    )
    issues.extend(
        _component_name_typo_issues(
            document,
            similarity_whitelist=similarity_whitelist,
        )
    )
    claim_subjects: List[str] = []
    claim_disclosure_coverage: Dict[str, object] = {}
    issues.extend(_claim_issues(document, claim_subjects, claim_disclosure_coverage))
    issues.extend(_embodiment_figure_reference_limit_issues(document))
    issues.extend(_embodiment_figure_ocr_issues(document, ocr_results or ()))
    issues.extend(_cross_section_figure_ocr_issues(document, ocr_results or ()))
    issues.extend(_unused_figure_ocr_issues(document, ocr_results or ()))
    issues.extend(_custom_text_issues(document, blacklist_rules))
    issues.sort(
        key=lambda issue: (
            _SEVERITY_ORDER.get(issue.severity, 99),
            -1 if issue.paragraph_index is None else issue.paragraph_index,
            -1 if issue.char_start is None else issue.char_start,
            issue.rule_id,
            issue.issue_id,
        )
    )
    return PatentTextReview(
        source_path=document.source_path,
        file_name=document.file_name,
        sha256=document.sha256,
        patent_type=document.patent_type,
        patent_title=document.patent_title,
        generated_at_utc=datetime.now(timezone.utc).isoformat(),
        claim_subjects=claim_subjects,
        claim_disclosure_coverage=claim_disclosure_coverage,
        issues=issues,
        rule_catalog=(
            list(RULE_CATALOG)
            + [_custom_text_definition(rule) for rule in blacklist_rules]
        ),
        parse_warnings=list(document.warnings),
    )
