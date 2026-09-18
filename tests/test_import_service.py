"""Import orchestration against real storage, including duplicate lifecycle."""
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import Mock

from src.errors import InputFileError, StorageError
from src.import_service import ImportService
from src.models import (
    AnalysisResult, CleanReview, DuplicatePolicy, ImportRequest, ProcessingStatus,
    RawReview, Sentiment,
)
from src.sqlite_repository import SQLiteReviewRepository


class ImportServiceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.repository = SQLiteReviewRepository(self.root / 'reviews.db')
        self.addCleanup(self.repository.close)
        self.collector = Mock()
        self.service = ImportService(self.repository, self.collector)
        self.request = ImportRequest(self.root / 'reviews.csv', DuplicatePolicy.SKIP)

    def test_invalid_review_values_are_preserved_for_later_cleaning(self):
        self.collector.load_reviews.return_value = [RawReview(
            source_review_id='external-1', product_name=None, review_date='not-a-date',
            rating=99, review_text='', source_file=str(self.request.file),
            raw_payload={'extra': 'original value'},
        )]
        result = self.service.import_reviews(self.request)
        self.assertEqual((result.processed, result.succeeded, result.rejected), (1, 1, 0))
        self.collector.load_reviews.assert_called_once_with(self.request.file)
        rows = self.repository.fetch_raw_reviews(status=ProcessingStatus.RAW)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].rating, 99)
        self.assertEqual(rows[0].review_date, 'not-a-date')
        self.assertEqual(rows[0].review_text, '')
        self.assertEqual(rows[0].source_file, str(self.request.file))
        self.assertEqual(rows[0].raw_payload, {'extra': 'original value'})
        self.assertEqual(self.repository.fetch_clean_reviews(), [])

    def test_skip_preserves_analysis_and_upsert_invalidates_it_without_changing_id(self):
        self.collector.load_reviews.return_value = [RawReview(
            source_review_id='external-1', review_text='original review')]
        self.service.import_reviews(self.request)
        review_id = self.repository.fetch_raw_reviews()[0].id
        now = datetime.now(timezone.utc)
        self.repository.save_clean_reviews([CleanReview(
            id=review_id, product_name='제품', review_date=date(2026, 9, 18), rating=5,
            review_text='original review', cleaned_at=now,
        )], DuplicatePolicy.SKIP)
        self.repository.save_analysis(AnalysisResult(
            review_id=review_id, sentiment=Sentiment.POSITIVE, confidence=.9,
            analyzed_at=now, provider='fake', model='test',
        ))
        self.collector.load_reviews.return_value = [RawReview(
            source_review_id='external-1', review_text='updated review')]
        skipped = self.service.import_reviews(self.request)
        self.assertEqual((skipped.succeeded, skipped.skipped), (0, 1))
        self.assertEqual(self.repository.fetch_raw_reviews()[0].review_text, 'original review')
        self.assertIsNotNone(self.repository.get_review(review_id).analysis)

        updated = self.service.import_reviews(ImportRequest(self.request.file, DuplicatePolicy.UPSERT))
        self.assertEqual((updated.succeeded, updated.skipped), (1, 0))
        rows = self.repository.fetch_raw_reviews(status=ProcessingStatus.RAW)
        self.assertEqual([(r.id, r.review_text) for r in rows], [(review_id, 'updated review')])
        self.assertIsNone(self.repository.get_review(review_id))

    def test_row_storage_failure_keeps_successes_and_error_details(self):
        self.collector.load_reviews.return_value = [
            RawReview(source_review_id='good', review_text='valid storage value'),
            RawReview(source_review_id='bad', raw_payload={'unsupported': object()}),
        ]
        result = self.service.import_reviews(self.request)
        self.assertEqual((result.processed, result.succeeded, result.failed), (2, 1, 1))
        self.assertTrue(result.is_partial_failure)
        self.assertEqual(result.errors[0].item_ref, '2')
        self.assertEqual(result.errors[0].code, 'RAW_STORAGE_INVALID_ITEM')
        self.assertEqual([r.source_review_id for r in self.repository.fetch_raw_reviews()], ['good'])

    def test_file_failure_does_not_start_storage_write(self):
        repository = Mock()
        self.collector.load_reviews.side_effect = InputFileError('읽을 수 없는 파일')
        with self.assertRaises(InputFileError):
            ImportService(repository, self.collector).import_reviews(self.request)
        repository.save_raw_reviews.assert_not_called()

    def test_storage_failure_propagates(self):
        self.collector.load_reviews.return_value = [RawReview(review_text='review')]
        self.repository.close()
        with self.assertRaises(StorageError):
            self.service.import_reviews(self.request)


if __name__ == '__main__':
    unittest.main()
