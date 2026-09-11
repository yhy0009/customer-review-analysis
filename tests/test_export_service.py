"""All-row export filtering and consistent reads against a real SQLite store."""
import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import Mock

from src.cli import parse_args
from src.errors import OutputError, StorageError
from src.export_service import ExportService
from src.handlers import build_export_handler
from src.models import (
    AnalysisResult, CleanReview, DuplicatePolicy, ExportFormat, ExportRequest,
    ExportResult, OutputArtifact, OutputKind, RawReview, ReviewFilter, Sentiment,
)
from src.sqlite_repository import SQLiteReviewRepository


class ExportServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'reviews.db'
        self.repo = SQLiteReviewRepository(self.path)
        self.addCleanup(self.repo.close)
        self.now = datetime.now(timezone.utc)
        self.exporter = Mock()
        self.exporter.export_reviews.side_effect = lambda rows, output, **kw: ExportResult(
            OutputArtifact(OutputKind.EXPORT, output, kw['export_format'].value), len(rows))
        self.request = ExportRequest(ReviewFilter(), self.root / 'reviews.jsonl', ExportFormat.JSONL)

    def seed(self, count):
        self.repo.save_raw_reviews([RawReview(source_review_id=str(i)) for i in range(1, count + 1)], DuplicatePolicy.SKIP)
        self.repo.save_clean_reviews([CleanReview(id=i, product_name='이어폰' if i % 2 else '키보드',
                                     rating=4 if i % 2 else 2, review_text=f'리뷰 {i}',
                                     review_date=date(2026, 9, 11), cleaned_at=self.now)
                                     for i in range(1, count + 1)], DuplicatePolicy.SKIP)

    def test_export_reads_every_page_in_id_order(self):
        self.seed(1003)
        result = ExportService(self.repo, self.exporter).export_reviews(self.request)
        self.assertEqual(result.row_count, 1003)
        rows = self.exporter.export_reviews.call_args.args[0]
        self.assertEqual([r.review.id for r in rows], list(range(1, 1004)))
        self.assertTrue(all(r.analysis is None for r in rows))

    def test_combined_filters_include_analysis_and_forward_output_options(self):
        self.seed(5)
        for i in (1, 2, 3):
            self.repo.save_analysis(AnalysisResult(review_id=i, sentiment=Sentiment.NEGATIVE, confidence=.9,
                                   analyzed_at=self.now, provider='fake', model='test'))
        filters = ReviewFilter(sentiment=Sentiment.NEGATIVE, product_name='이어', rating_min=3,
                               date_from=date(2026, 9, 11), date_to=date(2026, 9, 11))
        request = ExportRequest(filters, self.root / 'out.csv', ExportFormat.CSV, force=True)
        result = ExportService(self.repo, self.exporter).export_reviews(request)
        self.assertEqual(result.row_count, 2)
        call = self.exporter.export_reviews.call_args
        self.assertEqual([r.review.id for r in call.args[0]], [1, 3])
        self.assertTrue(all(r.analysis.sentiment is Sentiment.NEGATIVE for r in call.args[0]))
        self.assertEqual(call.args[1], request.output)
        self.assertEqual(call.kwargs, {'export_format': ExportFormat.CSV, 'force': True})

    def test_empty_export_is_delegated_and_query_error_does_not_write(self):
        result = ExportService(self.repo, self.exporter).export_reviews(self.request)
        self.assertEqual(result.row_count, 0)
        self.exporter.reset_mock()
        self.repo.close()
        with self.assertRaises(StorageError):
            ExportService(self.repo, self.exporter).export_reviews(self.request)
        self.exporter.export_reviews.assert_not_called()

    def test_read_snapshot_keeps_pages_consistent_during_another_writer(self):
        self.seed(1003)
        with sqlite3.connect(self.path) as connection:
            connection.execute('PRAGMA journal_mode=WAL')
        with SQLiteReviewRepository(self.path) as writer:
            repo = self.repo
            class InterleavingReader:
                def list_reviews(inner, query):
                    page = repo.list_reviews(query)
                    if query.page == 1:
                        writer.save_raw_reviews([RawReview(source_review_id='1003', review_text='changed')], DuplicatePolicy.UPSERT)
                    return page
            with self.repo.read_snapshot():
                result = ExportService(InterleavingReader(), self.exporter).export_reviews(self.request)
            self.assertEqual(result.row_count, 1003)
            self.assertEqual(self.exporter.export_reviews.call_args.args[0][-1].review.id, 1003)
            self.assertIsNone(self.repo.get_review(1003))

    def test_snapshot_is_released_after_export_failure(self):
        self.exporter.export_reviews.side_effect = OutputError('출력 실패')
        with self.assertRaises(OutputError):
            with self.repo.read_snapshot():
                ExportService(self.repo, self.exporter).export_reviews(self.request)
        self.assertEqual(self.repo.save_raw_reviews([RawReview(source_review_id='new')], DuplicatePolicy.SKIP).succeeded, 1)

    def test_handler_prints_written_count_and_path_and_maps_output_error(self):
        args = parse_args(['export', '--format', 'jsonl', '--output', str(self.request.output)])
        method = Mock(return_value=ExportResult(OutputArtifact(OutputKind.EXPORT, self.request.output, 'jsonl'), 7))
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(build_export_handler(method)(args), 0)
        self.assertIn('7건', stdout.getvalue())
        self.assertIn(str(self.request.output), stdout.getvalue())
        self.assertFalse(stderr.getvalue())
        method.side_effect = OutputError('이미 존재하는 파일')
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(build_export_handler(method)(args), 3)
            self.assertEqual(out.getvalue(), '')


if __name__ == '__main__':
    unittest.main()
