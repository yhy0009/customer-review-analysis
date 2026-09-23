"""Dashboard orchestration, isolated output publication and real SQLite filters."""
import os
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from src.dashboard_service import DashboardService
from src.errors import OutputError, StorageError
from src.models import (
    AnalysisResult, CleanReview, DashboardRequest, DuplicatePolicy, OutputArtifact,
    OutputKind, RawReview, ReportFormat, ReviewFilter, ReviewStatistics, Sentiment,
)
from src.sqlite_repository import SQLiteReviewRepository
from tests.html_fixtures import DashboardHTML, PNG


class DashboardServiceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.output = self.root / 'nested/output.png'
        self.now = datetime(2026, 9, 22, 1, 2, 3, tzinfo=timezone.utc)
        self.stats = ReviewStatistics(0, 0, 0, 0)
        self.repository = Mock(get_statistics=Mock(return_value=self.stats))
        self.visualizer = Mock(generate_dashboard=Mock(side_effect=self.chart))
        self.reporter = Mock(generate_report=Mock(side_effect=self.report))
        self.service = DashboardService(self.repository, self.visualizer, self.reporter,
                                       font_family='TestFont', dpi=200, clock=lambda: self.now)
        self.chart_path = self.output / 'dashboard_20260922_010203.png'
        self.report_path = self.output / 'report_20260922_010203.md'

    @staticmethod
    def chart(statistics, output, **kwargs):
        output.write_bytes(b'chart')
        return [OutputArtifact(OutputKind.CHART, output, 'png')]

    @staticmethod
    def report(statistics, insight, output, *, report_format, **kwargs):
        output.write_text('report', encoding='utf-8')
        return OutputArtifact(OutputKind.REPORT, output, report_format.value)

    def run_dashboard(self, *, filters=None, fmt=ReportFormat.MARKDOWN, force=False, generate_html=False):
        return self.service.create_dashboard(DashboardRequest(
            filters or ReviewFilter(), self.output, fmt, force, generate_html=generate_html))

    def assert_no_stage(self):
        self.assertEqual(list(self.output.glob('.dashboard-*')), [])

    def test_one_statistics_result_options_and_real_absolute_artifacts(self):
        filters = ReviewFilter(product_name='이어폰', date_from=date(2026, 9, 1))
        result = self.run_dashboard(filters=filters)
        self.repository.get_statistics.assert_called_once_with(filters)
        self.repository.close.assert_not_called()
        self.assertIs(result.statistics, self.stats)
        self.assertIsNone(result.insight)
        self.assertEqual([a.path for a in result.artifacts], [self.chart_path, self.report_path])
        self.assertEqual([a.kind for a in result.artifacts], [OutputKind.CHART, OutputKind.REPORT])
        self.assertEqual(self.chart_path.read_bytes(), b'chart')
        self.assertEqual(self.report_path.read_text(), 'report')
        self.assertIs(self.visualizer.generate_dashboard.call_args.args[0], self.stats)
        self.assertEqual(self.visualizer.generate_dashboard.call_args.kwargs,
                         {'font_family': 'TestFont', 'dpi': 200, 'force': False})
        self.assertEqual(self.reporter.generate_report.call_args.args[:2], (self.stats, None))
        self.assert_no_stage()

    def test_txt_format_and_empty_statistics_are_successful(self):
        result = self.run_dashboard(fmt=ReportFormat.TXT)
        self.assertEqual(result.statistics.total_reviews, 0)
        self.assertEqual(result.artifacts[1].format, 'txt')
        self.assertTrue((self.output / 'report_20260922_010203.txt').is_file())

    def png_chart(self, statistics, output, **kwargs):
        output.write_bytes(PNG)
        return [OutputArtifact(OutputKind.CHART, output, 'png')]

    def test_html_uses_the_same_statistics_time_filter_and_embeds_staged_png(self):
        self.visualizer.generate_dashboard.side_effect = self.png_chart
        result = self.run_dashboard(generate_html=True, filters=ReviewFilter(product_name='휴대용 이어폰'))
        self.assertEqual([a.format for a in result.artifacts], ['png', 'md', 'html'])
        self.assertEqual(result.artifacts[2].path, self.output / 'dashboard_20260922_010203.html')
        text = result.artifacts[2].path.read_text(encoding='utf-8')
        self.assertIn('휴대용 이어폰', text)
        self.assertIn('2026-09-22T01:02:03Z', text)
        self.assertIn('[판정 보류]', text)
        self.assertEqual(len(DashboardHTML(text).images), 1)
        self.repository.get_statistics.assert_called_once()
        self.assert_no_stage()

    def test_existing_html_blocks_all_generation_without_force(self):
        self.output.mkdir(parents=True)
        target = self.output / 'dashboard_20260922_010203.html'
        target.write_text('existing HTML')
        with self.assertRaisesRegex(OutputError, '--force'):
            self.run_dashboard(generate_html=True)
        self.visualizer.generate_dashboard.assert_not_called()
        self.assertEqual(list(self.output.iterdir()), [target])
        self.assertEqual(target.read_text(), 'existing HTML')

    def test_html_failure_preserves_all_old_outputs_and_force_can_replace_them(self):
        self.visualizer.generate_dashboard.side_effect = self.png_chart
        result = self.run_dashboard(generate_html=True)
        for artifact in result.artifacts:
            artifact.path.write_bytes(b'previous version')
        with patch('src.html_dashboard.render_dashboard_html', side_effect=OutputError('HTML failed')):
            with self.assertRaises(OutputError):
                self.run_dashboard(generate_html=True, force=True)
        self.assertTrue(all(artifact.path.read_bytes() == b'previous version' for artifact in result.artifacts))
        self.assert_no_stage()
        result = self.run_dashboard(generate_html=True, force=True)
        self.assertEqual(result.artifacts[0].path.read_bytes(), PNG)
        self.assertIn('<!doctype html>', result.artifacts[2].path.read_text())
        self.assert_no_stage()

    def test_html_write_failure_does_not_publish_partial_files(self):
        self.visualizer.generate_dashboard.side_effect = self.png_chart
        original_open = Path.open
        def fail_html(path, *args, **kwargs):
            if path.suffix == '.html':
                raise PermissionError('simulated write failure')
            return original_open(path, *args, **kwargs)
        with patch.object(Path, 'open', fail_html), self.assertRaises(OutputError):
            self.run_dashboard(generate_html=True)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_html_generator_does_not_read_artifacts_outside_stage(self):
        outside = self.root / 'outside.png'
        outside.write_bytes(PNG)
        self.visualizer.generate_dashboard.side_effect = None
        self.visualizer.generate_dashboard.return_value = [OutputArtifact(OutputKind.CHART, outside, 'png')]
        with patch('src.html_dashboard.render_dashboard_html') as render:
            with self.assertRaises(OutputError):
                self.run_dashboard(generate_html=True)
        render.assert_not_called()
        self.assertEqual(outside.read_bytes(), PNG)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_either_existing_file_blocks_both_outputs_before_rendering(self):
        self.output.mkdir(parents=True)
        for target in (self.chart_path, self.report_path):
            with self.subTest(target=target):
                target.write_bytes(b'original')
                with self.assertRaisesRegex(OutputError, '--force'):
                    self.run_dashboard()
                self.assertEqual(target.read_bytes(), b'original')
                self.assertEqual(list(self.output.iterdir()), [target])
                target.unlink()
        self.visualizer.generate_dashboard.assert_not_called()
        self.reporter.generate_report.assert_not_called()

    def test_force_replaces_both_files_after_successful_rendering(self):
        self.run_dashboard()
        self.chart_path.write_bytes(b'old chart')
        self.report_path.write_text('old report')
        result = self.run_dashboard(force=True)
        self.assertEqual(len(result.artifacts), 2)
        self.assertEqual(self.chart_path.read_bytes(), b'chart')
        self.assertEqual(self.report_path.read_text(), 'report')
        self.assert_no_stage()

    def test_either_generator_failure_cleans_stage_and_preserves_existing_files(self):
        self.run_dashboard()
        for generator in (self.visualizer.generate_dashboard, self.reporter.generate_report):
            for error in (OutputError('render failed'), KeyboardInterrupt()):
                with self.subTest(error=type(error), generator=generator):
                    self.chart_path.write_bytes(b'old chart')
                    self.report_path.write_text('old report')
                    original = generator.side_effect
                    generator.side_effect = error
                    try:
                        with self.assertRaises(type(error)):
                            self.run_dashboard(force=True)
                    finally:
                        generator.side_effect = original
                    self.assertEqual(self.chart_path.read_bytes(), b'old chart')
                    self.assertEqual(self.report_path.read_text(), 'old report')
                    self.assert_no_stage()

    def test_failed_first_generation_leaves_no_partial_output(self):
        self.reporter.generate_report.side_effect = OutputError('report failed')
        with self.assertRaises(OutputError):
            self.run_dashboard()
        self.assertEqual(list(self.output.iterdir()), [])

    def test_output_file_and_unwritable_directory_become_output_errors(self):
        self.output.parent.mkdir()
        self.output.write_bytes(b'original')
        with self.assertRaises(OutputError):
            self.run_dashboard(force=True)
        self.assertEqual(self.output.read_bytes(), b'original')
        self.output.unlink()
        with patch('src.dashboard_service.tempfile.TemporaryDirectory', side_effect=PermissionError):
            with self.assertRaises(OutputError):
                self.run_dashboard()
        self.visualizer.generate_dashboard.assert_not_called()

    def test_force_rejects_target_symlinks_and_directories(self):
        self.output.mkdir(parents=True)
        outside = self.root / 'original'
        outside.write_text('keep')
        self.chart_path.symlink_to(outside)
        with self.assertRaises(OutputError):
            self.run_dashboard(force=True)
        self.assertEqual(outside.read_text(), 'keep')
        self.chart_path.unlink()
        self.chart_path.mkdir()
        with self.assertRaises(OutputError):
            self.run_dashboard(force=True)
        self.visualizer.generate_dashboard.assert_not_called()

    def test_concurrent_creation_is_not_overwritten_without_force(self):
        link = os.link
        def racing_link(source, target):
            target.write_bytes(b'concurrent output')
            return link(source, target)
        with patch('src.dashboard_service.os.link', side_effect=racing_link):
            with self.assertRaises(OutputError):
                self.run_dashboard()
        self.assertEqual(self.chart_path.read_bytes(), b'concurrent output')
        self.assertFalse(self.report_path.exists())
        self.assert_no_stage()

    def test_late_publication_failure_reports_already_saved_file(self):
        link = os.link
        def fail_report(source, target):
            if target.suffix == '.md':
                raise OSError('disk full')
            return link(source, target)
        with patch('src.dashboard_service.os.link', side_effect=fail_report):
            with self.assertRaisesRegex(OutputError, str(self.chart_path)):
                self.run_dashboard()
        self.assertTrue(self.chart_path.is_file())
        self.assertFalse(self.report_path.exists())
        self.assert_no_stage()

    def test_invalid_generator_artifact_does_not_publish_or_touch_external_file(self):
        outside = self.root / 'external.png'
        outside.write_bytes(b'keep')
        self.visualizer.generate_dashboard.side_effect = None
        self.visualizer.generate_dashboard.return_value = [OutputArtifact(OutputKind.CHART, outside, 'png')]
        with self.assertRaises(OutputError):
            self.run_dashboard()
        self.assertEqual(outside.read_bytes(), b'keep')
        self.assertEqual(list(self.output.iterdir()), [])

    def test_storage_error_prevents_output_and_propagates(self):
        self.repository.get_statistics.side_effect = StorageError('db failed')
        with self.assertRaises(StorageError):
            self.run_dashboard()
        self.assertFalse(self.output.exists())
        self.visualizer.generate_dashboard.assert_not_called()

    def test_real_repository_filters_include_unanalyzed_and_failed_without_mutations(self):
        with SQLiteReviewRepository(self.root / 'reviews.db') as repository:
            repository.save_raw_reviews([RawReview(source_review_id=str(i)) for i in range(5)],
                                        DuplicatePolicy.SKIP)
            rows = [('이어폰', 22), ('이어폰', 22), ('이어폰', 22), ('키보드', 22), ('이어폰', 21)]
            repository.save_clean_reviews([
                CleanReview(id=i, product_name=product, review_text='테스트 리뷰입니다', rating=4,
                            review_date=date(2026, 9, day), cleaned_at=self.now)
                for i, (product, day) in enumerate(rows, 1)], DuplicatePolicy.SKIP)
            repository.save_analysis(AnalysisResult(review_id=1, sentiment=Sentiment.POSITIVE,
                confidence=.9, analyzed_at=self.now, provider='fake', model='test', keywords=['배송']))
            repository.mark_analysis_failed(3, 'test failure')
            before = [repository.get_review(i) for i in range(1, 6)]
            self.service.repository = repository
            result = self.run_dashboard(filters=ReviewFilter(product_name='이어',
                date_from=date(2026, 9, 22), date_to=date(2026, 9, 22)))
            stats = result.statistics
            self.assertEqual((stats.total_reviews, stats.analyzed_reviews,
                              stats.unanalyzed_reviews, stats.failed_reviews), (3, 1, 1, 1))
            self.assertEqual(stats.sentiment_counts[Sentiment.POSITIVE], 1)
            self.assertEqual(stats.top_positive_keywords[0].keyword, '배송')
            self.assertEqual([repository.get_review(i) for i in range(1, 6)], before)


if __name__ == '__main__':
    unittest.main()
