"""Real SQLite integration tests for the shared Raw/Clean/Analysis identity."""
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

from src.errors import StorageError, ValidationError
from src.models import (
    AnalysisResult, CleanReview, DuplicatePolicy, ProcessingStatus, RawReview,
    ReviewFilter, ReviewQuery, Sentiment, SortField, SortOrder,
)
from src.sqlite_repository import SQLiteReviewRepository, SqliteReviewRepository
from src.storage import ReviewRepository, SQLiteReviewRepository as CompatibleRepository


class RepositoryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'reviews.db'
        self.repo = SQLiteReviewRepository(self.path)
        self.addCleanup(self.repo.close)
        self.now = datetime.now(timezone.utc)

    def clean(self, source='a', **changes):
        self.repo.save_raw_reviews([RawReview(source_review_id=source)], DuplicatePolicy.SKIP)
        raw = next(r for r in self.repo.fetch_raw_reviews() if r.source_review_id == source)
        return replace(CleanReview(id=raw.id, source_review_id=source, product_name='제품',
                       rating=5, review_date=date(2026, 9, 8), review_text='좋아요', cleaned_at=self.now), **changes)

    def analyze(self, review, sentiment=Sentiment.POSITIVE):
        result = AnalysisResult(review_id=review.id, sentiment=sentiment, confidence=.9,
                                analyzed_at=self.now, provider='test', model='test', keywords=['배송', '배송'])
        self.repo.save_analysis(result)
        return result

    def test_imports_share_one_complete_implementation(self):
        self.assertIs(CompatibleRepository, SQLiteReviewRepository)
        self.assertIs(SqliteReviewRepository, SQLiteReviewRepository)
        self.assertIsInstance(self.repo, ReviewRepository)

    def test_clean_id_and_analysis_survive_reopening(self):
        first, second = self.clean('first'), self.clean('second')
        # Save only the second record: no independent Clean auto-increment ID.
        self.assertEqual(self.repo.save_clean_reviews([second], DuplicatePolicy.SKIP).succeeded, 1)
        self.analyze(second)
        self.repo.close()
        with SQLiteReviewRepository(self.path) as repo:
            self.assertIsNone(repo.get_review(first.id))
            detail = repo.get_review(second.id)
            self.assertEqual(detail.review.id, second.id)
            self.assertEqual(detail.analysis.review_id, second.id)
            self.assertEqual(detail.analysis.keywords, ['배송'])
            self.assertEqual(repo.fetch_raw_reviews(status=ProcessingStatus.ANALYZED)[0].id, second.id)

    def test_clean_skip_and_upsert_invalidate_only_changed_analysis(self):
        review = self.clean()
        self.repo.save_clean_reviews([review], DuplicatePolicy.SKIP)
        self.analyze(review)
        changed = replace(review, review_text='변경된 리뷰')
        self.assertEqual(self.repo.save_clean_reviews([changed], DuplicatePolicy.SKIP).skipped, 1)
        self.assertEqual(self.repo.get_review(review.id).review.review_text, '좋아요')
        self.repo.save_clean_reviews([review], DuplicatePolicy.UPSERT)
        self.assertIsNotNone(self.repo.get_review(review.id).analysis)
        self.repo.save_clean_reviews([changed], DuplicatePolicy.UPSERT)
        self.assertIsNone(self.repo.get_review(review.id).analysis)
        self.assertEqual(self.repo.fetch_unanalyzed_reviews()[0].id, review.id)
        self.assertEqual(self.repo.fetch_raw_reviews(status=ProcessingStatus.CLEANED)[0].id, review.id)

    def test_raw_upsert_invalidates_clean_and_analysis_by_stable_id(self):
        review = self.clean()
        self.repo.save_clean_reviews([review], DuplicatePolicy.SKIP)
        self.analyze(review)
        self.repo.save_raw_reviews([RawReview(source_review_id='a', review_text='수정')], DuplicatePolicy.UPSERT)
        self.assertIsNone(self.repo.get_review(review.id))
        self.assertEqual(self.repo.get_statistics().total_reviews, 0)
        self.assertEqual(self.repo.fetch_raw_reviews(status=ProcessingStatus.RAW)[0].id, review.id)

    def test_missing_raw_is_row_failure_and_other_clean_rows_commit(self):
        review = self.clean()
        result = self.repo.save_clean_reviews([replace(review, id=999), review], DuplicatePolicy.SKIP)
        self.assertEqual((result.processed, result.succeeded, result.failed), (2, 1, 1))
        self.assertEqual(result.errors[0].item_ref, '1')
        self.assertEqual(len(self.repo.fetch_clean_reviews()), 1)

    def test_clean_infrastructure_failure_restores_prior_analysis(self):
        first, second = self.clean('first'), self.clean('second')
        self.repo.save_clean_reviews([first], DuplicatePolicy.SKIP)
        self.analyze(first)
        with sqlite3.connect(self.path) as connection:
            connection.execute("CREATE TRIGGER fail_clean BEFORE INSERT ON clean_reviews "
                               "BEGIN SELECT abs(-9223372036854775808); END")
        with self.assertRaises(StorageError):
            self.repo.save_clean_reviews([replace(first, review_text='수정'), second], DuplicatePolicy.UPSERT)
        self.assertEqual(self.repo.get_review(first.id).review.review_text, '좋아요')
        self.assertIsNotNone(self.repo.get_review(first.id).analysis)
        self.assertIsNone(self.repo.get_review(second.id))

    def test_analysis_failure_is_retryable_without_double_counting_or_secret_leaks(self):
        review = self.clean()
        self.repo.save_clean_reviews([review], DuplicatePolicy.SKIP)
        self.analyze(review)
        with self.assertLogs('customer_review_analysis.storage', level='WARNING') as logs:
            self.repo.mark_analysis_failed(review.id, 'SECRET_API_KEY and original text')
        self.assertNotIn('SECRET', str(logs.output))
        stats = self.repo.get_statistics()
        self.assertEqual((stats.analyzed_reviews, stats.failed_reviews, stats.unanalyzed_reviews), (0, 1, 0))
        self.assertEqual(self.repo.fetch_unanalyzed_reviews()[0].id, review.id)
        with sqlite3.connect(self.path) as connection:
            self.assertNotIn('SECRET', connection.execute('SELECT error_message FROM clean_reviews').fetchone()[0])
        self.analyze(review, Sentiment.NEGATIVE)
        self.assertEqual(self.repo.get_statistics().failed_reviews, 0)
        self.assertEqual(self.repo.get_review(review.id).analysis.sentiment, Sentiment.NEGATIVE)

    def test_analysis_write_failure_rolls_back_existing_result(self):
        review = self.clean()
        self.repo.save_clean_reviews([review], DuplicatePolicy.SKIP)
        result = self.analyze(review)
        with sqlite3.connect(self.path) as connection:
            connection.execute("CREATE TRIGGER fail_status BEFORE UPDATE ON clean_reviews "
                               "BEGIN SELECT abs(-9223372036854775808); END")
        with self.assertRaises(StorageError):
            self.repo.save_analysis(replace(result, sentiment=Sentiment.NEGATIVE))
        self.assertEqual(self.repo.get_review(review.id).analysis.sentiment, Sentiment.POSITIVE)

    def test_filter_pagination_and_statistics_consume_same_data(self):
        reviews = [self.clean('a', product_name='Straße_100%', rating=5),
                   self.clean('b', product_name='STRASSE-other', rating=2, review_date=date(2026, 9, 9)),
                   self.clean('c', rating=3)]
        self.repo.save_clean_reviews(reviews, DuplicatePolicy.SKIP)
        self.analyze(reviews[0])
        self.analyze(reviews[1], Sentiment.NEGATIVE)
        self.assertEqual(len(self.repo.fetch_clean_reviews(ReviewFilter(product_name='STRASSE'))), 2)
        self.assertEqual(len(self.repo.fetch_clean_reviews(ReviewFilter(product_name='_100%'))), 1)
        query = ReviewQuery(page=2, size=1, sort=SortField.RATING, order=SortOrder.ASC)
        page = self.repo.list_reviews(query)
        self.assertEqual((page.total_items, page.total_pages, page.items[0].review.rating), (3, 3, 3))
        selected = ReviewFilter(sentiment=Sentiment.NEGATIVE, date_from=date(2026, 9, 9), date_to=date(2026, 9, 9))
        self.assertEqual(self.repo.fetch_clean_reviews(selected)[0].id, reviews[1].id)
        stats = self.repo.get_statistics()
        self.assertEqual((stats.total_reviews, stats.analyzed_reviews, stats.unanalyzed_reviews), (3, 2, 1))
        self.assertEqual(stats.sentiment_ratios[Sentiment.NEGATIVE], .5)
        self.assertEqual(stats.top_positive_keywords[0].count, 1)
        self.assertEqual(stats.rating_sentiment_matrix[2][Sentiment.NEGATIVE], 1)

    def test_empty_results_and_invalid_limits(self):
        self.assertEqual(self.repo.get_statistics().total_reviews, 0)
        self.assertEqual(self.repo.list_reviews(ReviewQuery()).total_pages, 0)
        for limit in (0, -1, True):
            with self.assertRaises(ValidationError):
                self.repo.fetch_unanalyzed_reviews(limit=limit)
        with self.assertRaises(StorageError):
            self.repo.mark_analysis_failed(999, 'missing')

    def test_from_config_rejects_other_backend(self):
        with self.assertRaises(StorageError):
            SQLiteReviewRepository.from_config({'storage': {'backend': 'jsonl', 'database_path': str(self.path)}})


if __name__ == '__main__':
    unittest.main()
