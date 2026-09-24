import tempfile
import unittest
import zipfile
from collections import Counter
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

from features.taiwan_china_spec import converter


SOURCE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "TW_方向校正專利圖式標號檢核系統_發明說明書.docx"
)


def specification(claims=None, content=None, kind="invention"):
    return converter.ParsedSpecification(
        kind=kind,
        title="感測裝置及操作方法",
        abstract=["一種感測裝置。"],
        technology_field=["本案涉及感測裝置。"],
        background=["現有裝置仍須改善。"],
        invention_content=content if content is not None else ["本發明的有益效果在於：降低耗電。"],
        drawing_description=["圖1為裝置示意圖。"],
        embodiments=["裝置具有感測器與處理器。"],
        claims=claims if claims is not None else [
            "一種感測裝置，包括感測器與處理器。",
            "如請求項1所述之感測裝置，其中處理器具有記憶體。",
            "一種感測方法，包括取得資料及處理資料。",
            "如請求項3所述之感測方法，其中資料經過濾波。",
        ],
    )


def content_texts(parsed):
    warnings = []
    content = converter._build_invention_content(parsed, warnings)
    # Inspect the final structural narrative without character/dictionary changes.
    return [
        converter._adapt_existing_dependent_narrative(item.text) if item.dependent else item.text
        for item in content
    ], warnings


def preview_text(preview):
    return "\n".join(item.prefix + item.converted_text for item in preview.paragraphs)


def docx_content(path):
    with zipfile.ZipFile(path) as package:
        root = ET.fromstring(package.read("word/document.xml"))
    paragraphs = [
        converter._paragraph_text(paragraph)
        for paragraph in root.findall("w:body/w:p", converter.NS)
    ]
    start = next(
        index for index, value in enumerate(paragraphs)
        if converter._normalized(value) in {"发明内容", "实用新型内容"}
    )
    end = next(
        index for index in range(start + 1, len(paragraphs))
        if converter._normalized(paragraphs[index]) == "附图说明"
    )
    return [value for value in paragraphs[start + 1:end] if value]


class AllClaimsContentTests(unittest.TestCase):
    def test_company_source_intro_claim_components_and_benefit_have_fixed_order(self):
        claim = (
            "一種可調回報率的輕量化無線滑鼠組，適用於訊號連接一運算處理單元，"
            "並包含：一滑鼠，包括界定出一滑鼠空間的滑鼠外殼，"
            "及一設置於該滑鼠空間的滑鼠控制單元。"
        )
        parsed = specification(claims=[claim], content=[
            "【0005】因此，本發明的目的，即在提供一種可調回報率的輕量化無線滑鼠組，"
            "以改善耗電。這一句不得帶入。",
            "【0006】於是，本發明的可調回報率的輕量化無線滑鼠組"
            "並包含一滑鼠、一台座、兩電池盒及一電池。這一句也不得帶入。",
            "中間舊技術內容不得保留。",
            "本發明的有益效果在於：降低耗電。",
        ])

        warnings = []
        claims = converter._claims_with_source_components(parsed, warnings)
        self.assertFalse(warnings)
        self.assertIn(
            "並包含一鼠標、一台座、兩電池盒及一電池，該鼠標包括",
            claims[0],
        )
        self.assertNotIn("並包含：一滑鼠，包括", claims[0])
        self.assertIn("無線滑鼠組", claims[0])

        texts, warnings = content_texts(converter.replace(parsed, claims=claims))
        self.assertFalse(warnings)
        self.assertEqual(texts, [
            "本發明的目的在於提供一種可調回報率的輕量化無線滑鼠組，以改善耗電。",
            "本發明的可調回報率的輕量化無線滑鼠組"
            "並包含一鼠標、一台座、兩電池盒及一電池。",
            "所述鼠標包括"
            "界定出一鼠標空間的鼠標外殼，及一設置於該鼠標空間的鼠標控制單元。",
            "本發明的有益效果在於：降低耗電。",
        ])

    def test_utility_model_fixed_intro_uses_full_owner_name(self):
        parsed = specification(
            kind="utility_model",
            claims=["一種折疊架，並包含：一支架，包括一連接部。"],
            content=[
                "因此，本新型之目的，即在提供一種節省空間的折疊架。",
                "於是，本新型的折疊架並包含一支架及一底座。",
                "本新型之功效在於：方便收納。",
            ],
        )
        warnings = []
        claims = converter._claims_with_source_components(parsed, warnings)
        texts, warnings = content_texts(converter.replace(parsed, claims=claims))
        self.assertFalse(warnings)
        self.assertEqual(texts[0], "本實用新型的目的在於提供一種節省空間的折疊架。")
        self.assertEqual(texts[1], "本實用新型的折疊架並包含一支架及一底座。")
        self.assertEqual(texts[2], "所述支架包括一連接部。")
        self.assertEqual(texts[-1], "本實用新型的有益效果在於：方便收納。")

    def test_component_mismatch_keeps_claim_and_reports_warning(self):
        parsed = specification(
            claims=["一種支架，並包含：一框體，包括一連接部。"],
            content=["於是，本發明的支架並包含一滑鼠及一台座。"],
        )
        warnings = []
        claims = converter._claims_with_source_components(parsed, warnings)
        self.assertEqual(claims, parsed.claims)
        self.assertEqual(len(warnings), 1)
        self.assertIn("第一個構件", warnings[0])

    def test_preview_applies_component_list_before_manual_article_review(self):
        claim = (
            "一種可調回報率的輕量化無線滑鼠組，並包含："
            "一滑鼠，包括界定出一滑鼠空間的滑鼠外殼。"
        )
        parsed = specification(claims=[claim], content=[
            "因此，本發明的目的，即在提供一種可調回報率的輕量化無線滑鼠組。",
            "於是，本發明的無線滑鼠組並包含一滑鼠、一台座、兩電池盒及一電池。",
            "所述鼠標包括原檔專有的傳輸模組。",
            "本發明的有益效果在於：降低耗電。",
        ])
        rules = [("該", "所述")]
        with patch.object(converter, "parse_taiwan_specification", return_value=parsed):
            preview = converter.build_conversion_preview(
                SOURCE, terminology_pairs=rules, traditional_characters=True
            )
        claim_text = next(
            item.converted_text for item in preview.paragraphs if item.role == "claim"
        )
        self.assertIn("一鼠標、一臺座、兩電池盒及一電池", claim_text)
        self.assertIn("所述鼠標包括界定出一鼠標空間", claim_text)
        self.assertIn("無線滑鼠組", claim_text)
        content = converter.parse_edited_preview_text(
            preview_text(preview), preview.specification_kind
        ).invention_content
        self.assertTrue(content[0].startswith("本發明的目的在於提供一種"))
        self.assertTrue(content[1].startswith("本發明"))
        self.assertNotIn("於是，", content[1])
        self.assertTrue(content[2].startswith("所述鼠標包括"))
        self.assertEqual(content[2], "所述鼠標包括原檔專有的傳輸模組。")
        self.assertNotIn("本發明的無線滑鼠組", content[2])
        self.assertTrue(content[-1].startswith("本發明的有益效果在於"))
        # Source disclosure is no longer a replaceable generated claims block.
        self.assertFalse(preview.fixed_content_layout)

    def test_claim_feature_connector_never_keeps_a_following_comma(self):
        cases = (
            "如請求項1所述之感測裝置，其中，所述處理器具有記憶體。",
            "如請求項1所述之感測裝置，其特徵在於：，所述處理器具有記憶體。",
            "如請求項1所述之感測裝置，其特徵在於，所述處理器具有記憶體。",
            "如請求項1所述之感測裝置，包括所述處理器。",
        )
        for original in cases:
            with self.subTest(original=original):
                counts = Counter()
                warnings = []
                converted = converter._convert_claim(
                    original, 2, [], counts, True, True, warnings,
                )
                self.assertIn("其特征在于：", converted)
                self.assertIn("所述处理器", converted)
                self.assertNotRegex(converted, r"其特征在于[：:]?[，,]")
                self.assertNotIn("，，", converted)
                self.assertFalse(warnings)

    def test_claim1_content_removes_feature_connector_but_claim_itself_keeps_it(self):
        claim = "一種感測裝置，其特徵在於：，包括感測器。"
        texts, warnings = content_texts(specification(claims=[claim], content=[]))
        self.assertEqual(texts, ["本發明的目的在於提供一種感測裝置，包括感測器。"])
        converted = converter._convert_claim(claim, 1, [], Counter(), True, True, [])
        self.assertIn("其特征在于：包括感测器。", converted)
        self.assertNotIn("其特征在于：，", converted)
        self.assertFalse(warnings)

    def test_dependent_content_removes_legacy_double_comma(self):
        self.assertEqual(
            converter._adapt_existing_dependent_narrative(
                "本實用新型的自動補換棒送料裝置，，每一個所述元件可移動。"
            ),
            "本實用新型的自動補換棒送料裝置，每一個所述元件可移動。",
        )

    def test_all_claims_follow_order_before_existing_benefits(self):
        parsed = specification()
        texts, warnings = content_texts(parsed)
        self.assertFalse(warnings)
        self.assertEqual(len(texts), 5)
        self.assertEqual(texts[0], "本發明的目的在於提供" + parsed.claims[0])
        self.assertEqual(texts[1], "本發明的感測裝置，處理器具有記憶體。")
        self.assertEqual(texts[2], "本發明的感測方法，包括取得資料及處理資料。")
        self.assertEqual(texts[3], "本發明的感測方法，資料經過濾波。")
        self.assertEqual(texts[-1], parsed.invention_content[0])

    def test_existing_purpose_and_non_duplicate_disclosure_are_preserved(self):
        original = [
            "【0001】本發明之目的在於降低誤判。",
            "【0002】本發明的有益效果在於：保留每次測量。",
            "【0003】濾波器還具有原claim未提及的測試端。",
        ]
        texts, _warnings = content_texts(specification(content=original))
        self.assertEqual(texts[0], "本發明之目的在於降低誤判。")
        self.assertEqual(texts[1], "本發明的感測裝置，包括感測器與處理器。")
        self.assertEqual(texts[-2], "濾波器還具有原claim未提及的測試端。")
        self.assertEqual(texts[-1], "本發明的有益效果在於：保留每次測量。")

    def test_complete_claim1_already_in_purpose_is_not_repeated(self):
        parsed = specification()
        full_purpose = "本發明的目的在於提供" + parsed.claims[0]
        parsed.invention_content.insert(0, full_purpose)
        texts, _warnings = content_texts(parsed)
        self.assertEqual(texts[0], full_purpose)
        self.assertEqual("".join(texts).count("包括感測器與處理器。"), 1)

    def test_exact_existing_dependent_paragraph_is_reused_in_claim_order(self):
        parsed = specification()
        dependent = "本發明的感測方法，其中資料經過濾波。"
        parsed.invention_content.insert(0, dependent)
        texts, _warnings = content_texts(parsed)
        self.assertNotIn(dependent, texts)
        self.assertEqual(texts.count("本發明的感測方法，資料經過濾波。"), 1)
        self.assertEqual(texts[3], "本發明的感測方法，資料經過濾波。")

    def test_duplicate_narrative_never_replaces_complete_existing_purpose(self):
        purpose = "本發明的目的在於提供一種感測裝置，包括感測器。"
        narrative = "本發明的感測裝置，包括感測器。"
        for originals in ([purpose, narrative], [narrative, purpose]):
            with self.subTest(originals=originals):
                texts, warnings = content_texts(specification(
                    claims=["一種感測裝置，包括感測器。"], content=originals
                ))
                self.assertEqual(texts, [purpose])
                self.assertFalse(warnings)

    def test_purpose_prefix_comma_is_ignored_only_for_whole_paragraph_equivalence(self):
        purpose = "本發明的目的在於，提供一種感測裝置，包括感測器、控制器，其中控制器具有電池。"
        extra = "本發明的感測裝置，包括感測器，控制器，其中控制器具有電池。"
        texts, _warnings = content_texts(specification(
            claims=["一種感測裝置，包括感測器、控制器，其中控制器具有電池。"],
            content=[purpose, extra],
        ))
        self.assertEqual(texts, [purpose, extra])
        self.assertEqual("".join(texts).count("包括感測器、控制器"), 1)

    def test_unadapted_original_dependent_claim_is_consumed_without_restoring_reference(self):
        original = "如請求項1所述之感測裝置，其中具有電池。"
        extra = "如請求項1所述之感測裝置，其中具有電池及測試端。"
        parsed = specification(
            claims=["一種感測裝置，包括感測器。", original],
            content=[original, extra],
        )
        texts, _warnings = content_texts(parsed)
        self.assertNotIn(original, texts)
        self.assertEqual(texts[1], "本發明的感測裝置，具有電池。")
        self.assertEqual("".join(texts).count("具有電池。"), 1)
        self.assertIn(extra, texts)

    def test_unadapted_original_independent_claim_keeps_generated_narrative(self):
        original = "一種感測裝置，包括感測器。"
        texts, _warnings = content_texts(specification(claims=[original], content=[original]))
        self.assertEqual(texts, ["本發明的目的在於提供" + original])

    def test_technical_whitespace_is_not_erased_when_matching_existing_narrative(self):
        for compact, separated in (("AB", "A B"), ("ab", "a b"), ("12", "1 2")):
            for claim_token, original_token in ((compact, separated), (separated, compact)):
                with self.subTest(claim=claim_token, original=original_token):
                    claim = f"一種感測裝置，包括{claim_token}輸入端。"
                    original = f"本發明的感測裝置，包括{original_token}輸入端。"
                    texts, warnings = content_texts(specification(claims=[claim], content=[original]))
                    self.assertEqual(texts, ["本發明的目的在於提供" + claim, original])
                    self.assertFalse(warnings)

    def test_technical_whitespace_is_not_erased_when_consuming_unadapted_claim(self):
        for compact, separated in (("AB", "A B"), ("ab", "a b"), ("12", "1 2")):
            for claim_token, original_token in ((compact, separated), (separated, compact)):
                with self.subTest(claim=claim_token, original=original_token):
                    claim = f"一種感測裝置，包括{claim_token}輸入端。"
                    original = f"一種感測裝置，包括{original_token}輸入端。"
                    texts, warnings = content_texts(specification(claims=[claim], content=[original]))
                    self.assertEqual(texts, ["本發明的目的在於提供" + claim, original])
                    self.assertFalse(warnings)

    def test_repeated_technical_whitespace_can_match_one_space_but_not_no_space(self):
        for original in (
            "本發明的感測裝置，包括A   B輸入端。",
            "本發明的感測裝置，包括A \t B輸入端。",
            "一種感測裝置，包括A   B輸入端。",
        ):
            with self.subTest(original=original):
                texts, warnings = content_texts(specification(
                    claims=["一種感測裝置，包括A B輸入端。"], content=[original]
                ))
                self.assertEqual(len(texts), 1)
                self.assertIn("包括A B輸入端。", texts[0])
                self.assertFalse(warnings)

    def test_original_utility_purpose_owner_is_normalized_without_modifying_body(self):
        purpose = "本新型之目的在於，提供一種感測裝置，包括感測器，本新型之測試文字原樣保留。"
        parsed = specification(
            kind="utility_model",
            claims=["一種感測裝置，包括感測器，本新型之測試文字原樣保留。"],
            content=[purpose, "本新型之功效在於：節省空間。"],
        )
        paragraphs = converter._build_invention_content(parsed, [])
        self.assertEqual(len(paragraphs), 2)
        self.assertEqual(paragraphs[0].source_text, purpose)
        self.assertEqual(
            paragraphs[0].text,
            "本實用新型的目的在於，提供一種感測裝置，包括感測器，本新型之測試文字原樣保留。",
        )
        self.assertEqual(paragraphs[-1].text, "本實用新型的有益效果在於：節省空間。")

    def test_near_duplicate_with_extra_feature_is_never_dropped(self):
        additional = "本發明的感測裝置，其中處理器具有記憶體及備援電池。"
        texts, _warnings = content_texts(specification(content=[additional]))
        self.assertIn(additional, texts)
        self.assertIn("本發明的感測裝置，處理器具有記憶體。", texts)

    def test_utility_model_uses_its_own_owner_and_original_benefit(self):
        texts, _warnings = content_texts(specification(
            kind="utility_model", content=["本新型之功效在於：節省空間。"]
        ))
        self.assertTrue(texts[0].startswith("本實用新型的目的在於提供"))
        self.assertEqual(texts[1], "本實用新型的感測裝置，處理器具有記憶體。")
        self.assertEqual(texts[-1], "本實用新型的有益效果在於：節省空間。")

    def test_supported_dependency_variants_use_the_claims_own_subject(self):
        prefixes = [
            "如請求項1所述之",
            "根據權利要求1所述的",
            "根据权利要求1所述的",
            "如請求項1、2或3所述之",
            "如請求項1至3所述之",
            "如請求項1～3中任一項所述之",
            "依據權利要求第1項至第3項中任一項所述的",
            "如申請專利範圍第1項所述之",
            "依照請求項 1 或請求項 2 所述之",
            "根據權利要求1-3任一項所述的",
            "根據權利要求１至３其中任意一項所述的",
        ]
        for prefix in prefixes:
            with self.subTest(prefix=prefix):
                original = prefix + "通訊方法，其中資料包含驗證碼。"
                texts, warnings = content_texts(specification(claims=[original], content=[]))
                self.assertEqual(texts, ["本發明的通訊方法，資料包含驗證碼。"])
                self.assertFalse(warnings)

    def test_reference_inside_technical_body_is_not_removed(self):
        original = "如請求項1所述之感測裝置，其中控制器儲存根據權利要求1所述的查核文字。"
        texts, _warnings = content_texts(specification(claims=[original], content=[]))
        self.assertEqual(texts, [
            "本發明的感測裝置，控制器儲存根據權利要求1所述的查核文字。"
        ])

    def test_dependent_feature_prefix_variants_are_omitted_in_both_patent_kinds(self):
        prefixes = (
            "其特徵在於：", "其特征在于:", "其特徵在於，", "其特征在于,",
            " 其特徵在於 ： ", "其特徵在於", "其中", "其中：", "其中，", "",
        )
        for kind, owner in (("invention", "本發明"), ("utility_model", "本實用新型")):
            for prefix in prefixes:
                with self.subTest(kind=kind, prefix=prefix):
                    parsed = specification(kind=kind, claims=[
                        "一種感測裝置，包括感測器。",
                        "根據權利要求1所述的感測裝置，" + prefix + "感測器具有A B端及控制器。",
                    ], content=[])
                    texts, warnings = content_texts(parsed)
                    self.assertEqual(texts[1], f"{owner}的感測裝置，感測器具有A B端及控制器。")
                    self.assertFalse(warnings)

    def test_only_opening_connector_is_removed_not_later_technical_text(self):
        body = "控制器儲存『其特徵在於：』文字，其中一個欄位為AB，另一欄位為A B。"
        original = "根據權利要求1所述的感測裝置，其特徵在於：" + body
        texts, warnings = content_texts(specification(claims=[original], content=[]))
        self.assertEqual(texts, ["本發明的感測裝置，" + body])
        self.assertFalse(warnings)

    def test_said_processor_in_feature_body_survives_preview_and_word_export(self):
        for kind, owner in (("invention", "本發明"), ("utility_model", "本實用新型")):
            for connector in ("其特徵在於：", "其特征在于：", "其中", ""):
                with self.subTest(kind=kind, connector=connector):
                    parsed = specification(kind=kind, claims=[
                        "一種感測裝置，包括處理器。",
                        "如請求項1所述之感測裝置，" + connector
                        + "所述處理器具有記憶體，所述記憶體儲存所述處理器的資料。",
                    ], content=[
                        f"{owner}的目的在於提供一種感測裝置。",
                        f"{owner}的感測裝置，所述處理器具有記憶體，"
                        "所述記憶體儲存所述處理器的資料。",
                    ])
                    expected = (
                        f"{owner}的感測裝置，所述處理器具有存儲器，"
                        "所述存儲器儲存所述處理器的資料。"
                    )
                    rules = [("記憶體", "存儲器")]
                    with patch.object(converter, "parse_taiwan_specification", return_value=parsed):
                        preview = converter.build_conversion_preview(
                            SOURCE, terminology_pairs=rules, traditional_characters=True,
                        )
                        content = converter.parse_edited_preview_text(preview_text(preview), kind)
                        self.assertEqual(content.invention_content[1], expected)
                        self.assertEqual(content.invention_content[1].count("所述"), 3)
                        with tempfile.TemporaryDirectory() as temporary:
                            for edited in (None, preview_text(preview)):
                                output = Path(temporary) / f"said_{edited is not None}.docx"
                                converter.convert_document(
                                    SOURCE, output, terminology_pairs=rules,
                                    edited_preview_text=edited,
                                )
                                self.assertEqual(
                                    docx_content(output)[1], converter._windows_simplified_chinese(expected)
                                )

    def test_existing_feature_narrative_is_not_duplicated_or_restored_with_connector(self):
        for connector in ("其特徵在於：", "其特征在于：", "其中", ""):
            with self.subTest(connector=connector):
                original = f"本發明的感測裝置，{connector}包括感測器。"
                extra = f"本發明的感測裝置，{connector}包括感測器及電池。"
                parsed = specification(claims=[
                    "根據權利要求1所述的感測裝置，其特徵在於：包括感測器。",
                ], content=[original, extra])
                texts, warnings = content_texts(parsed)
                self.assertEqual(texts, ["本發明的感測裝置，包括感測器。", extra])
                self.assertFalse(warnings)

    def test_claim1_purpose_and_unrelated_disclosure_keep_their_wording(self):
        claim1 = "一種感測裝置，其特徵在於：包括感測器。"
        original = "額外說明，其特徵在於：測試端不得移除。"
        texts, warnings = content_texts(specification(claims=[claim1], content=[original]))
        self.assertEqual(texts, ["本發明的目的在於提供一種感測裝置，包括感測器。", original])
        self.assertFalse(warnings)

    def test_ordered_dictionary_finishes_before_dependent_connector_removal(self):
        parsed = specification(claims=[
            "如請求項1所述之感測裝置，其中甲元件具有電池。",
        ], content=[])
        rules = [("其中甲元件", "其特徵在於：乙元件"), ("乙元件", "乙乙元件")]
        item = converter._build_invention_content(parsed, [])[0]
        counts = Counter()
        converted = converter._convert_content_paragraph(item, rules, counts, True)
        self.assertEqual(converted, "本发明的感测装置，乙乙元件具有电池。")
        self.assertEqual(counts, {"其中甲元件→其特徵在於：乙元件": 1, "乙元件→乙乙元件": 1})

    def test_clean_dependent_content_matches_preview_direct_and_edited_output(self):
        for kind, owner in (("invention", "本發明"), ("utility_model", "本實用新型")):
            with self.subTest(kind=kind):
                parsed = specification(kind=kind, claims=[
                    "一種感測裝置，包括感測器。",
                    "如請求項1所述之感測裝置，其特徵在於：其中一個感測器具有記憶體。",
                    "如請求項1所述之感測裝置，其中控制器包括備援電池。",
                ], content=[
                    f"{owner}的目的在於提供一種感測裝置。",
                    f"{owner}的感測裝置，其中一個感測器具有記憶體。",
                    f"{owner}的感測裝置，控制器包括備援電池。",
                    f"{owner}的有益效果在於：降低耗電。",
                ])
                rules = [("記憶體", "存儲器")]
                with patch.object(converter, "parse_taiwan_specification", return_value=parsed):
                    preview = converter.build_conversion_preview(
                        SOURCE, terminology_pairs=rules, traditional_characters=True,
                    )
                    expected = converter.parse_edited_preview_text(preview_text(preview), kind)
                    self.assertEqual(expected.invention_content[1:3], [
                        f"{owner}的感測裝置，其中一個感測器具有存儲器。",
                        f"{owner}的感測裝置，控制器包括備援電池。",
                    ])
                    self.assertTrue(all(
                        "其特征在于" in converter._windows_simplified_chinese(claim)
                        for claim in expected.claims
                    ))
                    self.assertTrue(expected.invention_content[-1].startswith(owner + "的有益效果在於"))
                    with tempfile.TemporaryDirectory() as temporary:
                        for template in converter.available_templates():
                            for edited in (None, preview_text(preview)):
                                output = Path(temporary) / f"{template}_{edited is not None}.docx"
                                converter.convert_document(
                                    SOURCE, output, template_key=template,
                                    terminology_pairs=rules, edited_preview_text=edited,
                                )
                                self.assertEqual(docx_content(output), [
                                    converter._windows_simplified_chinese(text)
                                    for text in expected.invention_content
                                ])

    def test_unknown_dependency_retains_every_character_and_warns(self):
        for original in (
            "如請求項一所述之感測裝置，其中資料不得移除。",
            "依前述請求項1之裝置，其中資料不得移除。",
            "如請求項1所述之，感測器未寫出標的。",
            "如請求項1所述之感測装置而無逗號。",
        ):
            with self.subTest(original=original):
                texts, warnings = content_texts(specification(claims=[original], content=[]))
                self.assertEqual(texts, [original])
                self.assertEqual(len(warnings), 1)
                self.assertIn("完整保留", warnings[0])

    def test_unparseable_independent_claim_is_not_truncated(self):
        original = "資料結構的完整描述而無逗號。"
        parsed = specification(claims=["一種裝置，包含元件。", original], content=[])
        texts, warnings = content_texts(parsed)
        self.assertEqual(texts[1], original)
        self.assertTrue(warnings)

    def test_inline_benefit_is_split_without_losing_preceding_disclosure(self):
        original = "本發明的目的在於節省空間。額外的保護層保留。本發明之功效在於：降低干擾。"
        texts, _warnings = content_texts(specification(content=[original]))
        self.assertEqual(texts[0], "本發明的目的在於節省空間。額外的保護層保留。")
        self.assertEqual(texts[-1], "本發明的有益效果在於：降低干擾。")

    def test_no_benefit_is_invented_when_source_has_none(self):
        texts, _warnings = content_texts(specification(content=[]))
        self.assertFalse(any("有益效果" in text for text in texts))
        self.assertEqual(len(texts), 4)

    def test_preview_comparison_uses_original_disclosure_not_claim_source(self):
        source_body = "所述處理器具有記憶體及來源專有模組。"
        parsed = specification(content=[source_body])
        with patch.object(converter, "parse_taiwan_specification", return_value=parsed):
            preview = converter.build_conversion_preview(
                SOURCE, terminology_pairs=[("記憶體", "存儲器")], traditional_characters=True
            )
        body = [item for item in preview.paragraphs if item.role == "body"]
        disclosure = next(item for item in body if "來源專有" in item.converted_text)
        self.assertEqual(disclosure.source_text, source_body)
        self.assertIn("存儲器", disclosure.converted_text)
        self.assertNotEqual(disclosure.source_text, disclosure.converted_text)

    def test_real_fixture_includes_every_claim_body_and_retains_source_paragraphs(self):
        parsed = converter.parse_taiwan_specification(SOURCE)
        texts, warnings = content_texts(parsed)
        self.assertFalse(warnings)
        joined = "\n".join(texts)
        for original in parsed.claims:
            body = original.partition("，")[2]
            if converter._DEPENDENT_CLAIM.fullmatch(original) and body.startswith("其中"):
                body = body[2:]
            self.assertIn(body, joined)
        for original in parsed.invention_content:
            self.assertIn(converter._normalize_patent_structure(original), texts)
        self.assertTrue(texts[-1].startswith("本發明的有益效果在於"))
        self.assertIn("本發明的方法，", joined)
        self.assertEqual(len(texts), len(parsed.invention_content) + len(parsed.claims))

    def test_direct_docx_and_traditional_preview_have_identical_generated_content(self):
        for template in converter.available_templates():
            with self.subTest(template=template), tempfile.TemporaryDirectory() as temporary:
                preview = converter.build_conversion_preview(
                    SOURCE, template_key=template, terminology_pairs=[], traditional_characters=True
                )
                expected = converter.parse_edited_preview_text(preview_text(preview), preview.specification_kind)
                output = Path(temporary) / "direct.docx"
                report = converter.convert_document(SOURCE, output, template_key=template, terminology_pairs=[])
                self.assertEqual(docx_content(output), [
                    converter._windows_simplified_chinese(item) for item in expected.invention_content
                ])
                self.assertEqual(report.sections_found["發明內容"], len(expected.invention_content))

    def test_utility_model_preview_and_direct_output_are_equivalent(self):
        parsed = specification(kind="utility_model", content=["本新型之功效在於：節省空間。"])
        with patch.object(converter, "parse_taiwan_specification", return_value=parsed):
            preview = converter.build_conversion_preview(SOURCE, terminology_pairs=[], traditional_characters=True)
            expected = converter.parse_edited_preview_text(preview_text(preview), parsed.kind)
            with tempfile.TemporaryDirectory() as temporary:
                output = Path(temporary) / "utility.docx"
                converter.convert_document(SOURCE, output, terminology_pairs=[])
                self.assertEqual(docx_content(output), [
                    converter._windows_simplified_chinese(item) for item in expected.invention_content
                ])
                with zipfile.ZipFile(output) as package:
                    root = ET.fromstring(package.read("word/document.xml"))
                headings = [
                    converter._normalized(converter._paragraph_text(paragraph))
                    for paragraph in root.findall("w:body/w:p", converter.NS)
                ]
                self.assertIn("实用新型内容", headings)
                self.assertNotIn("发明内容", headings)
        self.assertIn("實用新型內容", preview_text(preview))

    def test_ordered_dictionary_runs_once_per_generated_paragraph(self):
        parsed = specification(
            claims=["一種甲裝置，包括乙元件。"],
            content=["所述甲裝置包括乙元件。"],
        )
        rules = [("甲", "甲乙"), ("乙元件", "丙元件")]
        with patch.object(converter, "parse_taiwan_specification", return_value=parsed):
            preview = converter.build_conversion_preview(SOURCE, terminology_pairs=rules, traditional_characters=True)
            expected = converter.parse_edited_preview_text(preview_text(preview), parsed.kind)
            self.assertEqual(expected.invention_content, ["所述甲乙裝置包括丙元件。"])
            with tempfile.TemporaryDirectory() as temporary:
                output = Path(temporary) / "ordered.docx"
                report = converter.convert_document(SOURCE, output, terminology_pairs=rules)
                self.assertEqual(docx_content(output), [
                    converter._windows_simplified_chinese(item) for item in expected.invention_content
                ])
                self.assertEqual(report.replacement_counts, preview.replacement_counts)

    def test_edited_content_is_authoritative_and_not_regenerated_or_reconverted(self):
        preview = converter.build_conversion_preview(SOURCE, terminology_pairs=[], traditional_characters=True)
        lines = preview_text(preview).splitlines()
        start = lines.index("發明內容") + 1
        end = lines.index("附圖說明")
        lines[start:end] = ["人工自訂甲內容。"]
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "edited.docx"
            with patch.object(converter, "_build_source_content", side_effect=AssertionError("must not regenerate")):
                report = converter.convert_document(
                    SOURCE, output, terminology_pairs=[("甲", "乙")], edited_preview_text="\n".join(lines)
                )
            self.assertEqual(docx_content(output), ["人工自订甲内容。"])
            self.assertFalse(report.replacement_counts)


if __name__ == "__main__":
    unittest.main()
