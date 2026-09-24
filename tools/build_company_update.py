"""Build the small, runtime-preserving company update package."""

from __future__ import annotations

from argparse import ArgumentParser
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from zipfile import ZIP_DEFLATED, ZipFile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from features.patent_ocr.figure_heading_classes import FIGURE_HEADING_CLASS_NAMES

RELEASE_ROOT = PROJECT_ROOT / "release"
UPDATE_DESCRIPTION = "內部測試版本"
CLOUD_DICTIONARY_SEED_FILENAME = "taiwan_china_terminology.txt"
MODEL_FILES = (
    "class_map.json",
    "patent_char_v4_company_approved_recall.onnx",
    "patent_char_v4_company_approved_recall.names.json",
    "patent_char_v3_consensus.onnx",
    "patent_char_v3_consensus.names.json",
    "patent_label_group_v2_gold_ft.onnx",
    "patent_label_group_v1.onnx",
)
FIGURE_HEADING_RELEASE_FILES = (
    "figure_heading_locator_v1.onnx",
    "figure_heading_locator_v1.names.json",
    "figure_heading_locator_v1.release.json",
)
EASYOCR_MODEL_FILES = ("english_g2.pth",)
FIGURE_HEADING_TRIAL_FILES = (
    "figure_heading_pilot_v1.onnx",
    "figure_heading_pilot_v1.names.json",
    "figure_heading_pilot_v1.experimental.json",
)
FIGURE_HEADING_TRIAL_NOTES = """圖題方向模型增補版（2026-09-21）
- 本包已附入圖題方向試用模型及離線必要檔案，可按「第一步自動旋轉」，確認方向後再進行第二步辨識。
- 原始圖片不會被覆寫；證據不足的頁面保留原方向，可手動調整。既有標號 OCR 模型不變。
- 此模型仍屬試用，並非已達全部正式發布門檻。現有測試未涵蓋英文字母／prime 圖號，無圖題負樣本亦有限，請核對轉向結果。
- 本段取代下方歷史摘要中「未附圖題模型／不啟用自動轉向」的說明。

"""


def figure_heading_package_files(include_trial=False):
    files = approved_figure_heading_release_files()
    if not include_trial:
        return files
    from features.patent_ocr.ocr_worker import is_verified_experimental_figure_heading_model

    model_path = PROJECT_ROOT / "models" / FIGURE_HEADING_TRIAL_FILES[0]
    if not is_verified_experimental_figure_heading_model(model_path):
        raise RuntimeError("圖題方向試用模型不完整或 SHA-256 不符，不會建立更新包。")
    return files + FIGURE_HEADING_TRIAL_FILES


REQUIRED_PACKAGE_FILES = (
    "Install_Update.bat",
    "Offline_Check.bat",
    "Saint-Island_Patent_MDS.exe",
    "Startup_Diagnostic.bat",
    CLOUD_DICTIONARY_SEED_FILENAME,
    "更新說明.txt",
    "app/main.py",
    "app/app/config.py",
    "app/app/background_tasks.py",
    "app/app/main_window.py",
    "app/app/paths.py",
    "app/app/resources/app_icon.ico",
    "app/app/resources/app_icon.png",
    "app/app/resources/taiwan_china_spec/BeijingTaijiTemplate.docx",
    "app/app/resources/taiwan_china_spec/ShanghaiYiPin.docx",
    "app/app/resources/taiwan_china_spec/terminology.tsv",
    "app/app/styles.py",
    "app/app/startup_splash.py",
    "app/app/workflow_context.py",
    "app/app/features/arcade_cosmetics.py",
    "app/app/features/bulls_and_cows/__init__.py",
    "app/app/features/bulls_and_cows/engine.py",
    "app/app/features/bulls_and_cows/network.py",
    "app/app/features/chat_room/__init__.py",
    "app/app/features/chat_room/arcade_lan.py",
    "app/app/features/chat_room/game_ranking.py",
    "app/app/features/chat_room/game_report_avatar.jpg",
    "app/app/features/chat_room/store.py",
    "app/app/features/pong/__init__.py",
    "app/app/features/pong/network.py",
    "app/app/features/snake/__init__.py",
    "app/app/features/snake/score_store.py",
    "app/app/features/snake/network.py",
    "app/app/features/tetris/__init__.py",
    "app/app/features/tetris/network.py",
    "app/app/features/tetris/themes.py",
    "app/app/features/tank_battle/__init__.py",
    "app/app/features/tank_battle/network.py",
    "app/features/registry.py",
    "app/features/taiwan_china_spec/__init__.py",
    "app/features/taiwan_china_spec/converter.py",
    "app/features/taiwan_china_spec/terminology_store.py",
    "app/features/taiwan_china_spec/article_review.py",
    "app/features/patent_ocr/figure_heading.py",
    "app/features/patent_ocr/figure_orientation_batch.py",
    "app/features/patent_ocr/result_store.py",
    "app/ui/figure_result_persistence.py",
    "app/features/patent_ocr/figure_heading_classes.py",
    "app/features/patent_ocr/figure_identifiers.py",
    "app/features/patent_ocr/image_tools.py",
    "app/features/patent_ocr/reference_reconciliation.py",
    "app/features/patent_review/docx_reader.py",
    "app/features/patent_review/custom_rules.py",
    "app/features/patent_review/figure_ocr_checker.py",
    "app/features/patent_review/models.py",
    "app/features/patent_review/rule_engine.py",
    "app/features/patent_review/claim_coverage.py",
    "app/features/patent_review/syntax_lab.py",
    "app/ui/syntax_lab_dialog.py",
    "app/features/patent_review/section_parser.py",
    "app/features/patent_review/symbol_transfer.py",
    "app/ui/custom_text_rule_dialog.py",
    "app/ui/arcade_navigation.py",
    "app/ui/arcade_particles.py",
    "app/ui/arcade_skin_art.py",
    "app/ui/arcade_skin_picker.py",
    "app/ui/chat_room_page.py",
    "app/ui/bulls_and_cows_page.py",
    "app/ui/demo_tool_page.py",
    "app/ui/embodiment_figure_compare_page.py",
    "app/ui/feature_navigation.py",
    "app/ui/file_drop.py",
    "app/ui/patent_review_page.py",
    "app/ui/pong_game_page.py",
    "app/ui/recognition_page.py",
    "app/ui/snake_game_page.py",
    "app/ui/tetris_game_page.py",
    "app/ui/tank_battle_page.py",
    "app/ui/taiwan_china_spec_page.py",
    "app/ui/spec_article_review.py",
    "app/ui/workflow_pet.py",
    "app/ui/pixel_cat.py",
    "app/features/workflow_pet/__init__.py",
    "app/features/workflow_pet/guidance.py",
    "app/models/patent_char_v4_company_approved_recall.onnx",
    "app/models/class_map.json",
    "app/models/patent_char_v4_company_approved_recall.names.json",
    "app/models/patent_char_v3_consensus.onnx",
    "app/models/patent_char_v3_consensus.names.json",
    "app/models/patent_label_group_v2_gold_ft.onnx",
    "app/models/patent_label_group_v1.onnx",
    "app/easyocr_models/english_g2.pth",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def approved_figure_heading_release_files():
    model_dir = PROJECT_ROOT / "models"
    model_path = model_dir / FIGURE_HEADING_RELEASE_FILES[0]
    names_path = model_dir / FIGURE_HEADING_RELEASE_FILES[1]
    manifest_path = model_dir / FIGURE_HEADING_RELEASE_FILES[2]
    if not any(path.exists() for path in (model_path, names_path, manifest_path)):
        return ()
    if not all(path.is_file() for path in (model_path, names_path, manifest_path)):
        raise RuntimeError("圖題模型發布檔不完整，不會建立更新包。")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        manifest.get("approved_for_production") is not True
        or manifest.get("classes") != list(FIGURE_HEADING_CLASS_NAMES)
    ):
        raise RuntimeError("圖題模型尚未正式核准，不會建立更新包。")
    for path in (model_path, names_path):
        expected = str(
            manifest.get("artifacts", {}).get(path.name, {}).get("sha256") or ""
        ).lower()
        if not expected or sha256(path) != expected:
            raise RuntimeError(f"圖題模型發布檔 SHA-256 不符：{path}")
    names_payload = json.loads(names_path.read_text(encoding="utf-8"))
    if names_payload.get("names") != list(FIGURE_HEADING_CLASS_NAMES):
        raise RuntimeError("圖題模型類別順序不正確，不會建立更新包。")
    return FIGURE_HEADING_RELEASE_FILES


def read_version() -> str:
    namespace = {}
    config_path = PROJECT_ROOT / "app" / "config.py"
    exec(compile(config_path.read_text(encoding="utf-8"), str(config_path), "exec"), namespace)
    return str(namespace["APP_VERSION"])


def write_cloud_dictionary_seed(destination: Path) -> None:
    source = (
        PROJECT_ROOT
        / "app"
        / "resources"
        / "taiwan_china_spec"
        / "terminology.tsv"
    )
    rows = [
        line
        for line in source.read_text(encoding="utf-8-sig").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if len(rows) != 198:
        raise RuntimeError(
            f"The approved cloud dictionary must contain 198 rows, got {len(rows)}."
        )
    destination.write_text("\n".join(rows) + "\n", encoding="utf-8")


def copy_source_tree(source: Path, destination: Path) -> None:
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns(
            "__pycache__",
            "*.pyc",
            "*.pyo",
            "snake_scores.json",
            "snake_scores.json.tmp",
            "chat_profile.json",
            "chat_profile.json.tmp",
            "chat_avatar_*",
            # Never package a developer copy over company-maintained rules.
            "custom_text_rules.json",
            "custom_text_rules.json.tmp",
        ),
    )


def compile_launcher(destination: Path, source_path: Path | None = None) -> None:
    csc = (
        Path(os.environ.get("WINDIR", r"C:\Windows"))
        / "Microsoft.NET"
        / "Framework64"
        / "v4.0.30319"
        / "csc.exe"
    )
    if not csc.is_file():
        raise FileNotFoundError(f".NET Framework C# compiler is missing: {csc}")
    icon = PROJECT_ROOT / "app" / "resources" / "app_icon.ico"
    if not icon.is_file():
        raise FileNotFoundError(f"Application icon is missing: {icon}")
    subprocess.run(
        [
            str(csc),
            "/nologo",
            "/target:winexe",
            "/platform:x64",
            "/optimize+",
            f"/win32icon:{icon}",
            "/reference:System.Windows.Forms.dll",
            f"/out:{destination}",
            str(
                source_path
                or PROJECT_ROOT
                / "packaging"
                / "Saint-IslandPatentOCR.Launcher.cs"
            ),
        ],
        cwd=PROJECT_ROOT,
        check=True,
    )


def release_notes_with_easter_eggs(version: str) -> str:
    return f"""Saint-Island_Patent_MDS v{version} 內部測試版本
================================================

本更新包保留隱藏聊天室、貪食蛇、雙人彈球與俄羅斯方塊彩蛋，並同步整合正式功能更新。

本次更新
本次新增（2026-09-21，優先於下方歷史功能摘要）
- 內部專用：排行榜改以電腦代號累計與顯示；多人1A2B紀錄改左右分欄，並納入已完成的五款遊戲粒子／光效更新。
- 墨墨會依實際進度提供兩種下一步選擇；同一無標號元件反覆出現相似詞警告時，可自行決定加入本文件白名單或先看其他結果。新增後提醒重新檢核，不會自行排除錯誤。
- 圖式操作改為第一步自動旋轉、第二步圖片標號辨識、第三步清單比對。公司包不含尚未正式核准的圖題方向模型，第一步遇缺模型提示時，請手動轉正並直接使用第二步；正式標號OCR模型維持不變。
- 圖式辨識及手動增刪、修訂、圖號映射、方向與清單會保存在本機；再次匯入同名來源可還原。同名檔內容不同仍會載入舊結果並提醒，請核對後再重新處理。保存失敗時請保留視窗。
- 段落圖式比對會由圖式簡單說明提取圖名，支援多圖、範圍及英文字尾，無法可靠提取時不猜測。人工參閱圖號設定與缺失元件名稱顯示維持可用。
- 台陸轉換已撤回 D01–D50 規則改版，還原原先的繁體預覽、簡體 Word 輸出與說明書首行三字縮排；第 2 項起的權利要求依原附屬項規則插於原檔內文最後一段與有益效果之間。
- 本次保持各版本共用規則路徑與本機個人資料；不覆蓋custom_text_rules.json，不更新runtime，不需要連接外網。
- 台陸用語辭典讀寫位置改為主程式EXE旁的taiwan_china_terminology.txt，舊網路路徑覆寫設定不再生效；本機快取使用v4。請保留自行維護的辭典，更新包另附198筆公司辭典供核對。

歷史功能摘要（台陸格式轉換以本次上述規則選擇為準）
- 墨墨右鍵手動動作精簡為吃飯、喝水、玩毛線球；其餘動作由閒置十秒自動觸發，新增伸懶腰、打哈欠、踩奶、撲葉子及追尾巴。
- 墨墨新增向左走路／跑步；按住左鍵快速左右晃動會出現漩渦眼睛並吐出毛線球，慢速搬動不會誤觸，動畫與拖曳均限制在主視窗內。
- 段落圖式比對的「參閱圖式」可逐段雙擊修改，修改後立即重算缺失標號；保留人工設定並以藍色標示。
- 圖號範圍支援圖8-圖12、圖8A-圖8E、圖8A到圖8E與小寫字尾；描述元件「顯示如圖…的圖像」時，不再取代該段及後續段落原本參閱的圖式。
- 圖式標號頁整理本頁圖號、翻頁及旋轉按鈕。新增圖題辨識的相容支援，但本包不啟用尚未完成標註、訓練與正式核准的新圖題模型，自動轉向亦不會因此啟用。
- 坦克、彈球、1A2B及貪食蛇各新增九款排行榜專屬造型，名次依序解鎖，區網對戰同步顯示，並加入對應的命中、得分或回合結果動畫。
- 更新封裝完整性檢查，確保新增的造型與動畫共用模組一併安裝；不變更正式OCR模型、公司規則與使用者資料。
- 新增像素黑貓助手「墨墨」：可在程式視窗內拖曳，依目前頁面與操作進度提供離線提示；平常每兩秒甩尾並眨眼，滑鼠移入會抬頭揮爪回應，閒置每十秒隨機活動。右鍵可暫時收起，右上角圖示可叫回，並記住各使用者的顯示與聲音設定。
- 墨墨走跑使用左右方向的四腳側面步態，打滾時露出肚子，多數動作穿插甩尾與前爪搔臉；保留紅色怒氣符號的生氣動作，以及收尾趴睡、冒出 Zzz 的睡覺動作。
- 墨墨每次啟動會依電腦當下的日期與時間主動打招呼，自動切換早安、午安、晚安；切換頁面不重複招呼，並尊重已收起墨墨的設定。
- 墨墨每兩秒僅搖尾、眨眼；特殊動作期間暫停兩秒小動作，完成後恢復。滑鼠移開貓咪或提示框時，不再立即收起提示文字。
- 台陸轉換的「發明／實用新型內容」改為獨立人工檢查分類，不再混入一般說明書段落，方便單獨核對來源內容。
- 轉換時保留原始「發明／實用新型內容」的揭露文字與有益效果，只移除已用於固定開頭的來源句；第2項起的權利要求依既定附屬項規則轉寫，插入原檔內文與有益效果之間，不覆蓋原有技術內容。
- 移除人工檢查區的「以權利要求更新內容」按鈕，避免誤覆蓋原說明書內容；人工選取、刪除與復原功能維持不變。
- 圖式標號頁的旋轉按鈕統一使用清楚的 90° 標示，並維持預覽操作列的穩定排列。
- 台陸轉換的公司固定版型會移除權利要求1重複的標的與「並包含」引言，第三段直接由可安全辨識的「所述……」元件細節開始；附屬項仍保留原本的標的開頭，無法安全定位時保留原文並提示人工確認。
- 俄羅斯方塊區網房間改由房主按 START 後進行 3、2、1 倒數，並檢查雙方通訊版本；不同版本會提示更新，不會直接進入不同步的比賽。
- 俄羅斯方塊對戰畫面重新整理 HOLD、NEXT、待接收障礙與 KO 資訊，加入消行、炸彈、Hold、攻擊及 KO 動畫；收到的障礙會等待目前方塊落定後再加入，避免直接推撞操作中的方塊。
- 台陸轉換的公司固定內容格式會移除第二個來源機制句開頭多餘的「於是，」，保留其完整技術內容。
- 文件偵錯新增 REF007：實施方式的單一段落最多參閱四張不同圖式；圖號範圍會展開並去重，五張起列為錯誤。
- 圖式參閱解析補強「參閱圖1到圖3，及圖6」等逗號加連接詞寫法，避免漏算或錯算段落所引用的圖式。
- 文件檢核結果的「錯誤種類」欄會自動分行並依內容調整列高；滑鼠停留仍可查看完整原訊息，不改變任何規則結果。
- 每份文件專用的無標號元件白名單改存於各 Windows 使用者的 %LOCALAPPDATA%\\Saint-Island_Patent_MDS，避免多人共用安裝目錄時互相覆蓋；公司共用黑白名單路徑維持不變。
- 台陸轉換在同時辨識到公司標準「因此…目的…」與「於是…」句時，會依固定順序保留目的句、完整機制句、全部權利要求敘述與原有益效果；舊格式文件仍採原有無損回退流程。
- 「於是」句中的構件清單只會在第一構件一致且插入位置明確時補入權利要求1；條件不明確時保留原文並提示，不會猜測或硬改。標的「無線滑鼠組」維持原名，構件語境的「滑鼠」則依公司用語轉為「鼠標」。
- 台陸辭典新增第 198 筆「兩個合一個 → 二合一」，快取版本同步更新，更新包根目錄亦附上完整 198 筆公司共用辭典種子。
- 圖式標號頁的「全部右旋90度」改在背景執行；處理中會防止重複匯入或辨識，按 Esc 可安全取消，只有全部完成後才替換目前圖片與清除舊辨識結果。
- 圖片旋轉改用可安全在背景執行的 QImage；拖曳調整高解析預覽視窗時，會合併短時間內的重複重繪，降低介面卡頓。
- 台陸共用辭典與本機快取內容相同時不再重複寫檔，降低網路磁碟、同步資料夾及防毒掃描造成的等待。
- 修正各區網遊戲房間清單在頁面關閉或切換後仍嘗試發送 Qt 訊號的競態；即使共享資料夾讀取較慢，背景執行緒也會在頁面銷毀後安靜結束，不再留下背景例外。
- 彩蛋頁面改用共用頂部導覽列與一致的狀態提示樣式；聊天室右側將在線人員與遊戲排行榜整合為分頁，並直接顯示在線人數。
- 未設定聊天室暱稱時，遊戲頁會停用建立房間、加入房間及電腦對戰，避免產生無法辨識玩家的房間；快速連續發送訊息時也會維持穩定的時間順序。
- 台陸轉換後的說明書正文統一採 Word 首行縮排三個中文字寬，技術領域、背景技術、發明／實用新型內容、附圖說明與具體實施方式均適用，標題、摘要及權利要求不受影響。
- 台陸轉換側邊「一個檢查」新增可折疊的「附圖說明」分類，並可在各說明書子章節間維持正確分組。
- 文件偵錯補強 CLM012：元件先以複數型態揭露後，「對應的該 A／相對應之該 A」仍視為單數指稱並列為錯誤，問題詞句會完整標示。
- 數量詞解析不再把「其中兩者」錯套到後方元件；「一繞該軸線的第一周向」會正確將第一周向界定為單數，後續使用「該第一周向」不再誤報。
- 文件偵錯新增 CLM009：同一個請求項必須且只能出現一個全形句號「。」，且須位於句尾；跨行或跨 Word 段落仍合併計算，小數點與表格內容不會誤計。
- 聊天室線上名單會將同一使用者、同一電腦短暫殘留的重複連線合併，只保留最新狀態，避免連續傳訊後出現自己的多個分身。
- 俄羅斯方塊 HOLD 區會直接顯示保存的方塊；障礙列改為九個灰格加一個紅色炸彈且不留缺口，完整障礙列不會被一般滿行消除，壓中炸彈時只清除其所在橫列。
- 連續消行或炸彈消障礙列會形成 Combo；第二次起顯示約一秒的 COMBO 動畫，並依 Combo 數送出等量障礙列，未消除的落塊會中斷連段。
- 俄羅斯方塊新增九套造型獎勵；排行榜第 3、2、1 名分別解鎖前三、前六、全部九套，建立或加入區網房間前可預覽選擇，雙方造型會隨對戰狀態同步。
- 支援同一權利要求分多行修訂，保留其項次及技術內容；段落標題或項次有缺漏時不覆蓋原內容，也不直接更動原始 Word。
- 底部轉換按鈕改為較大的白色箭頭與文字。

文件檢核與效能更新（v{version}）
本次內測專用：文件偵錯直接提供「語法對照（內測）」入口，不需特殊啟動檔。依100件研究整理的對照模式，分別定位獨立標的與內容、各項新增敘述與實施方式；保留段落原文、條件／數量／態樣核對提示。僅列候選，不代表已涵蓋，不自動改稿。此功能不納入公司標準乾淨版。
1. ABS001 中文摘要字數提醒：合併中文摘要各段，排除名稱、英文摘要及代表圖符號；估計超過 250 字時警告，僅計數口徑可能超限時列為資訊提醒。
2. CLM018 重複請求項提醒：比較不同請求項完整內文，容許換行與等價全半形排版；依附項次、數值、數量、否定、大小寫及 prime 差異仍保留。
3. CLM019 請求項1與發明／新型內容對應提醒：離線比對技術敘述及有限改寫，支援換序、拆句、跨段、明確主被動及指定前置修飾展開。
4. 可拆卸方式、方向、數量、元件歸屬等限制不會因改寫被略過；不同實施例不拼湊，同組相反敘述也需人工核對。未支援的語意不直接當成已涵蓋。
5. 點選對應提醒時顯示完整請求項1，紅色標出待核對文字，藍色標出原始內容候選；多處不確定片段合併為一筆資訊提醒。候選文字不等同確認涵蓋，程式不判斷法律支持性、不修改原始文件。
6. 文件載入與檢核、PDF 轉圖、台陸轉換及規則載入改採背景工作；取消或關閉後不再套用過期結果，降低主視窗等待及卡住風險。
7. 保留延遲載入，優化文字比對與資源快取；改善聊天室共享檔案讀取及關閉等待，未改動 OCR 模型及既有檢核標準。
8. 安裝前檢查新增模組與模型檔案，保留既有 runtime 與公司自訂規則 custom_text_rules.json；啟動器同步讀取測試輸出，避免大量診斷訊息造成等待。

保留前版台陸轉換「一個」檢查
- 右側訊息欄縮小，下方保留訊息，上方新增預覽全文的「一個」逐處清單。
- 點選清單可跳到原句，以螢光底色標示句子與對應詞語；標示不會寫入輸出 Word。
- 支援 Shift／Ctrl 複選，再按「刪除所選項目」只刪除指定位置的「一個」兩字，保留句子其餘內容；刪除後自動選取下一處。
- Ctrl+Z 可復原，Ctrl+Y 可重做批次刪除及文字編輯；保留人工修訂後的 Word 輸出。
- 優先置頂：每一個、各一個、上一個、下一個、其中一個、另一個、其中另一個、至少一個、第一個，以及「一個」後 10 字內接「空間」的項目。
- 繼續編輯文章時同步更新清單定位與選取範圍；保留台陸頁的獨立拖曳規則，並調整小視窗排版。

既有功能與累積更新
1. 新增淺藍色啟動等待動畫與分階段載入提示，主視窗完成後自動關閉。
2. 功能頁改採延遲匯入，降低程式啟動初期的空白等待時間。
3. 文件偵錯頁新增「實施方式段落出現之無標號元件」，可排除指定詞語的模糊比對及標號偵測，並保留重新檢核功能。
4. 內部測試版本的共用自訂文字規則維持由 C:\\Documents 路徑讀寫，文件專用白名單維持獨立保存。
5. 請求項相同元件與相同內容的單複數錯誤，在同一請求項只顯示一次。
6. 新增「各自的該」、「二個／多個該元件」等合法複數成員指稱。
7. 新增「以下／下列／幾個／數個／多個步驟」的列舉型複數揭露；「步驟」不再嚴格檢查冠詞單複數，但仍檢查實施方式中的元件標號。
8. 實施方式非末段可用全形或半形冒號承接下一小段，不再誤報缺少句號。
9. 段落圖式比對支援「例如為圖5」累加依附、同段單列顯示，並從「綜上所述」起停止後續比對。
10. 圖式標號頁新增縮小、適合視窗及放大按鈕；段落比對頁移除重複提示文字。
11. 保留首頁 P 字連按五次開啟彩蛋；彩蛋首頁為即時聊天室，並可切換貪食蛇及雙人彈球。
12. 聊天室新增工作列閃爍提醒與開關、未讀分隔線、每則訊息已讀人數及正式功能頁未讀數字徽章；不同電腦即使曾複製到相同 user_id，也會依電腦編號正確區分未讀訊息。
13. 彩蛋頁新增「返回當前工作進度」，可回到原本的文件偵錯、圖式標號或段落圖式比對頁面。
14. 雙人彈球改為自動列出公司共享路徑中的有效房間，點一下即可加入，不再需要輸入房間代碼；滿兩人時由清單及主機端雙重禁止第三人加入，並強制採用公司區網直連以避開無效的系統代理設定。
15. 雙人彈球玩家名稱固定使用聊天室暱稱，完成比賽後由房主在聊天室發布一次勝負戰報。
16. 雙人彈球每 5 秒在場上中線隨機生成加速、延遲 0.5 秒交換位置或限時五倍巨大球道具，全部效果由房主同步。
17. 貪食蛇新成績實際進入排行榜前十名時，會在聊天室發布玩家名稱與新名次戰報。
18. 所有非首頁頁面皆可按 Esc 立即中止目前流程並返回首頁；OCR 辨識中止後會自動恢復控制項。
19. 沿用既有離線 runtime 與正式 OCR 模型，不需外網、Python 或 Conda。
20. 圖式標號預設改用黃金資料微調的完整標號定位器 v2；封存集完整標號定位率為 496/502（98.80%）。
21. 文件已有符號清單時，OCR 會保守修正唯一且同長度的已知字形混淆（例如 Ll→L1、WI→W1），並保留模型原始值及使用者手動修改。
22. 上述校正在封存黃金集由 466/502 提升至 473/502，逐筆稽核為 7 筆改善、0 筆誤改；不會自動增加或刪除字元。
23. 實施方式的小段落即使先以「步驟E」或「在本實施例中」開頭，仍可正確擷取段落中途出現的參閱圖式，不再顯示多餘的沿用圖式空結果。
24. 聊天室支援將 Windows 螢幕截圖以 Ctrl+V 直接貼入輸入欄，並可按 Enter 將截圖與文字一併傳送。
25. 貪食蛇排行榜改由 \\\\CPC2856\\Documents\\app\\features\\snake 共用，供公司電腦共享前十名紀錄。
26. 新增雙人區網俄羅斯方塊：房間清單點選加入、滿兩人禁入、即時盤面同步、消行攻擊、計分、勝負與聊天室戰報。
27. 雙人彈球房主可設定 1 至 30 分的獲勝分數，房間清單同步顯示；單機電腦對手改為較慢反應並加入預測誤差。
28. 聊天室訊息可按右鍵選擇回覆、複製或 emoji；回覆會保留原訊息發送者頭貼與縮小摘要，emoji 反應會永久保存於該訊息下方。
29. 聊天圖片不再固定放大；小圖維持原始尺寸，只有超出聊天室顯示範圍的圖片才會等比例縮小。
30. 建立雙人彈球、俄羅斯方塊或坦克大戰區網房間時，聊天室會自動發布可點擊的對戰邀請；已滿或已開始的房間會拒絕加入。
31. 俄羅斯方塊新增電腦對戰與落點投影，方便預判方塊最終位置。
32. 雙人彈球新增「虛」道具，球會限時改為虛線外框；場上現在最多可同時存在兩個道具。
33. 彩蛋功能頁統一頂部按鈕順序、邊距與視覺規格，聊天室、彈球、俄羅斯方塊、坦克大戰與貪食蛇可一致切換。
34. 新增坦克大戰：原創經典俯視角磚牆／鋼牆／水域地圖、三條命死鬥、1 至 3 名電腦對手，以及公司區網 2 至 4 人房間。
35. 坦克大戰 2 至 3 人固定各自為戰；四人房可選擇各自為戰或 2 對 2，主機端會阻止超額玩家加入。
36. 主視窗會依螢幕可用範圍決定啟動大小；切換頁面或更新內容時保留既有位置、尺寸及最大化狀態，不再因子頁尺寸提示而遮住控制項。
37. 所有正式功能頁恢復跨頁檔案拖放：DOCX 自動切換至文件偵錯，PDF 自動切換至圖式標號，不支援的格式會明確提示。
38. 全部按鈕字體增加 2pt，頂部功能列同步加高並維持各頁固定位置。
39. 圖式標號框改為細線，原圖預覽以螢幕實際像素密度重新取樣，在 Windows 高 DPI 縮放下仍保留細節。
40. 大章節間的下一頁分節符號改為選用，未插入分節符號不再列為錯誤。
41. 提高數字 0 的辨識門檻並過濾相對過小的獨立圓圈；完整標號已包含子框時，會保留完整標號並捨棄重複子標號。
42. 彈球、俄羅斯方塊及坦克大戰的電腦對戰皆提供簡單／普通／困難選項，遊戲頁面尺寸、邊距及提示排版已統一簡化。
43. 雙人彈球新增「旋」道具：球會繞著持續前進的軌跡中心旋轉，效果與區網狀態同步。
44. 坦克大戰新增十字要塞、河灣伏擊、雙堡對峙、鋼鐵迷宮及環島決戰五張原創地圖，房間清單及主機同步所選地圖。
45. 隱藏功能新增經典 1A2B 猜數字，可單人挑戰或雙人同機輪流遊玩；答案與猜測均為四位不重複數字，4A 即獲勝。
46. 俄羅斯方塊對戰升級為兩分鐘 Tetris Battle 風格：七袋出塊、Hold、五塊預覽、幽靈落點、Combo、Back-to-Back、T-Spin、Perfect Clear、炸彈垃圾行及最多五次 KO；時間到依 KO、攻擊行數、盤面高度依序判定。
47. 1A2B 與貪食蛇新增公司區網真人雙人房間，均可由共享清單直接加入、滿房禁入並發布聊天室邀請；1A2B 私密保存雙方答案，貪食蛇採同場競技盤面。
48. 聊天室新增永久綜合遊戲積分榜：俄羅斯方塊真人勝場 +3、彈球與坦克 +2、其餘真人對戰 +1；AI、單人及平手不計分，同一場結果不會重複加分。
49. 綜合積分採固定使用者 ID 保存；聊天室暱稱或頭像更新後，排行榜會顯示最新資料而不重置既有積分。右側前三名以皇冠、金銀銅牌及積分醒目呈現。
50. 聊天室訊息區縮窄，線上人員區向左移並加寬，最右側新增永久前三名排行榜。
51. 所有遊戲戰報改用指定人物圖片作為固定頭像；玩家戰勝 AI 時會顯示簡單／普通／困難及對應的「恭喜／厲害厲害／太神啦!!!」。
52. 圖式標號頁移除模型選擇列並向上放大原圖與辨識暫存區；選取整列後可按 Delete 刪除標號，雙擊編輯時 Delete 只刪除反白字元，新增／刪除按鈕不再遮住清單。
53. 專利文件偵錯頁移除「已擷取完整符號…」狀態列，主要檢核視窗向上延伸。
54. 新增台灣－大陸專利說明書轉換頁，可使用北京／上海範本，並在雲端預設、快取與內建辭典間安全回退後供使用者直接修訂。
55. 權利要求章節改採嚴格標題辨識；實施方式內提及「申請專利範圍」時不再把符號說明誤轉成第一項權利要求。
56. 台陸轉換頁的用語辭典固定採高對比白底配色，右側訊息欄縮小，並移除右上角使用說明與關於按鈕。
57. 圖式標號頁移除第二階段清單輸入欄，放大辨識結果，並將兩個結果切換按鈕排列於同一列。
58. 台陸辭典納入 198 條公司核定規則及人工更正，完全依核定列序逐條轉換，不排序、不去重。
59. 台陸轉換頁保留「同步」並新增「上傳」；共用辭典使用 \\\\sic11\\Doc\\_CP\\Saint-Island\\_Patent\\_MDS\\taiwan_china_terminology.txt，其他使用者下次開啟時自動讀取。
60. 權利要求段落移除 Word 黑色段落標記，但保留 1.、2.、3. 的自動編號與段落樣式。
61. 符號說明與發明申請專利範圍銜接文字不再誤入權利要求書。
62. 台陸轉換新增繁體、可編輯的大陸案格式預覽，修改處以紅字標示；圖式標號頁同步完成代表圖自動跳轉、上下搜尋箭頭及 3:2 視窗配置，文件偵錯頁的符號說明切換按鈕也已統一外觀。

安裝方式
1. 完全關閉 Saint-Island_Patent_MDS。
2. 解壓縮本更新包到公司應用程式根目錄。
3. 執行 Install_Update.bat。
4. 等待離線推論與 GUI 啟動檢查完成。

注意：這是既有公司版本的更新包，不包含 runtime；請勿只複製 EXE。
同一目錄請只安裝相符版本，勿將兩份更新包混合解壓。
"""


def release_notes(version: str) -> str:
    return release_notes_with_easter_eggs(version)


def build_package(output_root: Path, package_name: str, *, include_figure_heading_trial=False) -> tuple[Path, Path, Path]:
    heading_files = figure_heading_package_files(include_figure_heading_trial)
    package_dir = output_root / package_name
    zip_path = output_root / f"{package_name}.zip"
    checksum_path = output_root / f"{package_name}.zip.sha256.txt"
    if package_dir.exists() or zip_path.exists() or checksum_path.exists():
        raise FileExistsError(
            "Release output already exists; choose a new name or remove the previous generated release."
        )

    app_dir = package_dir / "app"
    app_dir.mkdir(parents=True)
    shutil.copy2(PROJECT_ROOT / "main.py", app_dir / "main.py")
    for folder in ("app", "features", "ui"):
        copy_source_tree(PROJECT_ROOT / folder, app_dir / folder)

    model_dir = app_dir / "models"
    model_dir.mkdir()
    for file_name in MODEL_FILES:
        source = PROJECT_ROOT / "models" / file_name
        if not source.is_file():
            raise FileNotFoundError(f"Required model file is missing: {source}")
        shutil.copy2(source, model_dir / file_name)
    for file_name in heading_files:
        source = PROJECT_ROOT / "models" / file_name
        shutil.copy2(source, model_dir / file_name)

    easyocr_model_dir = app_dir / "easyocr_models"
    easyocr_model_dir.mkdir()
    for file_name in EASYOCR_MODEL_FILES:
        source = PROJECT_ROOT / "easyocr_models" / file_name
        if not source.is_file():
            raise FileNotFoundError(f"Required EasyOCR model is missing: {source}")
        shutil.copy2(source, easyocr_model_dir / file_name)

    shutil.copy2(PROJECT_ROOT / "packaging" / "Install_Update.bat", package_dir)
    shutil.copy2(PROJECT_ROOT / "packaging" / "Startup_Diagnostic.bat", package_dir)
    compile_launcher(package_dir / "Saint-Island_Patent_MDS.exe")
    shutil.copy2(PROJECT_ROOT / "Offline_Check.bat", package_dir)
    write_cloud_dictionary_seed(
        package_dir / CLOUD_DICTIONARY_SEED_FILENAME
    )

    version = read_version()
    (package_dir / "更新說明.txt").write_text(
        (FIGURE_HEADING_TRIAL_NOTES if include_figure_heading_trial else "") + release_notes(version), encoding="utf-8-sig", newline="\r\n"
    )

    for relative_path in REQUIRED_PACKAGE_FILES:
        if not (package_dir / relative_path).is_file():
            raise FileNotFoundError(f"Incomplete update package: {relative_path}")

    files = sorted(path for path in package_dir.rglob("*") if path.is_file())
    manifest = {
        "product": "Saint-Island_Patent_MDS",
        "version": version,
        "package_name": package_name,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "runtime_included": False,
        "figure_heading_trial_included": include_figure_heading_trial,
        "requires_existing_runtime": True,
        "clean_distribution": False,
        "distribution_profile": "internal_test",
        "document_correspondence_preview": True,
        "easter_eggs_included": True,
        "files": [
            {
                "path": path.relative_to(package_dir).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
            for path in files
        ],
    }
    manifest_path = package_dir / "PACKAGE_MANIFEST.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    with ZipFile(zip_path, "w", ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(package_dir.rglob("*")):
            if path.is_file():
                archive.write(
                    path,
                    (Path(package_name) / path.relative_to(package_dir)).as_posix(),
                )

    checksum = sha256(zip_path)
    checksum_path.write_text(f"{checksum}\n", encoding="ascii")
    return package_dir, zip_path, checksum_path


def main() -> int:
    version = read_version()
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=RELEASE_ROOT)
    parser.add_argument("--include-figure-heading-trial", action="store_true")
    parser.add_argument(
        "--name",
        default=(
            f"Saint-Island_Patent_MDS_v{version}_{UPDATE_DESCRIPTION}_"
            f"{date.today():%Y%m%d}"
        ),
    )
    args = parser.parse_args()
    package_dir, zip_path, checksum_path = build_package(
        args.output_root.resolve(), args.name,
        include_figure_heading_trial=args.include_figure_heading_trial,
    )
    print(f"PACKAGE_DIR={package_dir}")
    print(f"ZIP={zip_path}")
    print(f"SHA256={checksum_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
