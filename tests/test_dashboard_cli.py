"""Default CLI renders real PNG and reports from isolated SQLite without API keys."""
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from src.models import AnalysisResult, CleanReview, DuplicatePolicy, RawReview, Sentiment
from src.sqlite_repository import SQLiteReviewRepository


class DashboardCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / 'project'
        self.root.mkdir()
        source = Path(__file__).resolve().parents[1]
        shutil.copytree(source / 'src', self.root / 'src', ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copyfile(source / 'main.py', self.root / 'main.py')
        (self.root / 'config').mkdir()
        self.config = {
            'paths': {'output_dir': 'configured/output'},
            'visualization': {'font_family': '', 'dpi': 60},
            'logging': {'file': None, 'level': 'ERROR'},
        }
        (self.root / 'config/config.json').write_text(json.dumps(self.config), encoding='utf-8')
        self.database = self.root / 'data/app_database.db'
        self.other_cwd = self.root.parent / 'elsewhere'
        self.other_cwd.mkdir()
        self.environment = {key: value for key, value in os.environ.items()
                            if key in {'PATH', 'TMPDIR', 'LANG', 'LC_ALL', 'SYSTEMROOT', 'MPLCONFIGDIR'}}
        self.environment['PYTHONDONTWRITEBYTECODE'] = '1'
        self.environment.setdefault('MPLCONFIGDIR', str(self.root.parent / 'matplotlib'))

    def run_cli(self, *args, no_packages=False):
        return subprocess.run([sys.executable, *(['-S'] if no_packages else []),
                               str(self.root / 'main.py'), *args], cwd=self.other_cwd,
                              env=self.environment, text=True, capture_output=True, timeout=45)

    def seed(self):
        now = datetime.now(timezone.utc)
        rows = [('이어폰', 5, 22), ('이어폰', 1, 22), ('키보드', 2, 22), ('이어폰', 2, 21)]
        with SQLiteReviewRepository(self.database) as repository:
            repository.save_raw_reviews([RawReview(source_review_id=str(i)) for i in range(4)],
                                        DuplicatePolicy.SKIP)
            repository.save_clean_reviews([
                CleanReview(id=i, product_name=product, rating=rating, review_date=date(2026, 9, day),
                            review_text='합성 테스트 리뷰', cleaned_at=now)
                for i, (product, rating, day) in enumerate(rows, 1)], DuplicatePolicy.SKIP)
            for i, sentiment, keyword in ((1, Sentiment.POSITIVE, '음질'),
                                         (2, Sentiment.NEGATIVE, '배송 지연')):
                repository.save_analysis(AnalysisResult(review_id=i, sentiment=sentiment,
                    confidence=.9, analyzed_at=now, provider='fake', model='test', keywords=[keyword]))

    def assert_artifacts(self, result, directory, report_format):
        self.assertEqual(result.returncode, 0, result.stderr)
        charts = list(directory.glob('dashboard_*.png'))
        reports = list(directory.glob(f'report_*.{report_format}'))
        self.assertEqual(len(charts), 1)
        self.assertEqual(len(reports), 1)
        for file in (*charts, *reports):
            self.assertIn(str(file), result.stdout)
        self.assertEqual(charts[0].stem.removeprefix('dashboard_'), reports[0].stem.removeprefix('report_'))
        png = charts[0].read_bytes()
        self.assertEqual(png[:8], b'\x89PNG\r\n\x1a\n')
        width, height = struct.unpack('>II', png[16:24])
        self.assertGreater(width, 400)
        self.assertGreater(height, 300)
        self.assertEqual(list(directory.glob('.dashboard-*')), [])
        self.assertFalse((self.other_cwd / 'output').exists())
        self.assertFalse((self.other_cwd / 'data').exists())
        return reports[0].read_text(encoding='utf-8')

    def test_default_markdown_and_configured_directory_without_api_key(self):
        self.seed()
        text = self.assert_artifacts(self.run_cli('dashboard'), self.root / 'configured/output', 'md')
        for expected in ('| 정제 리뷰 | 4건 |', '| 분석 완료 | 2건 |',
                         '| 미분석 (실패 제외) | 2건 |', '음질', '배송 지연', 'AI 인사이트가 제공되지 않았습니다'):
            self.assertIn(expected, text)

    def test_product_and_inclusive_date_filters_with_txt_and_output_override(self):
        self.seed()
        result = self.run_cli('dashboard', '--product', '이어', '--date-from', '2026-09-22',
                              '--date-to', '2026-09-22', '--output', 'custom/charts.png',
                              '--report-format', 'txt')
        text = self.assert_artifacts(result, self.root / 'custom/charts.png', 'txt')
        for expected in ('정제 리뷰 / 2건', '평균 별점 / 3.00/5',
                         '긍정 / 1건 / 50.0%', '부정 / 1건 / 50.0%'):
            self.assertIn(expected, text)
        self.assertFalse((self.root / 'configured').exists())

    def test_empty_database_generates_empty_chart_and_report(self):
        text = self.assert_artifacts(self.run_cli('dashboard'), self.root / 'configured/output', 'md')
        self.assertIn('통계 대상 정제 리뷰가 없습니다', text)
        self.assertIn('| 평균 별점 | N/A |', text)

    def test_default_alert_warns_from_real_sqlite_without_changing_success_exit_code(self):
        now = datetime.now(timezone.utc)
        # Same product, two complete periods: 1/5 -> 4/5 negative. A different
        # product has many positive reviews and must not dilute this comparison.
        rows = [(9, '이어폰', Sentiment.NEGATIVE if i == 0 else Sentiment.POSITIVE) for i in range(5)]
        rows += [(22, '이어폰', Sentiment.NEGATIVE if i < 4 else Sentiment.POSITIVE) for i in range(5)]
        rows += [(22, '키보드', Sentiment.POSITIVE) for _ in range(20)]
        with SQLiteReviewRepository(self.database) as repository:
            repository.save_raw_reviews([RawReview(source_review_id=str(i)) for i in range(len(rows))],
                                        DuplicatePolicy.SKIP)
            repository.save_clean_reviews([
                CleanReview(id=i, product_name=product, rating=3, review_date=date(2026, 9, day),
                            review_text='감정 변화 검증용 합성 리뷰', cleaned_at=now)
                for i, (day, product, _) in enumerate(rows, 1)], DuplicatePolicy.SKIP)
            for i, (_, _, sentiment) in enumerate(rows, 1):
                repository.save_analysis(AnalysisResult(review_id=i, sentiment=sentiment,
                    confidence=.9, analyzed_at=now, provider='fake', model='test'))
        result = self.run_cli('dashboard', '--date-to', '2026-09-22', '--product', '이어폰')
        self.assert_artifacts(result, self.root / 'configured/output', 'md')
        self.assertIn('[경고] 부정 리뷰 비율 급증', result.stdout)
        self.assertIn('20.0% → 80.0% (+60.0%p', result.stdout)
        self.assertIn('2026-09-09 ~ 2026-09-15', result.stdout)
        self.assertIn('2026-09-16 ~ 2026-09-22', result.stdout)
        self.assertNotIn('[경고]', result.stderr)

    def test_partial_comparison_period_is_withheld_and_alerts_can_be_disabled(self):
        self.seed()
        result = self.run_cli('dashboard', '--date-from', '2026-09-22', '--date-to', '2026-09-22')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('[판정 보류] 날짜 필터', result.stdout)
        self.assertNotIn('[경고]', result.stdout)
        result = self.run_cli('dashboard', '--output', 'disabled', '--no-alerts')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('감정 변화', result.stdout)
        self.assertNotIn('판정 보류', result.stdout)

    def test_bad_alert_settings_are_rejected_before_database_creation(self):
        for options in (('--alert-days', '0'), ('--alert-days', '3651'),
                        ('--alert-threshold', 'nan'), ('--alert-threshold', 'inf'),
                        ('--alert-threshold', '0'), ('--alert-threshold', '101'),
                        ('--alert-min-reviews', '0'), ('--date-to', '0001-01-01')):
            with self.subTest(options=options):
                result = self.run_cli('dashboard', *options, no_packages=True)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertNotIn('Traceback', result.stderr)
                self.assertFalse(result.stdout)
                self.assertFalse(self.database.exists())

    def test_output_file_is_error_without_success_output_or_damage(self):
        target = self.root / 'blocked'
        target.write_bytes(b'keep existing contents')
        result = self.run_cli('dashboard', '--output', 'blocked', '--force')
        self.assertEqual(result.returncode, 3)
        self.assertFalse(result.stdout)
        self.assertNotIn('Traceback', result.stderr)
        self.assertEqual(target.read_bytes(), b'keep existing contents')

    def test_help_and_invalid_requests_work_without_dependencies_or_db(self):
        result = self.run_cli('dashboard', '--help', no_packages=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        for option in ('--date-from', '--date-to', '--product', '--output', '--report-format', '--force'):
            self.assertIn(option, result.stdout)
        for options in (('--date-from', '2026-09-23', '--date-to', '2026-09-22'),
                        ('--report-format', 'html')):
            result = self.run_cli('dashboard', *options, no_packages=True)
            self.assertEqual(result.returncode, 2)
            self.assertNotIn('Traceback', result.stderr)
        self.assertFalse(self.database.exists())
        self.assertFalse((self.root / 'configured').exists())


if __name__ == '__main__':
    unittest.main()
