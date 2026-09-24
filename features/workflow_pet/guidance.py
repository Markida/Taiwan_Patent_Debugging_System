"""Offline, read-only workflow advice. Never run checks or edit user content."""

from collections import Counter
from dataclasses import dataclass


@dataclass(frozen=True)
class Choice:
    button: str = ""
    feature: str = ""
    target: str = ""
    effect: str = ""


@dataclass(frozen=True)
class Guidance:
    key: str
    text: str
    button: str = ""
    feature: str = ""
    target: str = ""
    alternative: Choice = Choice()


class GuidanceMemory:
    """Session-only preferences; cache findings once per completed review.

    Viewing a finding is NOT confirmation that it is correct or resolved.
    A new review or document resets the optional whitelist suggestion.
    """

    def __init__(self):
        self.document = None
        self.review = None
        self.repeated = ()
        self.skip_repeated = False
        self.visited = set()

    def sync_review(self, page):
        document = getattr(page, "document", None)
        review = getattr(page, "review", None)
        if document is self.document and review is self.review:
            return
        self.document, self.review = document, review
        self.skip_repeated = False
        self.visited.clear()
        counts = Counter()
        for issue in getattr(review, "issues", ()):
            if (getattr(issue, "rule_id", "") == "REF005"
                    and getattr(issue, "section_key", "") == "embodiments"):
                term = (getattr(issue, "details", {}) or {}).get("candidate", "")
                if term:
                    counts[str(term)] += 1
        self.repeated = tuple(counts.most_common(1)) if counts and max(counts.values()) >= 3 else ()

    def visit_issue(self, page, issue_id):
        self.sync_review(page)
        if issue_id:
            self.visited.add(issue_id)


def startup_greeting(now):
    """Use the computer's local wall time; no network or locale dependency."""
    greeting = "早安" if 5 <= now.hour < 12 else "午安" if 12 <= now.hour < 18 else "晚安"
    weekday = "一二三四五六日"[now.weekday()]
    return (f"{greeting}，喵～我是墨墨！\n"
            f"今天是 {now.year} 年 {now.month} 月 {now.day} 日（星期{weekday}），\n"
            f"現在時間 {now:%H:%M}。\n"
            "我是小助手，有操作困難請找我")


def _busy(page, name):
    return bool(getattr(getattr(page, name, None), "busy", False))


def _text(widget):
    return widget.text() if widget is not None else ""


def conversion_scope(page):
    """Cheap editor identity for advice about an exported revision."""
    editor = getattr(page, "preview_output", None)
    template = getattr(page, "template_combo", None)
    return (getattr(page, "_preview_source", ""),
            _text(getattr(page, "source_line", None)),
            editor.document().revision() if editor is not None else -1,
            _text(getattr(page, "output_line", None)),
            template.currentIndex() if template is not None else -1,
            getattr(page, "_dictionary_revision", -1))


def _review_guidance(page, memory):
    if _busy(page, "_review_tasks"):
        return Guidance("review-busy", "正在讀取與檢核文件，完成後可點選結果定位原文。需要中止時可按 Esc 回首頁。")
    if getattr(page, "document", None) is None:
        return Guidance("review-empty", "先拖入完整專利說明書 DOCX，或選擇文件；若目前只有 PDF 圖式，也可先辨識圖片，再補文件。",
                        "找到選檔按鈕", target="select_button",
                        alternative=Choice("先處理圖式", feature="patent_ocr"))
    memory.sync_review(page)
    if getattr(page, "review", None) is None:
        return Guidance("review-pending", "文件已載入，但還沒有完成的檢核結果。請等待檢核，或使用右下角重新檢核；這不代表文件沒有問題。",
                        "找到重新檢核", target="reload_button")
    if getattr(page, "document_whitelist_load_error", "") or getattr(page, "custom_rule_load_error", ""):
        return Guidance("review-rules-unavailable", "本次有自訂規則或本文件白名單讀取失敗，結果可能不完整。可確認路徑後重新檢核，也可先查看已產生的結果。",
                        "找到重新檢核", target="reload_button",
                        alternative=Choice("先看已有結果", target="issue_table"))
    count = len(page.review.issues)
    terms = tuple(getattr(page, "document_whitelist_terms", ()))
    if getattr(page, "_guidance_review_whitelist", terms) != terms:
        return Guidance("review-whitelist-pending", "本文件白名單已調整，但還沒有套用到這輪結果。請按右下角重新檢核；若 Word 也有修訂，先在 Word 存檔。也可先查看其他結果，現有提示暫時仍會保留。",
                        "找到重新檢核", target="reload_button",
                        alternative=Choice("先看其他檢核結果", target="issue_table"))
    if memory.repeated and not memory.skip_repeated:
        term, repetitions = memory.repeated[0]
        term = term[:24] + ("…" if len(term) > 24 else "")
        return Guidance("review-repeated", f"實施方式的「{term}」有 {repetitions} 筆相似詞警告。"
                        "若你確認名稱正確、屬於無標號元件，可在右下角「實施方式段落出現之無標號元件」加入本文件白名單，排除相似詞警告。"
                        "有標號的元件勿用此處排除；錯字應回 Word 修訂。不想加入也可先處理其他結果，我不會自動排除。",
                        "前往本文件白名單", target="document_whitelist_input",
                        alternative=Choice("暫不加入，先看其他結果", target="issue_table", effect="skip_repeated"))
    if count:
        progress = (f"你已點閱 {len(memory.visited)} 筆；點閱不代表已修正。" if memory.visited else "")
        return Guidance("review-progress" if memory.visited else "review-issues",
                        f"目前仍有 {count} 筆檢核提示。{progress}"
                        "可繼續看原文、回 Word 修訂並存檔，再按右下角重新檢核。"
                        "若你已大致確認，也可先核對符號清單並進入圖式標號；未處理提示仍會保留。",
                        "繼續查看檢核結果", target="issue_table",
                        alternative=Choice("已初步確認，前往圖式", feature="patent_ocr"))
    return Guidance("review-ready", "本次檢核沒有列出問題，仍請人工核對符號清單與原文。可前往圖式標號；若 Word 剛有修改，請先存檔並重新檢核。",
                    "前往圖式標號", "patent_ocr",
                    alternative=Choice("先重新檢核", target="reload_button"))


def _ocr_guidance(page):
    if _busy(page, "_pdf_tasks"):
        return Guidance("ocr-pdf", "正在載入 PDF／圖片與已保存的結果。完成後先確認案件、圖片方向，以及是否載入了同名檔案的舊結果。")
    if _busy(page, "_rotation_tasks"):
        return Guidance("ocr-rotation", "正在旋轉圖片。完成後先核對方向；若需重新辨識圖片標號，請使用第二步。")
    if _busy(page, "_orientation_tasks"):
        return Guidance("ocr-orientation", "正在逐張判斷方向並自動轉正；原圖不會被覆蓋。方向證據不足的圖片會保留原狀，稍後請人工核對。")
    if getattr(page, "_ocr_job_active", False):
        return Guidance("ocr-busy", "第二步正在辨識圖片標號。完成後先核對紅框的低信心標號，再查看其他標號；低信心不一定錯誤，高信心也不保證正確。")
    images, results = getattr(page, "image_paths", ()), getattr(page, "all_results", ())
    if not images:
        return Guidance("ocr-empty", "先拖入 PDF 或選擇多張圖片，確認方向與清晰度。若尚未準備標號清單，也可先到文件偵錯擷取。",
                        "找到圖片按鈕", target="image_button",
                        alternative=Choice("先擷取文件清單", feature="patent_review"))
    if not results:
        if getattr(page, "_heading_stage_enabled", False):
            return Guidance("ocr-oriented", f"已檢查 {len(images)} 張圖片方向。請核對方向；尚未辨識數字標號，接著按第二步。完成後圖式結果與人工修改會保存在本機。",
                            "找到第二步", target="run_button")
        return Guidance("ocr-ready", f"已載入 {len(images)} 張圖片。先核對方向，可嘗試第一步自動轉正；若提示缺少方向模型，改用手動旋轉，再按第二步辨識標號。方向已正確也可直接使用第二步。",
                        "找到第一步", target="auto_rotate_button",
                        alternative=Choice("方向正確，找到第二步", target="run_button"))
    status = _text(getattr(page, "result_save_status", None))
    if "尚未保存" in status:
        return Guidance("ocr-save-failed", "本機保存失敗，請先保留視窗與目前人工修改，確認磁碟空間或資料夾權限。可以繼續檢視，但請勿把結果視為已備份，也別急著重新辨識。",
                        "查看辨識暫存", target="review_table",
                        alternative=Choice("回到辨識暫存", target="review_table"))
    if getattr(page, "_result_source_changed", False):
        return Guidance("ocr-source-changed", "已載入同名檔案的舊結果，但來源內容有變更。可先核對保留的人工修改；若要以新來源重做，請從第一步自動轉正開始，接著按第二步辨識。請勿將舊結果當成新檔已辨識完成。",
                        "先核對已保存結果", target="review_table",
                        alternative=Choice("找到第一步重新轉正", target="auto_rotate_button"))
    table = getattr(page, "review_table", None)
    if table is not None and table.rowCount() == 0:
        return Guidance("ocr-no-labels", "目前沒有可核對的標號。若原圖確有標號，可手動新增；若方向或清晰度不佳，先調整圖片再重做第二步。",
                        "找到手動新增", target="add_label_button",
                        alternative=Choice("找到第二步", target="run_button"))
    reference = getattr(page, "reference_text", None)
    has_reference = reference is not None and not reference.document().isEmpty()
    snapshot = getattr(page, "_guidance_comparison_snapshot", None)
    compared = bool(snapshot and snapshot[0] is results and getattr(page, "reference_items", ())
                    and reference is not None and snapshot[1] == reference.document().revision())
    if compared:
        return Guidance("ocr-compared", "第三步已產生清單比對結果，這不代表人工確認已完成。可繼續核對缺少／多出標號；修改標號或清單後請重新按第三步。圖式結果與人工修改會保存在本機。",
                        "查看清單比對結果", target="result_text",
                        alternative=Choice("已確認，進入段落比對", feature="embodiment_figure_compare"))
    if has_reference:
        return Guidance("ocr-compare-ready", "已備有標號清單，但目前結果尚待第三步更新。先在辨識暫存核對紅框、補漏字及刪除誤判；確認後按第三步。圖式結果與人工修改會保存在本機。",
                        "繼續核對辨識暫存", target="review_table",
                        alternative=Choice("已核對，找到第三步", target="compare_button"))
    return Guidance("ocr-review", "可先核對紅框與其他標號，利用查詢欄逐筆找同一標號；手動修改會在本機保存。若要比對清單，可直接輸入右側清單，或回文件偵錯擷取完整／代表圖符號。",
                    "繼續核對辨識暫存", target="review_table",
                    alternative=Choice("前往文件擷取清單", feature="patent_review"))


def guidance_for(feature, page, context=None, memory=None):
    memory = memory if memory is not None else GuidanceMemory()
    if feature == "home":
        if getattr(context, "document", None) is not None and getattr(context, "ocr_results", ()):
            return Guidance("home-continue", "文件與圖片結果都在目前工作中。可繼續段落圖式比對，也可回文件看未處理提示；請先確認兩邊屬於同一案件。",
                            "繼續段落圖式比對", "embodiment_figure_compare",
                            alternative=Choice("回文件檢核", feature="patent_review"))
        return Guidance("home", "喵，我是墨墨。有 Word 可從文件偵錯開始；只有 PDF／圖片也可先辨識圖式，之後再接回文件流程。拖動我就能換位置。",
                        "從文件開始", "patent_review",
                        alternative=Choice("從圖式開始", feature="patent_ocr"))
    if feature == "patent_review":
        return _review_guidance(page, memory)
    if feature == "patent_ocr":
        return _ocr_guidance(page)
    if feature == "embodiment_figure_compare":
        if getattr(page, "document", None) is None:
            return Guidance("compare-document", "先到文件偵錯載入說明書，這裡才會出現實施方式段落；已有的 OCR 結果可保留繼續使用。", "前往文件偵錯", "patent_review")
        if not getattr(page, "ocr_results", ()):
            return Guidance("compare-images", "說明書已就緒，請先到圖式標號載入圖片、辨識並人工核對。也可先查看此處段落的參閱圖號是否擷取正確。",
                            "前往圖式標號", "patent_ocr", alternative=Choice("先看段落參閱圖號", target="paragraph_table"))
        getter = getattr(page, "_selected_comparison", None)
        comparison = getter() if callable(getter) else None
        if comparison is not None and not comparison.referenced_figures:
            return Guidance("compare-reference", "這段尚未指定參閱圖式。若原文有寫但未擷取，可雙擊左側「參閱圖式」欄補入圖號；若原文需要修訂，請回 Word 修改並重新檢核。",
                            "查看參閱圖式欄", target="paragraph_table",
                            alternative=Choice("回文件檢核", feature="patent_review"))
        if comparison is not None and comparison.missing_figures:
            return Guidance("compare-missing-figure", "本段有參閱圖式尚未找到。先核對實際圖號與頁面對應（同頁可能有多圖）；可到圖式標號補圖片／修對應，或修正左側誤抓的參閱圖號。頁碼不是圖號。",
                            "檢查圖片與頁面圖號", "patent_ocr",
                            alternative=Choice("檢查段落參閱圖號", target="paragraph_table"))
        if comparison is not None and comparison.labels_not_in_drawings:
            return Guidance("compare-missing-label", "「圖式缺失標號」代表 OCR 結果未找到，不一定是原圖真的缺字。若原圖有字，回圖式標號補辨識；若圖號／文件寫錯，核對參閱圖號並回 Word 修訂。右側可縮放看清楚。",
                            "回圖式補查標號", "patent_ocr",
                            alternative=Choice("先核對段落與圖號", target="paragraph_table"))
        return Guidance("compare-ready", "逐段查看參閱圖號與右側圖名、原圖。「缺失標號：無」只表示目前比對未列出缺失，並非全文都已正確；可繼續下一段，或回 OCR 複核圖片。",
                        "繼續查看段落", target="paragraph_table",
                        alternative=Choice("回圖式複核", feature="patent_ocr"))
    if feature == "taiwan_china_spec":
        if getattr(page, "_conversion_running", False):
            return Guidance("convert-busy", "正在產生轉換文件。完成後仍需用 Word 核對內容、表格、公式與版面；請保留原始文件。")
        if getattr(page, "_preview_thread", None) is not None:
            return Guidance("convert-preview", "正在擷取來源並建立預覽。完成後可逐組檢查「一個」，或先核對章節與用語變更。")
        if not getattr(page, "_preview_source", ""):
            return Guidance("convert-empty", "載入要轉換的台灣 Word 文件，選好範本與輸出位置。DOCX 可直接讀取；舊 DOC 需本機 Word 協助匯入。",
                            "找到來源按鈕", target="source_button",
                            alternative=Choice("先做文件檢核", feature="patent_review"))
        if getattr(page, "_guidance_conversion_snapshot", None) == conversion_scope(page):
            return Guidance("convert-exported", "這一版已產生轉換檔。下一步請到輸出位置用 Word 核對文字、表格、公式與版面，也請查看本次轉換訊息。若還要修改，可回預覽修訂後再次轉換；正式送件格式仍需另外確認。",
                            "查看輸出位置", target="output_line",
                            alternative=Choice("回預覽繼續核對", target="preview_output"))
        return Guidance("convert-review", "「一個」清單是待人工確認，不是全部都要刪除：可逐處修訂，也可保留語意正確的用法。接著核對紅字、章節與內容是否保留完整，再轉換；預覽不等於 Word 最終表格／公式版面。",
                        "查看一個檢查", target="article_review",
                        alternative=Choice("已核對，找到轉換按鈕", target="convert_button"))
    return Guidance("other", "喵，我在這裡陪你。點我可看小提示，右鍵可以餵食或陪我玩。按 Esc 可立即回首頁。")
