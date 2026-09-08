"""Exercise file input through storage, cleaning, analysis persistence and CLI errors."""
import contextlib
import io
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

from openpyxl import Workbook
from src.cli import parse_args
from src.collector import load_reviews
from src.cleaner import clean_reviews
from src.config import DEFAULT_CONFIG
from src.errors import InputFileError
from src.handlers import build_handlers
from src.models import AnalysisResult, CleaningOptions, DuplicatePolicy, ProcessingStatus, RawReview, Sentiment
from src.sqlite_repository import SQLiteReviewRepository


class PipelineIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.repo = SQLiteReviewRepository(self.directory / 'reviews.db')
        self.addCleanup(self.repo.close)
        self.options = CleaningOptions(policy=DuplicatePolicy.SKIP, min_length=3)

    def test_csv_to_analysis_preserves_ids_after_a_rejected_review(self):
        path = self.directory / 'reviews.csv'
        path.write_text('review_id,product_name,review_date,rating,review_text\n'
                        'bad,제품,invalid,5,좋아요\n'
                        'good,제품,2026-09-08,5,정말 좋아요 😀\n', encoding='utf-8-sig')
        loaded = load_reviews(path)
        self.assertTrue(all(r.id is None for r in loaded))
        self.assertEqual(self.repo.save_raw_reviews(loaded, DuplicatePolicy.SKIP).succeeded, 2)
        raw = self.repo.fetch_raw_reviews(status=ProcessingStatus.RAW)
        with self.assertLogs('customer_review_analysis.cleaner', level='WARNING'):
            batch = clean_reviews(raw, self.options)
        self.assertEqual((batch.succeeded, batch.rejected, batch.failed), (1, 1, 0))
        self.assertEqual(batch.reviews[0].id, raw[1].id)
        self.repo.save_clean_reviews(batch.reviews, DuplicatePolicy.SKIP)
        review = batch.reviews[0]
        self.repo.save_analysis(AnalysisResult(review_id=review.id, sentiment=Sentiment.POSITIVE,
                               confidence=.9, analyzed_at=datetime.now(timezone.utc), provider='test', model='test'))
        self.assertIsNone(self.repo.get_review(raw[0].id))
        self.assertEqual(self.repo.get_review(raw[1].id).analysis.sentiment, Sentiment.POSITIVE)
        self.assertEqual(self.repo.get_statistics().analyzed_reviews, 1)

    def test_cleaner_does_not_restart_id_for_new_batch(self):
        for source in ['first', 'second']:
            raw = RawReview(source_review_id=source, product_name='제품', review_text='좋아요', rating=5, review_date='2026-09-08')
            self.repo.save_raw_reviews([raw], DuplicatePolicy.SKIP)
            batch = clean_reviews(self.repo.fetch_raw_reviews(status=ProcessingStatus.RAW), self.options)
            self.assertEqual(batch.succeeded, 1)
            self.repo.save_clean_reviews(batch.reviews, DuplicatePolicy.SKIP)
        self.assertEqual([r.id for r in self.repo.fetch_clean_reviews()], [1, 2])
        self.assertEqual(batch.reviews[0].id, 2)

    def test_cleaner_refuses_unstored_input_without_leaking_source_id(self):
        with self.assertLogs('customer_review_analysis.cleaner', level='ERROR') as logs:
            result = clean_reviews([RawReview(source_review_id='SECRET')], self.options)
        self.assertEqual(result.failed, 1)
        self.assertEqual(result.errors[0].item_ref, 'row:1')
        self.assertNotIn('SECRET', str(logs.output) + str(result.errors))

    def test_excel_dates_and_numbers_reach_storage_and_cleaner(self):
        path = self.directory / 'reviews.xlsx'
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(['review_id', 'product_name', 'review_date', 'rating', 'review_text', 'extra'])
        sheet.append([10, '제품', datetime(2026, 9, 8), 5, '좋아요', 123])
        workbook.save(path)
        workbook.close()
        result = self.repo.save_raw_reviews(load_reviews(path), DuplicatePolicy.SKIP)
        self.assertEqual((result.succeeded, result.failed), (1, 0))
        raw = self.repo.fetch_raw_reviews()
        self.assertEqual(raw[0].raw_payload['extra'], 123)
        batch = clean_reviews(raw, self.options)
        self.assertEqual(batch.succeeded, 1)
        self.assertEqual(batch.reviews[0].rating, 5)

    def test_bad_files_raise_common_exception_and_cli_exit_three(self):
        paths = [self.directory / 'missing.csv', self.directory / 'bad.txt', self.directory / 'empty.csv']
        paths[1].write_text('unsupported')
        paths[2].write_text('review_text\n')
        for path in paths:
            with self.subTest(path=path), self.assertRaises(InputFileError):
                load_reviews(path)
            services = Mock()
            services.import_reviews.side_effect = lambda request: load_reviews(request.file)
            args = parse_args(['import', '--file', str(path)])
            args.app_config = DEFAULT_CONFIG
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(build_handlers(services)['import'](args), 3)


if __name__ == '__main__':
    unittest.main()
