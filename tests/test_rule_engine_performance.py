"""The bounded typo-candidate cache must not change any review finding."""

from functools import lru_cache
from pathlib import Path
import unittest
from unittest.mock import patch

from features.patent_review import parse_docx
from features.patent_review import rule_engine


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "TW_方向校正專利圖式標號檢核系統_發明說明書.docx"
COMPANY_SAMPLE = ROOT.parent / "01發明說明書-desc.docx"


def stable_review_payload(document):
    payload = rule_engine.review_document(document).to_dict()
    payload.pop("generated_at_utc", None)
    return payload


class RuleEnginePerformanceTests(unittest.TestCase):
    def assert_cached_and_uncached_results_equal(self, path):
        document = parse_docx(path)
        cached = stable_review_payload(document)
        # Disabling only the decorator runs exactly the same eligibility
        # predicates for every candidate, as before the optimization. Compare
        # complete findings, IDs, severities, offsets, details, catalog, etc.
        with patch.object(rule_engine, "lru_cache", lambda **_kwargs: lambda func: func):
            uncached = stable_review_payload(document)
        self.assertEqual(cached, uncached)

    def test_fixture_results_match_without_cache(self):
        self.assert_cached_and_uncached_results_equal(FIXTURE)

    @unittest.skipUnless(COMPANY_SAMPLE.is_file(), "Local company sample is not installed")
    def test_company_sample_results_match_without_cache(self):
        self.assert_cached_and_uncached_results_equal(COMPANY_SAMPLE)

    def test_candidate_cache_is_bounded_and_recreated_for_each_review(self):
        document = parse_docx(FIXTURE)
        caches = []

        def capture_cache(**kwargs):
            def decorate(func):
                cached = lru_cache(**kwargs)(func)
                caches.append(cached)
                return cached
            return decorate

        with patch.object(rule_engine, "lru_cache", capture_cache):
            list(rule_engine._component_name_typo_issues(document))
            list(rule_engine._component_name_typo_issues(document, ("測試白名單",)))
        self.assertEqual(len(caches), 2)
        self.assertIsNot(caches[0], caches[1])
        for cached in caches:
            info = cached.cache_info()
            self.assertEqual(info.maxsize, 16384)
            self.assertLessEqual(info.currsize, 16384)
            self.assertGreater(info.hits, 100)


if __name__ == "__main__":
    unittest.main()
