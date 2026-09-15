"""Verify pipeline request adapters, user output and common exit codes offline."""
import contextlib
import io
import tempfile
import unittest
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from src.cli import parse_args
from src.config import DEFAULT_CONFIG
from src.errors import AIProviderError, ConfigError, InputFileError, OutputError, StorageError
from src.handlers import (
    build_clean_handler, build_dashboard_handler, build_handlers, build_import_handler,
)
from src.models import (
    BatchOperationResult, CleanBatchResult, CleanReview, DashboardResult, DuplicatePolicy,
    ItemError, OutputArtifact, OutputKind, ReportFormat, ReviewStatistics,
)
from src.query_output import format_batch_result, format_dashboard_result


class PipelineHandlerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.config = deepcopy(DEFAULT_CONFIG)
        self.patch_root = patch('src.handlers.PROJECT_ROOT', self.root)
        self.patch_root.start()
        self.addCleanup(self.patch_root.stop)

    def invoke(self, builder, method, argv):
        args = parse_args(argv)
        args.app_config = self.config
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = builder(method)(args)
        return code, out.getvalue(), err.getvalue()

    def test_import_forwards_absolute_path_and_policy_and_reports_duplicates(self):
        method = Mock(return_value=BatchOperationResult(processed=3, succeeded=1, skipped=2))
        code, out, err = self.invoke(build_import_handler, method,
                                    ['import', '--file', 'data/reviews.csv', '--policy', 'upsert'])
        self.assertEqual(code, 0)
        self.assertFalse(err)
        request = method.call_args.args[0]
        self.assertEqual(request.file, self.root / 'data/reviews.csv')
        self.assertIs(request.policy, DuplicatePolicy.UPSERT)
        for value in ('processed=3', 'succeeded=1', 'skipped=2', 'failed=0', 'rejected=0'):
            self.assertIn(value, out)

    def test_import_partial_and_total_failures_show_counts_and_return_one(self):
        for succeeded in (0, 1):
            with self.subTest(succeeded=succeeded):
                result = BatchOperationResult(processed=succeeded + 1, succeeded=succeeded, failed=1,
                    errors=[ItemError('2', 'INVALID_RAW', '원본 값을 확인하세요.')])
                code, out, err = self.invoke(build_import_handler, Mock(return_value=result),
                                            ['import', '--file', 'reviews.csv'])
                self.assertEqual(code, 1)
                self.assertIn('failed=1', out)
                self.assertIn('INVALID_RAW', out)
                self.assertFalse(err)

    def test_empty_import_and_clean_have_zero_counts_and_succeed(self):
        for builder, argv, result in (
            (build_import_handler, ['import', '--file', 'reviews.csv'],
             BatchOperationResult(processed=0, succeeded=0)),
            (build_clean_handler, ['clean'], CleanBatchResult(processed=0, succeeded=0)),
        ):
            with self.subTest(command=argv[0]):
                code, out, err = self.invoke(builder, Mock(return_value=result), argv)
                self.assertEqual(code, 0)
                self.assertIn('processed=0', out)
                self.assertIn('rejected=0', out)
                self.assertFalse(err)

    def test_clean_uses_config_defaults_and_cli_overrides(self):
        self.config['cleaning'] = {'duplicate_policy': 'upsert', 'min_review_length': 7}
        for argv, policy, length in (
            (['clean'], DuplicatePolicy.UPSERT, 7),
            (['clean', '--policy', 'skip', '--min-length', '3'], DuplicatePolicy.SKIP, 3),
        ):
            method = Mock(return_value=CleanBatchResult(processed=0, succeeded=0))
            code, _, _ = self.invoke(build_clean_handler, method, argv)
            self.assertEqual(code, 0)
            self.assertEqual(method.call_args.args[0].options.policy, policy)
            self.assertEqual(method.call_args.args[0].options.min_length, length)

    def test_clean_rejections_are_reported_without_printing_successful_review_bodies(self):
        review = CleanReview(id=1, product_name='제품', review_date=date(2026, 9, 1), rating=5,
                             review_text='PRIVATE_REVIEW_BODY', cleaned_at=datetime.now(timezone.utc))
        result = CleanBatchResult(processed=2, succeeded=1, rejected=1, reviews=[review],
                                  errors=[ItemError('2', 'INVALID_RATING', '별점을 확인하세요.')])
        code, out, err = self.invoke(build_clean_handler, Mock(return_value=result), ['clean'])
        self.assertEqual(code, 1)
        self.assertIn('rejected=1', out)
        self.assertIn('INVALID_RATING', out)
        self.assertNotIn(review.review_text, out + err)

    def test_dashboard_forwards_filters_options_and_prints_every_artifact(self):
        artifacts = [OutputArtifact(OutputKind.CHART, self.root / '차트.png', 'png'),
                     OutputArtifact(OutputKind.REPORT, self.root / '보고서.txt', 'txt')]
        result = DashboardResult(artifacts, ReviewStatistics(0, 0, 0, 0))
        method = Mock(return_value=result)
        code, out, err = self.invoke(build_dashboard_handler, method,
            ['dashboard', '--product', '이어폰', '--date-from', '2026-09-01',
             '--date-to', '2026-09-15', '--output', 'charts', '--report-format', 'txt', '--force'])
        request = method.call_args.args[0]
        self.assertEqual(code, 0)
        self.assertFalse(err)
        self.assertEqual(request.output, self.root / 'charts')
        self.assertEqual(request.filters.product_name, '이어폰')
        self.assertEqual(request.filters.date_from, date(2026, 9, 1))
        self.assertEqual(request.filters.date_to, date(2026, 9, 15))
        self.assertIs(request.report_format, ReportFormat.TXT)
        self.assertTrue(request.force)
        for artifact in artifacts:
            self.assertIn(str(artifact.path), out)
            self.assertFalse(artifact.path.exists())  # Presentation performs no file I/O.

    def test_empty_dashboard_does_not_claim_files_were_created(self):
        result = DashboardResult([], ReviewStatistics(0, 0, 0, 0))
        code, out, err = self.invoke(build_dashboard_handler, Mock(return_value=result), ['dashboard'])
        self.assertEqual(code, 0)
        self.assertIn('생성된 파일이 없습니다', out)
        self.assertFalse(err)

    def test_invalid_request_fails_before_service_call(self):
        self.config['cleaning']['min_review_length'] = 0
        for builder, argv in (
            (build_clean_handler, ['clean']),
            (build_dashboard_handler, ['dashboard', '--date-from', '2026-09-15', '--date-to', '2026-09-01']),
        ):
            method = Mock()
            code, out, err = self.invoke(builder, method, argv)
            self.assertEqual(code, 2)
            method.assert_not_called()
            self.assertFalse(out)
            self.assertTrue(err)

    def test_service_errors_keep_common_exit_codes_without_success_output(self):
        for builder, argv in ((build_import_handler, ['import', '--file', 'reviews.csv']),
                              (build_clean_handler, ['clean']), (build_dashboard_handler, ['dashboard'])):
            for exception, expected in ((ConfigError, 2), (InputFileError, 3), (StorageError, 3),
                                        (OutputError, 3), (AIProviderError, 4)):
                with self.subTest(command=argv[0], error=exception.__name__):
                    code, out, err = self.invoke(builder, Mock(side_effect=exception('처리 오류')), argv)
                    self.assertEqual(code, expected)
                    self.assertFalse(out)
                    self.assertIn('처리 오류', err)

    def test_full_service_map_uses_the_output_adapters(self):
        services = Mock()
        services.import_reviews.return_value = BatchOperationResult(processed=0, succeeded=0)
        services.clean_reviews.return_value = CleanBatchResult(processed=0, succeeded=0)
        services.create_dashboard.return_value = DashboardResult([], ReviewStatistics(0, 0, 0, 0))
        handlers = build_handlers(services)
        for argv in (['import', '--file', 'reviews.csv'], ['clean'], ['dashboard']):
            args = parse_args(argv)
            args.app_config = self.config
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(handlers[argv[0]](args), 0)
            self.assertTrue(out.getvalue().strip())

    def test_external_error_fields_cannot_inject_terminal_commands_or_extra_lines(self):
        result = BatchOperationResult(processed=1, succeeded=0, failed=1, errors=[
            ItemError('\x1b[31m42\x1b[0m', 'BAD\nCODE', '확인\n하세요\x1b]8;;https://invalid\x07링크\x1b]8;;\x07')])
        out = format_batch_result(result)
        self.assertNotIn('\x1b', out)
        self.assertNotIn('https://invalid', out)
        self.assertEqual(len(out.splitlines()), 2)
        self.assertIn('BAD CODE', out)
        self.assertIn('item=42', out)

    def test_artifact_paths_preserve_unicode_and_remove_terminal_controls(self):
        result = DashboardResult([OutputArtifact(OutputKind.CHART, self.root / '한글\x1b[31m.png', 'png')],
                                 ReviewStatistics(0, 0, 0, 0))
        out = format_dashboard_result(result)
        self.assertNotIn('\x1b', out)
        self.assertIn('한글.png', out)


if __name__ == '__main__':
    unittest.main()
