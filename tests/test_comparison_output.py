import csv
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.comparison import ComparisonGroup, ComparisonRequest, ComparisonResult, ComparisonService
from src.comparison_output import format_comparison, write_comparison
from src.errors import OutputError
from src.models import ReviewStatistics
from src.sqlite_repository import SQLiteReviewRepository
from tests.comparison_fixtures import NOW, seed_comparison


class ComparisonOutputTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = SQLiteReviewRepository(self.root / 'reviews.db')
        self.addCleanup(self.repo.close)
        seed_comparison(self.repo)
        self.request = ComparisonRequest(group_by='category', output=self.root / 'output')
        self.result = ComparisonService(self.repo).compare(self.request)
        self.result.generated_at = NOW

    def test_console_explains_missing_analysis_and_small_samples(self):
        text = format_comparison(self.result, self.request)
        for expected in ('3개 그룹', '정제 리뷰 7건', '33.3%', '2.80', 'N/A', '[카테고리 없음]', '부족', '미분석', '실패'):
            self.assertIn(expected, text)
        self.assertIn('분석 완료 리뷰 기준', text)

    def test_csv_json_match_and_null_is_not_zero(self):
        files = write_comparison(self.result, self.request)
        csv_path, json_path = files
        document = json.loads(json_path.read_text())
        rows = document['groups']
        electronics = next(row for row in rows if row['name'] == '전자')
        self.assertEqual(electronics['average_rating'], 2.8)
        self.assertAlmostEqual(electronics['negative_ratio'], 1/3)
        self.assertEqual(electronics['analysis_completion_ratio'], .6)
        unknown = next(row for row in rows if row['name'] is None)
        self.assertIsNone(unknown['negative_ratio'])
        self.assertTrue(unknown['uncategorized'])
        with csv_path.open(encoding='utf-8-sig', newline='') as file:
            csv_rows = list(csv.DictReader(file))
        self.assertEqual(len(csv_rows), 3)
        self.assertEqual(next(row for row in csv_rows if row['uncategorized'] == 'True')['negative_ratio'], '')

    def test_output_formula_escaping_and_console_control_characters(self):
        group = ComparisonGroup('=1+1\x1b', 1, ReviewStatistics(0, 0, 0, 0))
        result = ComparisonResult([group], generated_at=NOW)
        csv_path, json_path = write_comparison(result, self.request)
        with csv_path.open(encoding='utf-8-sig', newline='') as file:
            self.assertEqual(next(csv.DictReader(file))['name'], "'=1+1\x1b")
        self.assertEqual(json.loads(json_path.read_text())['groups'][0]['name'], '=1+1\x1b')
        self.assertNotIn('\x1b', format_comparison(result, self.request))

    def test_empty_and_unmatched_results_remain_shareable(self):
        result = ComparisonResult([], ('없는 제품',), NOW)
        text = format_comparison(result, self.request)
        self.assertIn('조건에 맞는 정제 리뷰가 없습니다', text)
        self.assertIn('현재 조건에 없는 이름: 없는 제품', text)
        files = write_comparison(result, self.request)
        self.assertEqual(json.loads(files[1].read_text())['groups'], [])

    def test_existing_outputs_require_force_and_force_replaces(self):
        files = write_comparison(self.result, self.request)
        originals = {path: path.read_bytes() for path in files}
        with self.assertRaisesRegex(OutputError, '--force'):
            write_comparison(self.result, self.request)
        self.assertEqual({path: path.read_bytes() for path in files}, originals)
        self.request.force = True
        self.result.groups = []
        write_comparison(self.result, self.request)
        self.assertEqual(json.loads(files[1].read_text())['groups'], [])
        self.assertFalse(list(self.request.output.glob('.comparison-*')))

    def test_chart_failure_preserves_preexisting_tables_and_cleans_stage(self):
        files = write_comparison(self.result, self.request)
        originals = {path: path.read_bytes() for path in files}
        self.request.force = self.request.chart = True
        with patch('src.comparison_charts.render_comparison_charts', side_effect=RuntimeError('failure')):
            with self.assertRaises(OutputError):
                write_comparison(self.result, self.request)
        self.assertEqual({path: path.read_bytes() for path in files}, originals)
        self.assertFalse(list(self.request.output.glob('.comparison-*')))

    def test_symlinks_and_database_hardlinks_are_not_overwritten(self):
        self.request.output.mkdir()
        target = self.request.output / 'comparison_category_20260922_010203.csv'
        target.symlink_to(self.repo.database_path)
        self.request.force = True
        with self.assertRaises(OutputError):
            write_comparison(self.result, self.request)
        target.unlink()
        os.link(self.repo.database_path, target)
        with self.assertRaisesRegex(OutputError, 'SQLite'):
            write_comparison(self.result, self.request, protected_paths=(self.repo.database_path,))
        self.assertEqual(self.repo.get_statistics().total_reviews, 7)

    def test_publish_collision_and_partial_publish_report_paths(self):
        real_link = os.link
        def concurrent_file(source, target):
            Path(target).write_text('concurrent writer')
            real_link(source, target)
        with patch('src.comparison_output.os.link', side_effect=concurrent_file):
            with self.assertRaises(OutputError):
                write_comparison(self.result, self.request)
        target = self.request.output / 'comparison_category_20260922_010203.csv'
        self.assertEqual(target.read_text(), 'concurrent writer')
        target.unlink()
        calls = 0
        def second_fails(source, destination):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('disk failure')
            real_link(source, destination)
        with patch('src.comparison_output.os.link', side_effect=second_fails):
            with self.assertRaisesRegex(OutputError, '이미 저장된 파일:'):
                write_comparison(self.result, self.request)
        self.assertTrue(target.is_file())
        self.assertFalse(list(self.request.output.glob('.comparison-*')))

    def test_chart_pagination_renders_all_groups_and_handles_empty(self):
        from src.comparison_charts import render_comparison_charts
        from matplotlib import pyplot as plt
        groups = [ComparisonGroup(f'제품 {index}', 1, self.result.groups[0].statistics) for index in range(21)]
        result = ComparisonResult(groups, generated_at=NOW)
        files = render_comparison_charts(result, self.request, self.root, 'pages', font_family='', dpi=70)
        self.assertEqual(len(files), 2)
        files += render_comparison_charts(ComparisonResult([]), self.request, self.root, 'empty', font_family='', dpi=70)
        for path in files:
            self.assertTrue(path.read_bytes().startswith(b'\x89PNG\r\n\x1a\n'))
        self.assertFalse(plt.get_fignums())

    def test_chart_accepts_long_names_and_math_like_source_text(self):
        from src.comparison_charts import render_comparison_charts
        group = ComparisonGroup('$invalid_{' + '가' * 200, 1, self.result.groups[0].statistics)
        files = render_comparison_charts(ComparisonResult([group]), self.request, self.root, 'long', font_family='', dpi=70)
        self.assertTrue(files[0].read_bytes().startswith(b'\x89PNG'))
