"""Exercise both import paths and the real analysis service against one database."""

import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import Mock

from src.ai_provider import AnalysisProvider, NonRetryableAIError, ProviderResponse
from src.analysis_service import AnalysisService
from src.analyzer import BatchReviewAnalyzer
from src.errors import AIProviderError, StorageError
from src.models import (
    AnalysisOptions, AnalysisResult, AnalyzeRequest, AnalyzeTarget, CleanReview,
    DuplicatePolicy, ProcessingStatus, RawReview, ReviewFilter, ReviewQuery, Sentiment,
)
from src.sqlite_repository import SQLiteReviewRepository
from src.storage import SQLiteReviewRepository as CompatibleRepository


class SQLiteConsistencyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "reviews.db"
        self.writer = CompatibleRepository(self.path)
        self.addCleanup(self.writer.close)
        self.reader = SQLiteReviewRepository(self.path)
        self.addCleanup(self.reader.close)
        self.now = datetime.now(timezone.utc)
        self.raw = [RawReview(source_review_id=source, product_name="이어폰",
                              review_date="2026-09-11", rating=5, review_text="음질이 좋아요")
                    for source in ("unprocessed", "first", "second")]
        self.writer.save_raw_reviews(self.raw, DuplicatePolicy.SKIP)
        # Leave the first Raw row uncleaned so independent Clean IDs would fail.
        self.reviews = [CleanReview(id=raw.id, source_review_id=raw.source_review_id,
                                    product_name=raw.product_name, rating=raw.rating,
                                    review_date=date(2026, 9, 11), review_text=raw.review_text,
                                    cleaned_at=self.now)
                        for raw in self.reader.fetch_raw_reviews()[1:]]
        self.reader.save_clean_reviews(self.reviews, DuplicatePolicy.SKIP)
        self.provider = Mock(spec=AnalysisProvider)
        self.response = ProviderResponse(json.dumps({
            "sentiment": "positive", "confidence": .9,
            "summary": "음질에 만족합니다.", "keywords": ["음질"],
        }), "test-model")
        self.provider.complete.return_value = self.response
        self.sleep = Mock()
        self.analyzer = BatchReviewAnalyzer(self.writer, self.provider, sleep=self.sleep)
        self.options = AnalysisOptions(provider="fake", model="fake", timeout_seconds=10,
                                       max_retries=1)
        self.service = AnalysisService(self.reader, self.analyzer, self.options)

    def assert_stats(self, analyzed, failed, unanalyzed):
        stats = self.reader.get_statistics()
        self.assertEqual(stats, self.writer.get_statistics())
        self.assertEqual((stats.analyzed_reviews, stats.failed_reviews, stats.unanalyzed_reviews),
                         (analyzed, failed, unanalyzed))
        self.assertEqual(stats.total_reviews, analyzed + failed + unanalyzed)
        self.assertEqual(sum(stats.sentiment_counts.values()), analyzed)
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM clean_reviews c JOIN raw_reviews r ON r.id=c.id "
                "WHERE c.status != r.status"
            ).fetchone()[0], 0)

    def test_skip_upsert_and_reopen_share_identity_across_imports(self):
        batch = self.analyzer.analyze_reviews(self.reviews, self.options)
        self.assertEqual([result.review_id for result in batch.results], [2, 3])
        self.assertIsNone(self.reader.get_review(1))
        duplicate = self.reader.save_raw_reviews(self.raw, DuplicatePolicy.SKIP)
        self.assertEqual(duplicate.skipped, 3)
        self.assert_stats(2, 0, 0)

        changed = replace(self.raw[1], review_text="내용 수정")
        self.reader.save_raw_reviews([changed], DuplicatePolicy.UPSERT)
        self.assertIsNone(self.writer.get_review(2))
        self.assertIsNotNone(self.writer.get_review(3).analysis)
        self.assertEqual([r.id for r in self.writer.fetch_raw_reviews()], [1, 2, 3])
        self.assert_stats(1, 0, 0)

        self.writer.save_clean_reviews([replace(self.reviews[0], review_text="내용 수정")],
                                       DuplicatePolicy.SKIP)
        self.assert_stats(1, 0, 1)
        self.writer.close()
        self.reader.close()
        with CompatibleRepository(self.path) as reopened:
            self.assertEqual([r.id for r in reopened.fetch_clean_reviews()], [2, 3])
            self.assertEqual([r.id for r in reopened.fetch_unanalyzed_reviews()], [2])
            self.assertEqual(reopened.get_review(3).analysis.review_id, 3)

    def test_retry_skip_force_and_statistics_follow_persisted_status(self):
        self.provider.complete.side_effect = [AIProviderError("private"), self.response,
                                              NonRetryableAIError("private")]
        first = self.service.analyze_reviews(AnalyzeRequest(target=AnalyzeTarget.UNANALYZED))
        self.assertEqual((first.succeeded, first.failed, first.skipped), (1, 1, 0))
        self.sleep.assert_called_once_with(1)
        self.assert_stats(1, 1, 0)
        self.assertEqual([r.id for r in self.reader.fetch_unanalyzed_reviews()], [3])

        self.provider.complete.side_effect = None
        second = self.service.analyze_reviews(AnalyzeRequest(target=AnalyzeTarget.ALL))
        self.assertEqual((second.succeeded, second.skipped), (1, 1))
        self.assertEqual(self.provider.complete.call_count, 4)
        self.assert_stats(2, 0, 0)

        self.provider.complete.side_effect = NonRetryableAIError("private")
        forced = self.service.analyze_reviews(AnalyzeRequest(
            target=AnalyzeTarget.REVIEW_ID, review_id=2, force=True))
        self.assertEqual(forced.failed, 1)
        self.assertIsNone(self.reader.get_review(2).analysis)
        self.assert_stats(1, 1, 0)

        self.provider.complete.side_effect = None
        retry = self.service.analyze_reviews(AnalyzeRequest(target=AnalyzeTarget.UNANALYZED))
        self.assertEqual((retry.processed, retry.succeeded), (1, 1))
        self.assert_stats(2, 0, 0)
        self.assertEqual([r.id for r in self.reader.fetch_raw_reviews(
            status=ProcessingStatus.ANALYZED)], [2, 3])
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute(
                "SELECT error_message FROM clean_reviews ORDER BY id").fetchall(), [(None,), (None,)])

    def test_batch_storage_failure_preserves_prior_success_without_ai_retry(self):
        with sqlite3.connect(self.path) as connection:
            connection.execute("CREATE TRIGGER fail_status BEFORE UPDATE ON clean_reviews "
                               "WHEN NEW.id=3 BEGIN SELECT abs(-9223372036854775808); END")
        with self.assertRaises(StorageError):
            self.service.analyze_reviews(AnalyzeRequest(target=AnalyzeTarget.ALL))
        self.assertEqual(self.provider.complete.call_count, 2)
        self.sleep.assert_not_called()
        self.assertIsNotNone(self.reader.get_review(2).analysis)
        self.assertIsNone(self.reader.get_review(3).analysis)
        self.assert_stats(1, 0, 1)

    def test_failed_status_transaction_restores_previous_analysis_on_db_error(self):
        self.analyzer.analyze_reviews(self.reviews, self.options)
        before = self.reader.get_review(2)
        with sqlite3.connect(self.path) as connection:
            connection.execute("CREATE TRIGGER fail_status BEFORE UPDATE ON raw_reviews "
                               "WHEN NEW.id=2 BEGIN SELECT abs(-9223372036854775808); END")
        with self.assertRaises(StorageError):
            self.writer.mark_analysis_failed(2, "private")
        self.assertEqual(self.reader.get_review(2), before)
        self.assert_stats(2, 0, 0)

    def test_statistics_preserve_zero_buckets_precision_and_keyword_tie_order(self):
        extra = RawReview(source_review_id="third")
        self.writer.save_raw_reviews([extra], DuplicatePolicy.SKIP)
        self.reviews.append(replace(self.reviews[0], id=4, source_review_id="third", rating=3))
        self.writer.save_clean_reviews([self.reviews[-1]], DuplicatePolicy.SKIP)
        for review, keywords in zip(self.reviews, (["z", "a", "z"], ["a"], ["b"])):
            self.writer.save_analysis(AnalysisResult(
                review_id=review.id, sentiment=Sentiment.POSITIVE, confidence=.9,
                analyzed_at=self.now, provider="fake", model="fake", keywords=keywords))
        stats = self.reader.get_statistics()
        self.assertAlmostEqual(stats.average_rating, 13 / 3)
        self.assertEqual([(k.keyword, k.count) for k in stats.top_positive_keywords],
                         [("a", 2), ("b", 1), ("z", 1)])
        self.assertEqual(set(stats.rating_sentiment_matrix), set(range(1, 6)))
        self.assertEqual(stats.rating_sentiment_matrix[1], {s: 0 for s in Sentiment})
        self.assertEqual(stats.daily_sentiment_counts[date(2026, 9, 11)][Sentiment.NEGATIVE], 0)
        filters = ReviewFilter(rating_min=4, sentiment=Sentiment.POSITIVE)
        self.assertEqual(self.reader.get_statistics(filters).total_reviews, 2)
        self.assertEqual(self.writer.list_reviews(ReviewQuery(filters=filters)).total_items, 2)
        empty = self.reader.get_statistics(ReviewFilter(product_name="no match"))
        self.assertIsNone(empty.average_rating)
        self.assertEqual(empty.rating_sentiment_matrix, {r: {s: 0 for s in Sentiment} for r in range(1, 6)})


class LegacySQLiteTests(unittest.TestCase):
    def test_old_schema_and_data_remain_untouched_through_both_import_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.db"
            schema = Path(__file__).with_name("fixtures") / "sqlite_legacy_v0.sql"
            with sqlite3.connect(path) as connection:
                connection.executescript(schema.read_text())
                connection.execute(
                    "INSERT INTO raw_reviews (id, review_text, dedupe_key, status, created_at, updated_at) "
                    "VALUES (1, 'original', 'id:legacy', 'ANALYZED', 'time', 'time')")
                connection.execute(
                    "INSERT INTO clean_reviews (id, raw_id, product_name, review_date, rating, "
                    "review_text, cleaned_at, dedupe_key) "
                    "VALUES (7, 1, 'product', '2026-09-11', 5, 'original', 'time', 'id:legacy')")
                connection.execute(
                    "INSERT INTO analysis_results (review_id, sentiment, confidence, analyzed_at, provider, model) "
                    "VALUES (7, 'positive', .9, 'time', 'fake', 'fake')")
            before = path.read_bytes()
            for repository in (CompatibleRepository, SQLiteReviewRepository):
                with self.assertRaises(StorageError):
                    repository(path)
                self.assertEqual(path.read_bytes(), before)
            with sqlite3.connect(path) as connection:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 0)
                self.assertEqual(connection.execute(
                    "SELECT c.id, c.raw_id, a.review_id FROM clean_reviews c "
                    "JOIN analysis_results a ON a.review_id=c.id").fetchall(), [(7, 1, 7)])


if __name__ == "__main__":
    unittest.main()
