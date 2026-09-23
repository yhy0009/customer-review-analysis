"""Exercise cleaning races using two real SQLite connections."""
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from src import cleaner
from src.clean_service import CleanService
from src.models import (
    AnalysisResult, CleanRequest, CleaningOptions, DuplicatePolicy, RawReview, Sentiment,
)
from src.sqlite_repository import SQLiteReviewRepository


class CleanConcurrencyTests(unittest.TestCase):
    def raw(self, source, text='수정 전 리뷰입니다'):
        return RawReview(source_review_id=source, product_name='제품',
                         review_date='2026-09-18', rating=5, review_text=text)

    def test_changed_original_cannot_save_or_reject_stale_result(self):
        for policy in DuplicatePolicy:
            for rejected in (False, True):
                with self.subTest(policy=policy, rejected=rejected), tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / 'reviews.db'
                    with SQLiteReviewRepository(path) as repository, SQLiteReviewRepository(path) as writer:
                        repository.save_raw_reviews([
                            self.raw('r1', '짧음' if rejected else '수정 전 리뷰입니다'), self.raw('r2'),
                        ], DuplicatePolicy.SKIP)
                        request = CleanRequest(CleaningOptions(policy, 3))
                        def interleave(rows, options):
                            result = cleaner.clean_reviews(rows, options)
                            if rows[0].id == 1:
                                writer.save_raw_reviews([self.raw('r1', '수정 후 리뷰입니다')], DuplicatePolicy.UPSERT)
                                if rejected:
                                    # Another process has already cleaned/analyzed the corrected original.
                                    fresh = writer.fetch_raw_reviews()[0]
                                    accepted = cleaner.clean_reviews([fresh], options)
                                    writer.save_clean_reviews(accepted.reviews, DuplicatePolicy.SKIP)
                                    writer.save_analysis(AnalysisResult(
                                        review_id=1, sentiment=Sentiment.POSITIVE, confidence=.9,
                                        analyzed_at=datetime.now(timezone.utc), provider='fake', model='test',
                                    ))
                            return result
                        result = CleanService(repository, Mock(clean_reviews=interleave)).clean_reviews(request)
                        self.assertEqual((result.processed, result.succeeded, result.failed, result.rejected),
                                         (2, 1, 1, 0))
                        self.assertEqual([r.id for r in result.reviews], [2])
                        self.assertEqual(result.errors[0].code, 'RAW_REVIEW_CHANGED')
                        self.assertEqual(result.errors[0].item_ref, '1')
                        self.assertTrue(result.errors[0].retryable)
                        self.assertEqual(repository.fetch_raw_reviews()[0].review_text, '수정 후 리뷰입니다')
                        if rejected:
                            self.assertIsNotNone(repository.get_review(1).analysis)
                        else:
                            self.assertIsNone(repository.get_review(1))
                        retried = CleanService(repository, cleaner).clean_reviews(
                            CleanRequest(CleaningOptions(DuplicatePolicy.SKIP, 3)))
                        self.assertEqual(retried.failed, 0)
                        self.assertEqual(retried.succeeded, 0 if rejected else 1)
                        self.assertEqual(repository.get_review(1).review.review_text, '수정 후 리뷰입니다')

    def test_source_check_and_write_hold_the_same_write_lock(self):
        for reject in (False, True):
            with self.subTest(reject=reject), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'reviews.db'
                with SQLiteReviewRepository(path) as repository, closing(sqlite3.connect(path, timeout=0)) as writer:
                    repository.save_raw_reviews([self.raw('r1')], DuplicatePolicy.SKIP)
                    original = repository.fetch_raw_reviews()[0]
                    cleaned = cleaner.clean_reviews([original], CleaningOptions(DuplicatePolicy.SKIP, 3))
                    check = repository._check_raw_unchanged
                    attempts = []
                    def update_after_check(connection, expected):
                        check(connection, expected)
                        # A writer tries precisely between comparison and persistence.
                        with self.assertRaisesRegex(sqlite3.OperationalError, 'locked'):
                            writer.execute("UPDATE raw_reviews SET review_text='\"concurrent edit\"' WHERE id=1")
                        writer.rollback()
                        attempts.append(True)
                    with patch.object(repository, '_check_raw_unchanged', side_effect=update_after_check):
                        if reject:
                            repository.mark_cleaning_rejected(1, expected_raw=original)
                        else:
                            repository.save_clean_reviews(cleaned.reviews, DuplicatePolicy.SKIP,
                                                          expected_raw=[original])
                    self.assertEqual(attempts, [True])
                    # The lock is released after the operation, so later imports can proceed.
                    writer.execute("UPDATE raw_reviews SET source_file='later.csv' WHERE id=1")
                    writer.commit()


if __name__ == '__main__':
    unittest.main()
