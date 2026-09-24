"""Opt-in offline research workbench; retrieval is NOT a coverage verdict.

No production rule, whitelist, document or review object is modified here.
The 100-case study motivates candidate retrieval and explicit review guards.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from html import escape
import re
from threading import Event

from .models import PatentDocument
from .symbol_transfer import extract_document_symbols

VERSION = "yang-100-preview-1"
_MARKER = re.compile(r"(?:【|\[)?請求項\s*([0-9０-９]+)(?:】|\])?\s*[:：.]?")
_INDEPENDENT = re.compile(r"^\s*一種")
_DEPENDENT = re.compile(r"^\s*(?:如|依據|根據|依)\s*(?:申請專利範圍)?\s*請求項")
_BRANCH = re.compile(r"(?:第[一二三四五六七八九十0-9]+|另一|另一些|其他|又一|再一|不同).{0,10}?(?:實施例|實施方式|實施態樣)|替代方案|以.{1,12}取代|省略")
_CHECKS = (
    ("branch", _BRANCH, "態樣／替換／省略：不得跨分支合併候選。"),
    ("condition", re.compile(r"當|若|如果|倘|除非|僅當|情況下|狀態|步驟|依序|接著"), "核對條件、狀態、流程先後及執行者。"),
    ("logic", re.compile(r"不|未|無|非|僅|只有|或|以及|及|且"), "保留否定、僅、AND/OR及作用範圍。"),
    ("quantity", re.compile(r"複數|多個|數個|至少|至多|每一|各該|相應|對應|[一二兩三四五六七八九十0-9]+(?:個|件|組|片|層|條)|(?<!第)[一二兩三四五六七八九十](?=[\u4e00-\u9fff])"), "核對總數、每組／成員數與對應對象；單複數不作自動判斷。"),
    ("direction", re.compile(r"第一|第二|第三|上方|下方|左右|前後|內側|外側|軸線|方向|周向|相反"), "保留第一／第二、方向、基準及鏡像角色。"),
    ("numeric", re.compile(r"[0-9０-９]|[=<>≤≥%％]|大於|小於|等於|比值|總和"), "核對數值、單位、範圍等號、比例分母及總和。"),
    ("identity", re.compile(r"即|實際為|兼具|例如|同樣|相同|對稱|所述"), "名稱／上下位／兼具功能須有局部證據，不作全域同義。"),
    ("prior_art", re.compile(r"習知|先前技術|比較例|對比例|美國專利|反例"), "含背景或比較敘述；不能直接作為本案證據。"),
    ("formula", re.compile(r"公式|如下式|cos|sin|[=θ∑]"), "公式須核對完整原文、大小寫、下標與運算子。"),
)
LABELS = {"literal_candidate": "原句候選（未判定涵蓋）",
          "rewrite_candidate": "改寫候選（待人工核對）",
          "no_candidate": "未找到候選（不代表原稿缺漏）",
          "source_review": "來源需核對"}


def _spans(text):
    """Keep exact source offsets; do not split inside parentheses/formulas."""
    stack, start = [], 0
    pairs = {"(": ")", "（": "）", "[": "]", "【": "】", "「": "」"}
    for i, ch in enumerate(text):
        if ch in pairs:
            stack.append(pairs[ch])
        elif stack and ch == stack[-1]:
            stack.pop()
        elif not stack and ch in "，,；;。\n：:":
            if text[start:i].strip():
                yield start + len(text[start:i]) - len(text[start:i].lstrip()), len(text[:i].rstrip())
            start = i + 1
    if text[start:].strip():
        yield start + len(text[start:]) - len(text[start:].lstrip()), len(text.rstrip())


def _key(text):
    # Case, number, first/second, negation, prime, unit and formula preserved.
    return re.sub(r"\s+", "", text).translate(str.maketrans("，；：（）", ",;:()"))


def _flags(text):
    return [code for code, pattern, _ in _CHECKS if pattern.search(text)]


def _anchor(p, start, end):
    return {"paragraph_index": p.index, "source_path": p.source_path,
            "numbering_text": p.numbering_text, "char_start": start,
            "char_end": end, "text": p.text[start:end]}


def _claims(document, warnings):
    claims, current = [], None
    for p in document.paragraphs:
        if p.is_heading or (p.section_key != "claims" and p.major_section_key != "claims"):
            continue
        if p.source_kind == "table":
            warnings.append(f"請求項段落{p.index}在表格內，未自動拆項。")
            continue
        matches = [m for m in _MARKER.finditer(p.text)
                   if not p.text[:m.start()].strip() or p.text[:m.start()].rstrip().endswith("。")]
        number = None
        if not matches or matches[0].start() > 0:
            auto = re.fullmatch(r"\s*(?:【?請求項\s*)?([0-9０-９]+)(?:】|[.．、)])?\s*", p.numbering_text)
            number = int(auto.group(1)) if auto else None
        regions = [(int(m.group(1)), m.end(), matches[i + 1].start() if i + 1 < len(matches) else len(p.text))
                   for i, m in enumerate(matches)] if matches else [(number, 0, len(p.text))]
        if matches and matches[0].start() > 0:
            regions.insert(0, (number, 0, matches[0].start()))
        for number, start, end in regions:
            if number is not None:
                head = p.text[start:end].lstrip()
                kind = "independent" if _INDEPENDENT.match(head) else "dependent" if _DEPENDENT.match(head) else "unknown"
                current = {"number": number, "kind": kind, "heading": head.split("，")[0],
                           "fragments": [], "claim_id": len(claims)}
                claims.append(current)
                if kind == "unknown":
                    warnings.append(f"請求項{number}項首類型不明；仍保留實施方式候選，未納入獨立標的內容對照。")
            if current is None:
                if p.text[start:end].strip():
                    warnings.append(f"請求項段落{p.index}找不到可辨識的項號，未默認為請求項1。")
                continue
            first = True
            for left, right in _spans(p.text[start:end]):
                anchor = _anchor(p, start + left, start + right)
                # Only skip a leading dependency header, never an embedded
                # reference inside an independent assembly claim.
                if first and current["kind"] == "dependent" and _DEPENDENT.match(anchor["text"]):
                    first = False
                    continue
                first = False
                if anchor["text"].strip() in {"包含", "包括", "具有", "設有", "及", "以及", "其中"}:
                    continue  # A list connector alone is not a technical feature.
                anchor["incomplete_source"] = bool(p.image_relationship_ids)
                current["fragments"].append(anchor)
    duplicates = [n for n, count in Counter(c["number"] for c in claims).items() if count > 1]
    if duplicates:
        warnings.append("重複項號保留為不同資料列，請核對：" + "、".join(map(str, duplicates)))
    if not claims:
        warnings.append("未找到可解析的請求項；本次沒有完成對照。")
    return claims


def _search_key(text, symbols):
    """Recall-only transformations. NEVER use this as semantic equivalence."""
    text = _key(text)
    for name, label in symbols:
        text = re.sub(re.escape(name + label) + r"(?![0-9A-Za-z'′])", name, text)
    text = re.sub(r"^(?:於是|其中|具體來說|也就是說)[,，]?", "", text)
    text = re.sub(r"本(?:發明|新型)", "", text)
    text = re.sub(r"所述的|所述|該等|該", "", text)
    text = re.sub(r"包括|具有|設有", "包含", text)
    text = re.sub(r"設置在|設於", "設置於", text)
    return text


def _grams(text):
    return {text[i:i + 2] for i in range(len(text) - 1)}


def analyze_syntax_lab(document: PatentDocument, *, cancel: Event | None = None):
    warnings = []
    claims = _claims(document, warnings)
    transfer = extract_document_symbols(document)
    symbols = sorted({(e.name, e.label) for e in transfer.full_entries}, key=lambda x: -len(x[0] + x[1]))
    sources = {"disclosure": [], "embodiments": []}
    group = {"disclosure": 0, "embodiments": 0}
    for p in document.paragraphs:
        if p.is_heading or p.section_key not in sources:
            continue
        section = p.section_key
        for left, right in _spans(p.text):
            a = _anchor(p, left, right)
            if _BRANCH.search(a["text"]):
                group[section] += 1
            a.update(group=group[section], paragraph_text=p.text,
                     flags=_flags(p.text), incomplete_source=bool(p.image_relationship_ids) or p.source_kind == "table")
            a["_key"] = _key(a["text"])
            a["_grams"] = _grams(_search_key(a["text"], symbols))
            sources[section].append(a)
    # Bound memory and pair scoring explicitly; never silently claim success.
    if any(len(v) > 8000 for v in sources.values()) or sum(len(c["fragments"]) for c in claims) > 1500:
        return {"version": VERSION, "status": "limited", "warnings": warnings + ["文字量超出內測上限，未執行候選比對。"],
                "claims": [], "rows": []}
    indexes = {}
    for section, items in sources.items():
        index = defaultdict(set)
        for i, a in enumerate(items):
            for gram in a["_grams"]:
                index[gram].add(i)
        indexes[section] = index
        if not items:
            warnings.append(("發明／新型內容" if section == "disclosure" else "實施方式") + "未擷取到文字，不代表已涵蓋。")
    rows = []
    for claim in claims:
        sections = ("disclosure", "embodiments") if claim["kind"] == "independent" else ("embodiments",)
        for a in claim["fragments"]:
            if cancel is not None and cancel.is_set():
                return {"version": VERSION, "status": "cancelled", "warnings": ["已取消，未完成。"], "claims": [], "rows": []}
            q = _grams(_search_key(a["text"], symbols))
            for section in sections:
                votes = Counter(i for gram in q for i in indexes[section].get(gram, ()))
                ranked = []
                for i, shared in votes.most_common(128):
                    s = sources[section][i]
                    score = 2 * shared / max(1, len(q) + len(s["_grams"]))
                    literal = _key(a["text"]) == s["_key"]
                    if literal or (shared >= 2 and score >= .24):
                        ranked.append((literal, score, s))
                ranked.sort(key=lambda item: (-item[0], -item[1], item[2]["paragraph_index"], item[2]["char_start"]))
                evidence = []
                for literal, score, s in ranked[:3]:
                    evidence.append({k: v for k, v in s.items() if not k.startswith("_")} |
                                    {"match_kind": "literal" if literal else "rewrite", "retrieval_score": round(score, 3)})
                checks = set(_flags(a["text"]))
                for e in evidence:
                    checks.update(e["flags"])
                status = "literal_candidate" if evidence and evidence[0]["match_kind"] == "literal" else "rewrite_candidate" if evidence else "no_candidate"
                if a["incomplete_source"] or any(e["incomplete_source"] for e in evidence):
                    status = "source_review"
                rows.append({"claim_number": claim["number"], "claim_id": claim["claim_id"],
                             "claim_kind": claim["kind"], "section": section,
                             "claim": a, "status": status, "checks": sorted(checks), "evidence": evidence})
    return {"version": VERSION, "status": "candidates_only",
            "warnings": warnings, "claims": [{k: v for k, v in c.items() if k != "fragments"} for c in claims],
            "rows": rows,
            "limitations": [
                "僅作離線候選定位，不是涵蓋、正確率或法律判斷；原句候選也須核對語境。",
                "內容預設各獨立標的；實施方式列各項新增敘述；尚未驗證完整依附組合。",
                "最多呈現三個候選。跨段／跨態樣不合併為證明；檢索分數不是信心或涵蓋率。",
                "元件標號僅按本文件符號表輔助檢索，保留原文；表格／影像內容需核對原稿。",
            ]}


def render_syntax_lab(report, *, claim_id=None, section=None):
    """Escaped, self-contained HTML, usable in the dialog or a local report."""
    def original(text):
        return "<p>" + escape(text).replace("\n", "<br>") + "</p>"
    parts = ["<html><head><meta charset='utf-8'></head><body><h2>語法對照研究台（內測）</h2>",
             "<p>手動唯讀分析；不修改文件、不加入正式錯誤清單。候選不表示已涵蓋。</p>"]
    for message in report.get("warnings", []) + report.get("limitations", []):
        parts.append("<p>" + escape(message) + "</p>")
    labels = {code: label for code, _, label in _CHECKS}
    shown = False
    for row in report["rows"]:
        if claim_id is not None and row["claim_id"] != claim_id or section is not None and row["section"] != section:
            continue
        shown = True
        a = row["claim"]
        parts.append(f"<hr><h3>請求項{row['claim_number']} → {'內容' if row['section'] == 'disclosure' else '實施方式'}：{LABELS[row['status']]}</h3>")
        parts.append(f"<p>請求項原文（段落{a['paragraph_index']}，字元{a['char_start']}:{a['char_end']}）</p>" + original(a["text"]))
        for code in row["checks"]:
            parts.append("<p>• " + escape(labels[code]) + "</p>")
        if row["status"] == "source_review":
            parts.append("<p>有影像或表格來源，文字可能不完整，請開啟原稿核對。</p>")
        for e in row["evidence"]:
            where = f"段落{e['paragraph_index']} {e['numbering_text']}／字元{e['char_start']}:{e['char_end']}／保守分組{e['group']}"
            parts.append(f"<p><b>{escape(where)}</b>（候選，不作證明）</p>" + original(e["text"]))
            parts.append("<p>完整段落語境：</p>" + original(e["paragraph_text"]))
    if not shown:
        parts.append("<p>此篩選沒有對照列。附屬項請切換至實施方式；若有缺章節、項號或運算上限提示，本次未完成對照。</p>")
    parts.append("</body></html>")
    return "".join(parts)
