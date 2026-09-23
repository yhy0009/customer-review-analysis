"""Cleaning rejection is durable and atomic with dependent result removal."""
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from src.errors import StorageError, ValidationError
from src.models import (
    AnalysisResult, CleanReview, DuplicatePolicy, ProcessingStatus, RawReview, Sentiment,
)
from src.sqlite_repository import SQLiteReviewRepository


class CleanRejectionStorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name).resolve() / 'reviews.db'
        self.repository = SQLiteReviewRepository(self.path)
        self.addCleanup(self.repository.close)
        self.repository.save_raw_reviews([
            RawReview(source_review_id='external-1', review_text='original text'),
            RawReview(source_review_id='external-2', review_text='another text'),
        ], DuplicatePolicy.SKIP)
        self.review_id = self.repository.fetch_raw_reviews()[0].id

    def seed_analysis(self):
        now = datetime.now(timezone.utc)
        self.repository.save_clean_reviews([CleanReview(
            id=self.review_id, product_name='제품', review_date=date(2026, 9, 18),
            rating=5, review_text='original text', cleaned_at=now,
        )], DuplicatePolicy.SKIP)
        self.repository.save_analysis(AnalysisResult(
            review_id=self.review_id, sentiment=Sentiment.POSITIVE, confidence=.9,
            analyzed_at=now, provider='fake', model='test',
        ))

    def test_rejection_preserves_raw_and_survives_reopening_and_repeat(self):
        original = self.repository.fetch_raw_reviews()[0]
        for _ in range(2):
            self.repository.mark_cleaning_rejected(self.review_id)
        with SQLiteReviewRepository(self.path) as reopened:
            self.assertEqual(reopened.fetch_raw_reviews(status=ProcessingStatus.REJECTED), [original])
            self.assertEqual(len(reopened.fetch_raw_reviews(status=ProcessingStatus.RAW)), 1)
            self.assertIsNone(reopened.get_review(self.review_id))

    def test_rejection_removes_clean_and_analysis_and_query_targets(self):
        self.seed_analysis()
        self.repository.mark_cleaning_rejected(self.review_id)
        self.assertEqual(self.repository.get_statistics().total_reviews, 0)
        self.assertFalse(self.repository.fetch_unanalyzed_reviews())
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM analysis_results').fetchone()[0], 0)
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 2)

    def test_status_write_failure_rolls_back_clean_and_analysis_removal(self):
        self.seed_analysis()
        with sqlite3.connect(self.path) as connection:
            connection.execute("CREATE TRIGGER fail_rejection BEFORE UPDATE OF status ON raw_reviews "
                               "WHEN NEW.status = 'REJECTED' BEGIN "
                               "SELECT RAISE(ABORT, 'simulated failure'); END")
        with self.assertRaises(StorageError):
            self.repository.mark_cleaning_rejected(self.review_id)
        self.assertIsNotNone(self.repository.get_review(self.review_id).analysis)
        self.assertEqual(len(self.repository.fetch_raw_reviews(status=ProcessingStatus.ANALYZED)), 1)
        self.assertFalse(self.repository.fetch_raw_reviews(status=ProcessingStatus.REJECTED))

    def test_read_only_connection_cannot_reject_or_delete_results(self):
        self.seed_analysis()
        before = self.path.read_bytes()
        with SQLiteReviewRepository(self.path, read_only=True) as repository:
            with self.assertRaises(StorageError):
                repository.mark_cleaning_rejected(self.review_id)
        self.assertEqual(self.path.read_bytes(), before)

    def test_invalid_missing_and_closed_targets_raise_without_changing_rows(self):
        for value in (0, -1, True, '1'):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                self.repository.mark_cleaning_rejected(value)
        with self.assertRaises(StorageError):
            self.repository.mark_cleaning_rejected(999)
        self.assertEqual(len(self.repository.fetch_raw_reviews(status=ProcessingStatus.RAW)), 2)
        self.repository.close()
        with self.assertRaises(StorageError):
            self.repository.mark_cleaning_rejected(self.review_id)


if __name__ == '__main__':
    unittest.main()
