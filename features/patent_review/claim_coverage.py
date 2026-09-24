"""Conservative, offline claim-to-disclosure evidence matching.

This is a drafting aid, not a legal support/enablement decision.  Only exact
statements and a small, fully consumed grammar can produce positive results.
Unknown text is deliberately retained as an unresolved item.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Dict, List, Optional, Sequence, Tuple

from .models import PatentParagraph


_WIDTH = str.maketrans({"，": ",", "；": ";", "：": ":", "（": "(",
                       "）": ")", "［": "[", "］": "]", "　": " "})
_QUANTITY = r"(?:至少|至多|恰好|僅)?(?:[0-9０-９]+|[一二兩三四五六七八九十百千]+|複數|數個|多個)(?:個)?"
_ARTICLE = r"(?:所述的|所述之|所述|該等|該)?"
_GUARD = re.compile(r"若|如果|當|倘|除非|只要|僅當|在.{0,40}(?:時|情況下)|"
                    r"(?:不能|不可|不得|並非|並不|不是|不|未|無|沒有|非)|"
                    r"(?:能夠|可以|可選|可|應|必須|須)|或者|或是|或|擇一|任選")
_EXCLUDED_CONTEXT = re.compile(r"不適用|不屬於|錯誤(?:的)?(?:配置|敘述|示例)|反例|反面例|"
                               r"比較例|對比例|僅供比較|不是本(?:發明|新型)|"
                               r"非本(?:發明|新型)|本(?:發明|新型)不採用|先前技術|習知")
_BRANCH = re.compile(r"(?:第[一二三四五六七八九十0-9]+|另(?:一|外一)?|其他|不同)"
                     r"[^，,；;。]{0,12}?(?:實施例|實施方式|實施態樣|方案)|"
                     r"(?:一|某)(?:個)?(?:實施例|實施方式|實施態樣)|"
                     r"替代(?:方案|方式)|可選(?:地|方案)|另一選擇")
_BRANCH_PREFIX = re.compile(r"^(?:在|於)?(?:本發明的|本新型的|本實施例的)?"
                            r"(?:(?:第[一二三四五六七八九十0-9]+|另(?:一|外一)?|其他|不同|一|某)"
                            r"(?:個)?(?:實施例|實施方式|實施態樣|方案)|替代(?:方案|方式))"
                            r"(?:中|內)?(?:,)?")
_PREDICATES = {
    "電性連接於": "electrical", "電性連接至": "electrical", "電性連接": "electrical",
    "機械連接於": "mechanical", "機械連接": "mechanical",
    "固定設置於": "fixed_place", "固定設置在": "fixed_place",
    "設置於": "place", "設置在": "place", "設於": "place",
    "位於": "located", "位在": "located",
    "固定於": "fixed", "固定在": "fixed",
    "耦接於": "couple", "耦接至": "couple", "耦接": "couple",
    "連接於": "connect", "連接至": "connect", "連接到": "connect", "連接": "connect",
    "接收": "receive", "傳送至": "send", "傳送到": "send",
    "支撐": "support",
    "對應於": "correspond", "對應": "correspond",
    "包含": "has", "包括": "has", "具有": "has", "設有": "has",
}
_PRED = "(?:" + "|".join(sorted(_PREDICATES, key=len, reverse=True)) + ")"
_POSITION = r"(?:上方|下方|上側|下側|左側|右側|前方|後方|內側|外側|內部|外部|上|下|內|外)?"
_LIMITATIONS = [
    "僅比對指定的發明／新型內容；不是法律上的支持、充分揭露或權利範圍判斷。",
    "僅精確文字及有限結構句型可自動確認；未支援的改寫、功能、條件或修飾仍保留待人工確認。",
    "不同實施例或擇一方案不拼湊為完整揭露；相似字詞、元件名稱出現或相似度不構成涵蓋證據。",
]


@dataclass(frozen=True)
class _Atom:
    kind: str
    subject: str
    obj: str = ""
    detail: str = ""


@dataclass
class _Unit:
    text: str
    start: int
    end: int
    paragraph: Optional[PatentParagraph] = None
    group: int = 0
    guard: bool = False
    blocked: bool = False
    atoms: List[_Atom] = field(default_factory=list)
    complete: bool = False
    context: str = ""


def _normal(text: str) -> str:
    """Normalize layout only, preserving case, primes, exponents and tokens."""
    text = text.translate(_WIDTH)
    return re.sub(r"\s+", lambda m: " " if m.start() and m.end() < len(text)
                  and text[m.start() - 1].isascii() and text[m.start() - 1].isalnum()
                  and text[m.end()].isascii() and text[m.end()].isalnum() else "", text).strip()


def _literal_key(text: str) -> str:
    text = _normal(text)
    text = re.sub(r"^(?:【[0-9０-９]{1,4}】|\[[0-9０-９]{1,4}\])", "", text)
    # Only a discourse opening before an explicit reference, never the
    # selective quantifier 其中一 or arbitrary words containing 其中.
    return re.sub(r"^其中(?=該|所述)", "", text)


def _number(text: str) -> str:
    text = text.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    return text.replace("兩", "二").removesuffix("個")


def _split(text: str, delimiters: str = "，,；;：:。\n") -> List[Tuple[int, int]]:
    result: List[Tuple[int, int]] = []
    stack: List[str] = []
    start = 0
    pairs = {"(": ")", "（": "）", "[": "]", "［": "］", "【": "】", "「": "」", "『": "』"}
    for pos, char in enumerate(text):
        if char in pairs:
            stack.append(pairs[char])
        elif stack and char == stack[-1]:
            stack.pop()
        elif not stack and char in delimiters:
            if text[start:pos].strip():
                left = start + len(text[start:pos]) - len(text[start:pos].lstrip())
                result.append((left, pos - len(text[start:pos]) + len(text[start:pos].rstrip())))
            start = pos + 1
    if text[start:].strip():
        result.append((start + len(text[start:]) - len(text[start:].lstrip()), len(text.rstrip())))
    return result


def _evidence(unit: _Unit) -> Dict[str, object]:
    assert unit.paragraph is not None
    return {"paragraph_index": unit.paragraph.index, "text": unit.text,
            "char_start": unit.start, "char_end": unit.end}


class _Grammar:
    def __init__(self, names: Sequence[str], subjects: Sequence[str], *, infer_context: bool = True):
        self.subjects = {_normal(s) for s in subjects if s.strip()}
        self.names = sorted({_normal(s) for s in (*names, *subjects) if s.strip()}, key=len, reverse=True)
        self.name = "(?:" + "|".join(re.escape(n) for n in self.names) + ")" if self.names else r"(?!)"
        self.entity = _ARTICLE + "(?P<name>" + self.name + ")"
        self.root = next(iter(sorted(self.subjects)), "") if infer_context else ""
        self.active = self.root
        self.list_owner = ""

    def noun(self, text: str) -> Optional[str]:
        match = re.fullmatch(self.entity, text)
        return match.group("name") if match else None

    def quantified(self, text: str, owner: str) -> Optional[List[_Atom]]:
        match = re.fullmatch(r"(?P<q>" + _QUANTITY + ")" + self.entity, text)
        if match:
            return [_Atom("has", owner, match.group("name"), _number(match.group("q")))]
        # Bound relative clause: 一設置於該基座上方的外殼.
        match = re.fullmatch(r"(?P<q>" + _QUANTITY + r")(?P<modifier>可拆卸地)?(?P<p>" + _PRED + r")"
                             + _ARTICLE + r"(?P<object>" + self.name + r")(?P<pos>" + _POSITION
                             + r")(?:的|之)(?P<child>" + self.name + r")", text)
        if match and _PREDICATES[match.group("p")] != "has":
            return [_Atom("has", owner, match.group("child"), _number(match.group("q"))),
                    _Atom(_PREDICATES[match.group("p")], match.group("child"),
                          match.group("object"), ("可拆卸|" if match.group("modifier") else "") + match.group("pos"))]
        match = re.fullmatch(r"(?P<q>" + _QUANTITY + r")沿(?P<dq>" + _QUANTITY + r")"
                             r"(?P<direction>[^，,；;。:：、]{1,40}?方向)設置(?:的|之)"
                             r"(?P<child>" + self.name + r")", text)
        if match:
            return [_Atom("has", owner, match.group("child"), _number(match.group("q"))),
                    _Atom("along", match.group("child"), match.group("direction"), _number(match.group("dq")))]
        return None

    def parse(self, unit: _Unit) -> None:
        text = _normal(unit.text)
        text = re.sub(r"^(?:【[0-9０-９]{1,4}】|\[[0-9０-９]{1,4}\])", "", text)
        unit.context = self.active
        text = _BRANCH_PREFIX.sub("", text)
        text = re.sub(r"^(?:其中(?=該|所述)|並且|以及|且|並|及)", "", text)
        # Patent prose wrappers, anchored and limited to a named invention.
        text = re.sub(r"^本(?:發明|新型|實用新型)(?:的目的在於|之目的在於|旨在)?(?:提供|提出)", "", text)
        target = re.fullmatch(r"(?:一種|一)(?P<name>" + self.name + r")", text)
        if target and target.group("name") in self.subjects:
            self.root = self.active = target.group("name")
            unit.context = self.active
            unit.atoms = [_Atom("target", self.active)]
            unit.complete = True
            return
        if not text:
            return
        negative = re.fullmatch(self.entity + r"(?:並未|並不|沒有|未|不)(?P<p>" + _PRED + r")"
                                + _ARTICLE + r"(?P<object>" + self.name + r")(?P<pos>" + _POSITION + r")", text)
        if negative and _PREDICATES[negative.group("p")] != "has":
            unit.atoms = [_Atom("not:" + _PREDICATES[negative.group("p")], negative.group("name"),
                                negative.group("object"), negative.group("pos"))]
            self.active = negative.group("name")
            unit.complete = True
            return
        # Intro + predicate, including 一種固定裝置包含… in one clause.
        named = re.match(r"^(?:一種|一)(?P<name>" + self.name + r")(?=" + _PRED + ")", text)
        if named and named.group("name") in self.subjects:
            self.root = self.active = named.group("name")
            unit.context = self.active
            unit.atoms.append(_Atom("target", self.active))
            text = text[named.end():]
        # List headers are meaningful owner context, but need no extra atom.
        if text in {"包含", "包括", "具有", "設有"} and self.active:
            self.list_owner = self.active
            unit.complete = True
            return
        owner = self.list_owner or self.root
        quantified = self.quantified(text, owner) if owner else None
        if quantified:
            unit.atoms.extend(quantified)
            self.active = quantified[0].obj
            unit.complete = True
            return
        # Canonical passive voice, deliberately only explicitly named operands.
        passive = re.fullmatch(self.entity + r"(?:被|由)" + _ARTICLE + r"(?P<agent>" + self.name
                               + r")(?P<p>" + _PRED + r")", text)
        if passive and _PREDICATES[passive.group("p")] not in {"has", "located", "place"}:
            unit.atoms.append(_Atom(_PREDICATES[passive.group("p")], passive.group("agent"), passive.group("name")))
            self.active = passive.group("name")
            unit.complete = True
            return
        # A與B連接 -> A連接B. Do not reverse either operand.
        between = re.fullmatch(self.entity + r"(?P<modifier>以可拆卸方式)?與" + _ARTICLE + r"(?P<object>" + self.name
                               + r")(?P<p>連接|耦接|電性連接|機械連接)", text)
        if between:
            unit.atoms.append(_Atom(_PREDICATES[between.group("p")], between.group("name"), between.group("object"),
                                    "可拆卸|" if between.group("modifier") else ""))
            self.active = between.group("name")
            unit.complete = True
            return
        subject = self.active
        explicit = re.match(self.entity, text)
        if explicit:
            subject = explicit.group("name")
            self.active = subject
            text = text[explicit.end():]
        if not subject:
            return
        direction = re.fullmatch(r"沿(?P<dq>" + _QUANTITY + r")(?P<direction>[^，,；;。:：、]{1,40}?方向)設置", text)
        if direction:
            unit.atoms.append(_Atom("along", subject, direction.group("direction"), _number(direction.group("dq"))))
            self.active = subject
            unit.complete = True
            return
        modifier = ""
        if text.startswith("可拆卸地"):
            modifier, text = "可拆卸|", text[len("可拆卸地"):]
        pred = re.match(r"(?P<p>" + _PRED + ")", text)
        if not pred:
            return
        kind = _PREDICATES[pred.group("p")]
        tail = text[pred.end():]
        if kind == "has":
            atoms: List[_Atom] = []
            # Only conjunction lists; OR and nested unknown content stay unknown.
            for part in re.split(r"(?:以及|及|與|和|、)", tail):
                value = self.quantified(part, subject)
                if not value:
                    return
                atoms.extend(value)
            if atoms:
                unit.atoms.extend(atoms)
                self.active = subject
                unit.complete = True
            return
        obj = re.fullmatch(_ARTICLE + r"(?P<object>" + self.name + r")(?:(?:的|之))?(?P<pos>" + _POSITION + r")", tail)
        if obj:
            unit.atoms.append(_Atom(kind, subject, obj.group("object"), modifier + obj.group("pos")))
            self.active = subject
            unit.complete = True


def _units(text: str, paragraph: Optional[PatentParagraph], group: int) -> List[_Unit]:
    result: List[_Unit] = []
    # Guard the entire sentence, so a conditional antecedent cannot be lost
    # merely because the consequent starts after a comma.
    for sentence_start, sentence_end in _split(text, "。"):
        sentence = text[sentence_start:sentence_end]
        guarded = False
        for start, end in _split(sentence):
            start += sentence_start
            end += sentence_start
            # A condition/choice begins at its own clause and can govern the
            # following clauses; it must not erase independent earlier facts.
            # 可拆卸 is only consumed by the bounded modifier grammar above.
            guarded = guarded or bool(_GUARD.search(re.sub(r"可拆卸(?:地|方式)", "", text[start:end])))
            result.append(_Unit(text[start:end], start, end, paragraph, group, guarded))
    return result


def _same_atom_except_detail(left: _Atom, right: _Atom) -> bool:
    if (left.kind.removeprefix("not:"), left.subject, left.obj) != (right.kind.removeprefix("not:"), right.subject, right.obj):
        return False
    if left.kind != right.kind:
        negative, positive = (left, right) if left.kind.startswith("not:") else (right, left)
        return negative.detail == positive.detail or (not negative.detail and positive.detail == "可拆卸|")
    if left.kind in {"has", "along"}:
        return left.detail != right.detail
    # Detachability is an added way of connecting, not a contradiction of a
    # plain connection. Spatial positions still require exact compatibility.
    return left.detail.removeprefix("可拆卸|") != right.detail.removeprefix("可拆卸|")


def _covering_atom(index: Dict[_Atom, _Unit], atom: _Atom) -> Optional[_Unit]:
    direct = index.get(atom)
    if direct is not None:
        return direct
    if atom.kind not in {"has", "along", "target"} and not atom.detail.startswith("可拆卸|"):
        # This is intentionally one-way: an explicit detachable connection
        # demonstrates a connection, but a plain connection says nothing
        # about whether detachment is possible.
        return index.get(_Atom(atom.kind, atom.subject, atom.obj, "可拆卸|" + atom.detail))
    return None


def analyze_claim_disclosure(
    claim_body: str,
    disclosure_paragraphs: Sequence[PatentParagraph],
    component_names: Sequence[str] = (),
    subject_names: Sequence[str] = (),
) -> Dict[str, object]:
    """Return source-anchored evidence without editing either source.

    ``covered`` means this bounded matcher found evidence for every retained
    statement in one coherent group, not that a legal requirement is met.
    """
    counts = {"covered": 0, "possible_gap": 0, "conflict": 0, "uncertain": 0}
    result: Dict[str, object] = {"status": "unavailable", "items": [], "counts": counts,
                               "limitations": list(_LIMITATIONS)}
    paragraphs = [p for p in disclosure_paragraphs if p.text.strip() and (not p.is_heading or p.content_text.strip())]
    if not claim_body.strip() or not paragraphs:
        result["limitations"].append("請求項1或發明／新型內容沒有可比對文字。")
        return result
    if len(claim_body) > 100_000 or sum(len(p.text) for p in paragraphs) > 1_000_000 or len(component_names) + len(subject_names) > 4_000:
        result["limitations"].append("文字或名稱數量超過保守比對上限，本次不作自動涵蓋判定，請人工核對。")
        return result
    subjects = list(subject_names)
    if not subjects:
        target = re.match(r"\s*(?:一種|一)([^，,；;：:\n]+?)(?=包含|包括|具有|，|,|；|;|：|:)", claim_body)
        if target:
            subjects.append(target.group(1))
    grammar = _Grammar(component_names, subjects)
    claim_units = _units(claim_body, None, 0)
    for unit in claim_units:
        grammar.parse(unit)

    sources: List[_Unit] = []
    groups = {0}
    group = 0
    source_grammar = _Grammar(component_names, subjects, infer_context=False)
    excluded_context = False
    pending_condition = False
    table_seen = False
    source_ranges: Dict[int, Tuple[int, int]] = {}
    for paragraph in paragraphs:
        # Every new explicit branch starts a separate evidence universe, even
        # when authors use the same ambiguous label (e.g. "另一實施例").
        body_start = 0
        if paragraph.is_heading:
            body_start = paragraph.text.find(paragraph.content_text)
            if body_start < 0:
                marker = re.match(r"\s*[【\[][^】\]]+[】\]]\s*", paragraph.text)
                if marker is None:
                    continue
                body_start = marker.end()
        source_ranges[id(paragraph)] = (body_start, len(paragraph.text))
        para_units = _units(paragraph.text[body_start:], paragraph, group)
        paragraph_excluded = bool(_EXCLUDED_CONTEXT.search(paragraph.text))
        table_seen = table_seen or paragraph.source_kind == "table"
        for unit in para_units:
            unit.start += body_start
            unit.end += body_start
            if _BRANCH.search(unit.text):
                group += 1
                groups.add(group)
                source_grammar = _Grammar(component_names, subjects, infer_context=False)
                excluded_context = False
                pending_condition = False
            unit.group = group
            excluded_context = excluded_context or paragraph_excluded
            unit.blocked = excluded_context or pending_condition or paragraph.source_kind == "table"
            unit.guard = unit.guard or unit.blocked
            source_grammar.parse(unit)
            if not unit.complete and not re.match(_ARTICLE + source_grammar.name, _normal(unit.text)):
                # Unknown owners/prefaces must not lend the previous subject
                # to a following bare predicate. Known explicit subjects can
                # still establish context even for an unsupported property.
                source_grammar.active = ""
                source_grammar.list_owner = ""
        sources.extend(para_units)
        content = paragraph.text[body_start:].strip()
        if re.match(r"^(?:當|若|如果|倘|除非|只要|僅當|在.{0,40}(?:時|情況下))", content):
            # A condition-only preamble can govern subsequent Word paragraphs.
            if not content.endswith("。") or re.search(r"(?:時|情況下)[，,:：。]?$", content):
                pending_condition = True
    if table_seen:
        result["limitations"].append("表格內容保留人工核對，不跨儲存格拼接或自動認定涵蓋。")
    if len(claim_units) * max(1, len(sources)) > 5_000_000:
        result["limitations"].append("子句比對量超過保守運算上限，本次不作自動涵蓋判定，請分段人工核對。")
        return result

    # A complete original statement is safe to match verbatim, including
    # unsupported material/function/conditional language.  Match complete
    # sentences, never substring occurrences inside negated or modal prose.
    exact_sentences: List[Tuple[int, int, List[_Unit]]] = []
    for left, right in _split(claim_body, "。"):
        key = _normal(claim_body[left:right])
        for paragraph in paragraphs:
            source_range = source_ranges.get(id(paragraph))
            if source_range is None or paragraph.source_kind == "table":
                continue
            body_start, _ = source_range
            for start, end in _split(paragraph.text[body_start:], "。"):
                start += body_start
                end += body_start
                if _normal(paragraph.text[start:end]) == key:
                    claim_first = next((u for u in claim_units if left <= u.start < right), None)
                    source_parts = [s for s in sources if s.paragraph is paragraph and start <= s.start < end]
                    if not claim_first or not source_parts or len({s.group for s in source_parts}) != 1:
                        continue
                    # Exact conditional statements are valid only if the
                    # source has no *additional* inherited guard/context.
                    if any(s.blocked for s in source_parts):
                        continue
                    if _EXCLUDED_CONTEXT.search(paragraph.text):
                        continue
                    # A copied bare predicate must not borrow a different
                    # preceding owner. Explicitly named subjects are stable.
                    explicit_subject = bool(re.match(_ARTICLE + grammar.name, key))
                    named_target = bool(re.match(r"^(?:一種|一)(?:" + "|".join(re.escape(s) for s in subjects) + r")", key)) if subjects else False
                    same_parsed_context = (claim_first.complete and source_parts[0].complete
                                           and claim_first.atoms == source_parts[0].atoms)
                    explicit_conditional = bool(_GUARD.search(key)) and any(
                        re.match(_ARTICLE + grammar.name, _normal(u.text)) and u.complete
                        for u in claim_units if left <= u.start < right
                    )
                    if not (explicit_subject or named_target or same_parsed_context or explicit_conditional):
                        continue
                    exact_sentences.append((left, right, [_Unit(paragraph.text[start:end], start, end, paragraph, source_parts[0].group)]))

    matched_by_unit: List[Dict[int, List[_Unit]]] = []
    conflicts_by_unit: List[List[_Unit]] = []
    pool_by_group: Dict[int, List[_Unit]] = {g: [] for g in groups}
    atoms_by_group: Dict[int, Dict[_Atom, _Unit]] = {g: {} for g in groups}
    literal_by_group: Dict[int, Dict[Tuple[str, str], _Unit]] = {g: {} for g in groups}
    explicit_literal_by_group: Dict[int, Dict[str, _Unit]] = {g: {} for g in groups}
    conflicts_index: Dict[Tuple[str, str, str], List[Tuple[_Atom, _Unit]]] = {}
    for source in sources:
        if source.complete and not source.blocked:
            for atom in source.atoms:
                if not source.guard or atom.kind.startswith("not:"):
                    conflicts_index.setdefault((atom.kind.removeprefix("not:"), atom.subject, atom.obj), []).append((atom, source))
        if source.guard:
            continue
        pool_by_group[source.group].append(source)
        literal_by_group[source.group].setdefault((_literal_key(source.text), source.context), source)
        explicit_literal_by_group[source.group].setdefault(_literal_key(source.text), source)
        if source.complete:
            for atom in source.atoms:
                atoms_by_group[source.group].setdefault(atom, source)
    for unit in claim_units:
        candidates: Dict[int, List[_Unit]] = {}
        conflicts: List[_Unit] = []
        for left, right, evidence in exact_sentences:
            if left <= unit.start and unit.end <= right:
                candidates.setdefault(evidence[0].group, []).extend(evidence)
        if not unit.guard:
            for candidate_group in groups:
                # Exact clauses are accepted only when fully parsed or when
                # their explicit named subject and surrounding subject agree.
                literal_key = _literal_key(unit.text)
                literal = literal_by_group[candidate_group].get((literal_key, unit.context))
                explicit_literal = bool(re.match(_ARTICLE + grammar.name, literal_key))
                if literal is None and explicit_literal:
                    # Explicit operands make preceding discourse subject
                    # irrelevant, enabling reordered verbatim unknown clauses.
                    literal = explicit_literal_by_group[candidate_group].get(literal_key)
                if literal and (unit.complete or explicit_literal):
                    # A fully parsed inherited predicate must retain its
                    # resolved operands, even when the words are identical.
                    if not unit.complete or (literal.complete and unit.atoms == literal.atoms):
                        candidates.setdefault(candidate_group, []).append(literal)
                if unit.complete and unit.atoms:
                    chosen: List[_Unit] = []
                    for atom in unit.atoms:
                        hit = _covering_atom(atoms_by_group[candidate_group], atom)
                        if hit is None:
                            break
                        chosen.append(hit)
                    else:
                        candidates.setdefault(candidate_group, []).extend(chosen)
        if unit.complete:
            for atom in unit.atoms:
                for other, source in conflicts_index.get((atom.kind.removeprefix("not:"), atom.subject, atom.obj), []):
                    if _same_atom_except_detail(atom, other):
                        conflicts.append(source)
        matched_by_unit.append(candidates)
        conflicts_by_unit.append(conflicts)

    substantive = [i for i, unit in enumerate(claim_units) if unit.atoms or not unit.complete]
    common = set(groups)
    for i in substantive:
        common.intersection_update(matched_by_unit[i])
    preferred = min(common) if common else None
    items: List[Dict[str, object]] = []
    for i, unit in enumerate(claim_units):
        if unit.complete and not unit.atoms:
            continue  # A consumed list header is attached to the following atoms.
        candidates = matched_by_unit[i]
        evidence: List[_Unit] = []
        candidate_only = False
        if candidates:
            chosen_group = preferred if preferred in candidates else min(candidates)
            evidence = candidates[chosen_group]
            status = "covered"
            reason = "在同一揭露分組找到完整文字或受支援結構的對應證據。"
            if preferred is None and all(matched_by_unit[j] for j in substantive):
                status = "uncertain"
                reason = "各敘述分別出現在不同實施例或方案，不能拼湊認定完整涵蓋。"
            same_group_conflicts = [s for s in conflicts_by_unit[i] if s.group == chosen_group]
            if same_group_conflicts:
                status = "conflict"
                reason = "同一揭露分組也出現相反關係、數量或方位限制；不能只取相符句而忽略衝突，請人工核對。"
                evidence = evidence + same_group_conflicts[:3]
        elif conflicts_by_unit[i]:
            status = "conflict"
            reason = "找到相同構件但關係正反、明確數量或方位限制不同；請核對原文。"
            evidence = conflicts_by_unit[i][:3]
        elif unit.guard or not unit.complete or table_seen:
            status = "uncertain"
            reason = "此敘述含尚未可靠解析的条件、否定、選擇、功能或修飾；未略過任何殘餘文字。"
        else:
            status = "possible_gap"
            reason = "未在指定內容中找到此完整敘述的足夠對應證據；不表示已判定原稿缺漏。"
        if not evidence and status in {"possible_gap", "uncertain"}:
            mentioned = [name for name in grammar.names if name in _normal(unit.text)]
            if mentioned:
                ranked = []
                for source in sources:
                    shared = [name for name in mentioned if name in _normal(source.text)]
                    if shared:
                        ranked.append((sum(len(name) for name in shared), len(shared), source))
                ranked.sort(key=lambda row: (-row[0], -row[1], row[2].paragraph.index, row[2].start))
                evidence = [row[2] for row in ranked[:2]]
                candidate_only = bool(evidence)
                if candidate_only:
                    reason += " 以下僅為含相同元件的相關候選文字，不構成涵蓋證據；不同揭露分組不合併為證明。"
        seen = set()
        anchored = []
        for source in evidence:
            key = (source.paragraph.index, source.start, source.end)
            if key not in seen:
                seen.add(key)
                anchor = _evidence(source)
                if candidate_only:
                    anchor["evidence_kind"] = "candidate"
                    anchor["disclosure_group"] = source.group
                anchored.append(anchor)
        items.append({"claim_text": unit.text, "claim_start": unit.start, "claim_end": unit.end,
                      "status": status, "reason": reason, "evidence": anchored})
        counts[status] += 1
    result["items"] = items
    result["status"] = "covered" if items and all(i["status"] == "covered" for i in items) else "needs_review"
    return result
