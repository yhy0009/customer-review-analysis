"""Clean use-case counts, revalidation and persistence against real SQLite."""
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from src import cleaner
from src.clean_service import CleanService
from src.errors import StorageError
from src.models import (
    AnalysisResult, CleaningOptions, CleanRequest, DuplicatePolicy, ProcessingStatus,
    RawReview, Sentiment,
)
from src.sqlite_repository import SQLiteReviewRepository


class CleanServiceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name).resolve() / 'reviews.db'
        self.repository = SQLiteReviewRepository(self.path)
        self.addCleanup(self.repository.close)
        self.service = CleanService(self.repository, cleaner)

    def raw(self, source_id, **fields):
        values = dict(source_review_id=source_id, product_name='  제품  ',
                      review_date='2026/09/18', rating='5.0', review_text='  정말\n 좋아요  ')
        values.update(fields)
        return RawReview(**values)

    def seed(self, *rows):
        self.repository.save_raw_reviews(rows, DuplicatePolicy.SKIP)
        return self.repository.fetch_raw_reviews()

    def run_clean(self, policy=DuplicatePolicy.SKIP, min_length=3):
        return self.service.clean_reviews(CleanRequest(CleaningOptions(policy, min_length)))

    def analyze(self, review_id):
        self.repository.save_analysis(AnalysisResult(
            review_id=review_id, sentiment=Sentiment.POSITIVE, confidence=.9,
            analyzed_at=datetime.now(timezone.utc), provider='fake', model='test',
        ))

    def test_mixed_batch_preserves_raw_ids_and_counts_only_saved_successes(self):
        originals = self.seed(self.raw('bad-date', review_date='invalid'), self.raw('good'),
                              self.raw('bad-rating', rating=99), self.raw('short', review_text='짧음'))
        result = self.run_clean()
        self.assertEqual((result.processed, result.succeeded, result.rejected, result.failed), (4, 1, 3, 0))
        self.assertEqual([r.id for r in result.reviews], [originals[1].id])
        self.assertEqual(result.reviews[0].review_text, '정말 좋아요')
        self.assertEqual(result.reviews[0].product_name, '제품')
        self.assertEqual(result.reviews[0].rating, 5)
        self.assertEqual([e.item_ref for e in result.errors], [str(originals[i].id) for i in (0, 2, 3)])
        self.assertEqual(len(self.repository.fetch_raw_reviews(status=ProcessingStatus.REJECTED)), 3)
        self.assertEqual(self.repository.fetch_raw_reviews(), originals)
        self.assertEqual(self.repository.get_statistics().total_reviews, 1)

    def test_empty_and_repeated_skip_return_consistent_counts_and_preserve_analysis(self):
        self.assertEqual(self.run_clean().processed, 0)
        row = self.seed(self.raw('good'))[0]
        first = self.run_clean().reviews[0]
        self.analyze(row.id)
        result = self.run_clean(min_length=100)
        self.assertEqual((result.processed, result.succeeded, result.skipped, result.rejected), (1, 0, 1, 0))
        self.assertEqual(result.reviews, [])
        detail = self.repository.get_review(row.id)
        self.assertEqual(detail.review, first)
        self.assertIsNotNone(detail.analysis)

    def test_rejected_reviews_can_be_retried_with_relaxed_options(self):
        row = self.seed(self.raw('short', review_text='짧음'))[0]
        self.assertEqual(self.run_clean().rejected, 1)
        result = self.run_clean(min_length=2)
        self.assertEqual(result.succeeded, 1)
        self.assertEqual(result.reviews[0].id, row.id)
        self.assertFalse(self.repository.fetch_raw_reviews(status=ProcessingStatus.REJECTED))
        self.assertEqual(len(self.repository.fetch_raw_reviews(status=ProcessingStatus.CLEANED)), 1)

    def test_upsert_preserves_identical_analysis_but_invalidates_changed_clean_content(self):
        row = self.seed(self.raw('good'))[0]
        review = self.run_clean().reviews[0]
        self.analyze(row.id)
        self.assertEqual(self.run_clean(DuplicatePolicy.UPSERT).succeeded, 1)
        self.assertIsNotNone(self.repository.get_review(row.id).analysis)
        self.repository.save_clean_reviews([replace(review, review_text='previous normalization')],
                                           DuplicatePolicy.UPSERT)
        self.analyze(row.id)
        self.assertEqual(self.run_clean(DuplicatePolicy.UPSERT).succeeded, 1)
        self.assertEqual(self.repository.get_review(row.id).review.review_text, '정말 좋아요')
        self.assertIsNone(self.repository.get_review(row.id).analysis)

    def test_stricter_upsert_rejects_and_removes_previous_analysis(self):
        row = self.seed(self.raw('good'))[0]
        self.run_clean()
        self.analyze(row.id)
        result = self.run_clean(DuplicatePolicy.UPSERT, min_length=100)
        self.assertEqual((result.succeeded, result.rejected), (0, 1))
        self.assertIsNone(self.repository.get_review(row.id))
        self.assertEqual([r.id for r in self.repository.fetch_raw_reviews(status=ProcessingStatus.REJECTED)], [row.id])

    def test_unexpected_cleaning_error_is_failed_and_does_not_mark_rejected(self):
        row = self.seed(self.raw('good'))[0]
        with patch('src.cleaner._clean_one', side_effect=RuntimeError('internal detail')):
            result = self.run_clean()
        self.assertEqual((result.failed, result.rejected, result.succeeded), (1, 0, 0))
        self.assertEqual(result.errors[0].code, 'CLEANING_ERROR')
        self.assertEqual(result.errors[0].item_ref, str(row.id))
        self.assertNotIn('internal detail', result.errors[0].message)
        self.assertEqual(len(self.repository.fetch_raw_reviews(status=ProcessingStatus.RAW)), 1)

    def test_storage_row_failure_uses_original_id_and_excludes_unsaved_review(self):
        rows = self.seed(self.raw('first'), self.raw('bad-storage'), self.raw('last'))
        def clean_with_invalid_storage_value(reviews, options):
            result = cleaner.clean_reviews(reviews, options)
            if reviews[0].id == rows[1].id:
                result.reviews[0].rating = 99
            return result
        service = CleanService(self.repository, Mock(clean_reviews=clean_with_invalid_storage_value))
        result = service.clean_reviews(CleanRequest(CleaningOptions(DuplicatePolicy.SKIP, 3)))
        self.assertEqual((result.processed, result.succeeded, result.failed), (3, 2, 1))
        self.assertEqual([r.id for r in result.reviews], [rows[0].id, rows[2].id])
        self.assertEqual(result.errors[0].item_ref, str(rows[1].id))
        self.assertEqual(result.errors[0].code, 'CLEAN_STORAGE_INVALID_ITEM')

    def test_later_infrastructure_failure_propagates_and_keeps_prior_commits(self):
        rows = self.seed(self.raw('first'), self.raw('second'))
        save = self.repository.save_clean_reviews
        calls = 0
        def fail_second(reviews, policy):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise StorageError('simulated database failure')
            return save(reviews, policy)
        with patch.object(self.repository, 'save_clean_reviews', side_effect=fail_second):
            with self.assertRaises(StorageError):
                self.run_clean()
        self.assertEqual([r.id for r in self.repository.fetch_clean_reviews()], [rows[0].id])
        retried = self.run_clean()
        self.assertEqual((retried.succeeded, retried.skipped), (1, 1))

    def test_rejection_persistence_failure_propagates_without_false_result(self):
        self.seed(self.raw('bad', rating=99))
        with patch.object(self.repository, 'mark_cleaning_rejected', side_effect=StorageError('failure')):
            with self.assertRaises(StorageError):
                self.run_clean()
        self.assertEqual(len(self.repository.fetch_raw_reviews(status=ProcessingStatus.RAW)), 1)


if __name__ == '__main__':
    unittest.main()
