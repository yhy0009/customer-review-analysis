"""Extraction against real SQLite: selection, read isolation and no analysis writes."""

import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from src.ai_provider import AnalysisProvider, ProviderResponse
from src.errors import AIProviderError, StorageError
from src.insight_extractor import AIInsightExtractor
from src.insight_service import InsightService
from src.models import (
    AnalysisOptions, AnalysisResult, CleanReview, DuplicatePolicy, ExtractRequest,
    RawReview, ReviewFilter, Sentiment,
)
from src.storage import SQLiteReviewRepository


class InsightServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "reviews.db"
        self.repo = SQLiteReviewRepository(self.path)
        self.addCleanup(self.repo.close)
        self.provider = Mock(spec=AnalysisProvider)
        self.provider.complete.return_value = ProviderResponse(json.dumps({
            "issues": ["배송 지연"], "improvement_suggestions": ["출고 일정을 점검하세요."],
            "summary": "일부 리뷰에서 배송 불편이 보고됩니다.",
        }), "test")
        self.options = AnalysisOptions(provider="fake", model="test", timeout_seconds=10, max_retries=0)
        self.extractor = AIInsightExtractor(self.options, self.provider)
        self.service = InsightService(self.repo, self.extractor, snapshot=self.repo.read_snapshot)
        self.now = datetime.now(timezone.utc)
        # Small pages exercise the actual multi-page algorithm with a compact fixture.
        page_patch = patch("src.insight_service._PAGE_SIZE", 2)
        page_patch.start()
        self.addCleanup(page_patch.stop)

    def seed(self):
        self.repo.save_raw_reviews([RawReview(source_review_id=str(i)) for i in range(1, 7)], DuplicatePolicy.SKIP)
        self.repo.save_clean_reviews([
            CleanReview(id=i, product_name="Straße_100%" if i % 2 else "다른 제품", rating=4 if i % 2 else 2,
                        review_date=date(2026, 9, i), review_text=f"배송 리뷰 {i}", cleaned_at=self.now)
            for i in range(1, 7)], DuplicatePolicy.SKIP)
        for i, sentiment in [(3, Sentiment.NEGATIVE), (4, Sentiment.NEGATIVE),
                             (5, Sentiment.POSITIVE), (6, Sentiment.NEUTRAL)]:
            self.repo.save_analysis(AnalysisResult(review_id=i, sentiment=sentiment, confidence=.8,
                analyzed_at=self.now, provider="fake", model="test", keywords=["배송", "배송", "포장"]))
        self.repo.mark_analysis_failed(2, "분석 실패")

    def selected_texts(self):
        body = json.loads(self.provider.complete.call_args.args[0][1]["content"])
        return [r["review_text"] for r in body["reviews"]]

    def test_limit_counts_analyzed_reviews_across_pages(self):
        self.seed()
        result = self.service.extract_insights(ExtractRequest(ReviewFilter(), limit=2))
        self.assertEqual(result.review_count, 2)
        self.assertEqual(self.selected_texts(), ["배송 리뷰 3", "배송 리뷰 4"])
        self.assertEqual([(k.keyword, k.count) for k in result.negative_keywords], [("배송", 2), ("포장", 2)])

    def test_all_rows_and_keyword_counts_match_storage_statistics(self):
        self.seed()
        result = self.service.extract_insights(ExtractRequest(ReviewFilter()))
        stats = self.repo.get_statistics()
        self.assertEqual(result.review_count, stats.analyzed_reviews)
        self.assertEqual(result.review_count, 4)
        self.assertEqual(result.positive_keywords, stats.top_positive_keywords)
        self.assertEqual(result.negative_keywords, stats.top_negative_keywords)
        self.assertEqual(self.selected_texts(), [f"배송 리뷰 {i}" for i in range(3, 7)])

    def test_combined_filters_match_core_and_repository(self):
        self.seed()
        filters = ReviewFilter(product_name="STRASSE_100%", sentiment=Sentiment.NEGATIVE,
                               date_from=date(2026, 9, 3), date_to=date(2026, 9, 3), rating_min=4)
        result = self.service.extract_insights(ExtractRequest(filters))
        self.assertEqual(result.review_count, 1)
        self.assertEqual(self.selected_texts(), ["배송 리뷰 3"])
        self.assertEqual(result.negative_keywords, self.repo.get_statistics(filters).top_negative_keywords)

    def test_empty_result_no_api_call(self):
        self.assertEqual(self.service.extract_insights(ExtractRequest(ReviewFilter())).review_count, 0)
        self.seed()
        result = self.service.extract_insights(ExtractRequest(ReviewFilter(date_to=date(2026, 9, 2))))
        self.assertEqual(result.review_count, 0)
        self.provider.complete.assert_not_called()

    def test_success_and_ai_failure_leave_analysis_and_status_unchanged(self):
        self.seed()
        before = [self.repo.get_review(i) for i in range(1, 7)]
        stats = self.repo.get_statistics()
        self.service.extract_insights(ExtractRequest(ReviewFilter()))
        self.provider.complete.side_effect = AIProviderError("private provider body")
        with self.assertRaises(AIProviderError):
            self.service.extract_insights(ExtractRequest(ReviewFilter()))
        self.assertEqual([self.repo.get_review(i) for i in range(1, 7)], before)
        self.assertEqual(self.repo.get_statistics(), stats)
        self.assertEqual([r.id for r in self.repo.fetch_unanalyzed_reviews()], [1, 2])

    def test_storage_failure_prevents_ai_call(self):
        self.repo.close()
        with self.assertRaises(StorageError):
            self.service.extract_insights(ExtractRequest(ReviewFilter()))
        self.provider.complete.assert_not_called()

    def test_snapshot_is_released_before_provider_call(self):
        self.seed()
        response = self.provider.complete.return_value
        def complete(*args):
            # A second SQLite connection can commit even in rollback-journal mode.
            with SQLiteReviewRepository(self.path) as writer:
                writer.save_raw_reviews([RawReview(source_review_id="new")], DuplicatePolicy.SKIP)
            return response
        self.provider.complete.side_effect = complete
        self.assertEqual(self.service.extract_insights(ExtractRequest(ReviewFilter())).review_count, 4)
        self.assertEqual(len(self.repo.fetch_raw_reviews()), 7)

    def test_snapshot_keeps_same_count_content_changes_out_of_selection(self):
        self.seed()
        with sqlite3.connect(self.path) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
        with SQLiteReviewRepository(self.path) as writer:
            read = self.repo.list_reviews
            def interleave(query):
                result = read(query)
                if query.page == 1:
                    # Same total row count, but a later row loses its analysis.
                    clean = self.repo.get_review(5).review
                    writer.save_clean_reviews([replace(clean, review_text="changed")], DuplicatePolicy.UPSERT)
                return result
            with patch.object(self.repo, "list_reviews", side_effect=interleave):
                result = self.service.extract_insights(ExtractRequest(ReviewFilter()))
        self.assertEqual(result.review_count, 4)
        self.assertIn("배송 리뷰 5", self.selected_texts())
        self.assertIsNone(self.repo.get_review(5).analysis)

    def test_inconsistent_page_counts_fail_before_ai_without_snapshot(self):
        self.seed()
        read = self.repo.list_reviews
        def inconsistent(query):
            result = read(query)
            if query.page > 1:
                result.total_items -= 1
            return result
        with patch.object(self.repo, "list_reviews", side_effect=inconsistent):
            with self.assertRaises(StorageError):
                InsightService(self.repo, self.extractor).extract_insights(ExtractRequest(ReviewFilter()))
        self.provider.complete.assert_not_called()

    def test_missing_or_repeated_page_rows_fail_before_ai(self):
        self.seed()
        read = self.repo.list_reviews
        for mode in ("missing", "repeated"):
            with self.subTest(mode=mode):
                def corrupt(query):
                    result = read(query)
                    if query.page == 2:
                        if mode == "missing":
                            result.items = []
                        else:
                            result.items = [self.repo.get_review(1)]
                    return result
                with patch.object(self.repo, "list_reviews", side_effect=corrupt):
                    with self.assertRaises(StorageError):
                        self.service.extract_insights(ExtractRequest(ReviewFilter()))
        self.provider.complete.assert_not_called()


if __name__ == "__main__":
    unittest.main()
