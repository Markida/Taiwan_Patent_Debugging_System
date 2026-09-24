import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from features.taiwan_china_spec.converter import (
    ConversionError,
    default_terminology_path,
    parse_terminology_text,
)
from features.taiwan_china_spec.terminology_store import (
    CLOUD_TERMINOLOGY_ENVIRONMENT_VARIABLE,
    TerminologyDictionaryStore,
    default_cloud_terminology_path,
    default_terminology_cache_path,
)


class TerminologyDictionaryTextStoreTests(unittest.TestCase):
    def test_default_paths_use_company_unc_txt_and_versioned_txt_cache(self):
        with patch.dict(
            os.environ,
            {CLOUD_TERMINOLOGY_ENVIRONMENT_VARIABLE: ""},
            clear=False,
        ):
            self.assertEqual(
                str(default_cloud_terminology_path()),
                (
                    r"\\sic11\Doc\_CP\Saint-Island\_Patent\_MDS"
                    r"\taiwan_china_terminology.txt"
                ),
            )
        self.assertEqual(
            default_terminology_cache_path().name,
            "taiwan_china_terminology.v4.txt",
        )

    def test_environment_variable_still_overrides_cloud_path(self):
        override = r"D:\company\custom_dictionary.txt"
        with patch.dict(
            os.environ,
            {CLOUD_TERMINOLOGY_ENVIRONMENT_VARIABLE: override},
        ):
            self.assertEqual(default_cloud_terminology_path(), Path(override))

    def test_upload_round_trip_preserves_order_duplicates_and_blank_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cloud = root / "shared" / "dictionary.txt"
            cache = root / "cache" / "dictionary.v3.txt"
            store = TerminologyDictionaryStore(
                cloud_path=cloud,
                cache_path=cache,
                bundled_path=default_terminology_path(),
            )
            pairs = (
                ("先", "後"),
                ("重複", "第一步"),
                ("重複", "第二步"),
                ("整句刪除", ""),
            )

            snapshot = store.upload_pairs(pairs)

            expected_text = (
                "先\t後\n"
                "重複\t第一步\n"
                "重複\t第二步\n"
                "整句刪除\t\n"
            )
            self.assertEqual(snapshot.pairs, pairs)
            self.assertEqual(snapshot.source_kind, "雲端")
            self.assertEqual(snapshot.source_path, cloud)
            self.assertEqual(cloud.read_text(encoding="utf-8"), expected_text)
            self.assertEqual(cache.read_text(encoding="utf-8"), expected_text)
            self.assertEqual(store.load_preferred().pairs, pairs)
            self.assertEqual(list(cloud.parent.glob("*.tmp")), [])

    def test_another_user_loads_the_exact_198_uploaded_company_rules(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cloud = root / "shared" / "taiwan_china_terminology.txt"
            approved = tuple(
                parse_terminology_text(
                    default_terminology_path().read_text(encoding="utf-8")
                )
            )
            uploader = TerminologyDictionaryStore(
                cloud_path=cloud,
                cache_path=root / "user-a" / "cache.txt",
                bundled_path=default_terminology_path(),
            )
            downloader = TerminologyDictionaryStore(
                cloud_path=cloud,
                cache_path=root / "user-b" / "cache.txt",
                bundled_path=default_terminology_path(),
            )

            uploader.upload_pairs(approved)
            received = downloader.load_preferred()

            self.assertEqual(len(received.pairs), 198)
            self.assertEqual(received.pairs[-1], ("兩個合一個", "二合一"))
            self.assertEqual(received.pairs, approved)
            self.assertEqual(received.source_kind, "雲端")
            self.assertEqual(received.pairs[154], ("次多個", "次數"))
            self.assertEqual(
                received.pairs[186:189],
                (
                    ("應所述注意的是", "應該注意的是"),
                    ("單多個", "單數"),
                    ("雙多個", "雙數"),
                ),
            )

    def test_load_preferred_accepts_utf8_bom_and_updates_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cloud = root / "cloud.txt"
            cache = root / "cache.txt"
            cloud.write_text("圖式\t附圖\n刪除句\t\n", encoding="utf-8-sig")
            store = TerminologyDictionaryStore(
                cloud_path=cloud,
                cache_path=cache,
                bundled_path=default_terminology_path(),
            )

            snapshot = store.load_preferred()

            self.assertEqual(snapshot.source_kind, "雲端")
            self.assertEqual(
                snapshot.pairs,
                (("圖式", "附圖"), ("刪除句", "")),
            )
            self.assertEqual(
                cache.read_text(encoding="utf-8"),
                "圖式\t附圖\n刪除句\t\n",
            )

    def test_identical_cloud_dictionary_does_not_rewrite_local_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cloud = root / "cloud.txt"
            cache = root / "cache.txt"
            cloud.write_text("圖式\t附圖\n", encoding="utf-8")
            store = TerminologyDictionaryStore(
                cloud_path=cloud,
                cache_path=cache,
                bundled_path=default_terminology_path(),
            )
            first = store.load_preferred()
            self.assertEqual(first.pairs, (("圖式", "附圖"),))

            with patch(
                "features.taiwan_china_spec.terminology_store._write_pairs_atomically"
            ) as write:
                second = store.load_preferred()

            write.assert_not_called()
            self.assertEqual(second.pairs, first.pairs)
            self.assertEqual(second.source_kind, "雲端")

    def test_invalid_cloud_uses_last_known_good_cache_without_overwriting_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cloud = root / "cloud.txt"
            cache = root / "cache.txt"
            cloud.write_text("無效內容\n", encoding="utf-8")
            cache.write_text("圖式\t附圖\n", encoding="utf-8")
            store = TerminologyDictionaryStore(
                cloud_path=cloud,
                cache_path=cache,
                bundled_path=default_terminology_path(),
            )

            snapshot = store.load_preferred()

            self.assertEqual(snapshot.source_kind, "快取")
            self.assertEqual(snapshot.pairs, (("圖式", "附圖"),))
            self.assertIn("雲端辭典無法使用", snapshot.warning)
            self.assertEqual(cache.read_text(encoding="utf-8"), "圖式\t附圖\n")

    def test_failed_atomic_replace_keeps_previous_shared_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cloud = root / "cloud.txt"
            cache = root / "cache.txt"
            cloud.write_text("原本\t內容\n", encoding="utf-8")
            store = TerminologyDictionaryStore(
                cloud_path=cloud,
                cache_path=cache,
                bundled_path=default_terminology_path(),
            )

            with patch(
                "features.taiwan_china_spec.terminology_store.os.replace",
                side_effect=OSError("locked"),
            ):
                with self.assertRaisesRegex(ConversionError, "無法上傳"):
                    store.upload_pairs((("新", "詞"),))

            self.assertEqual(cloud.read_text(encoding="utf-8"), "原本\t內容\n")
            self.assertFalse(cache.exists())
            self.assertEqual(list(root.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
