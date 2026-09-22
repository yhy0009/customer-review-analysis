import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from src.sqlite_repository import SQLiteReviewRepository
from tests.comparison_fixtures import seed_comparison


class ComparisonCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / 'project'
        self.root.mkdir()
        source = Path(__file__).resolve().parents[1]
        shutil.copytree(source / 'src', self.root / 'src', ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copyfile(source / 'main.py', self.root / 'main.py')
        (self.root / 'config').mkdir()
        (self.root / 'config/config.json').write_text(json.dumps({'logging': {'file': None, 'level': 'ERROR'}}))
        self.database = self.root / 'data/app_database.db'
        self.environment = {key: value for key, value in os.environ.items() if key in {'PATH', 'TMPDIR', 'LANG', 'LC_ALL', 'SYSTEMROOT', 'MPLCONFIGDIR'}}
        self.environment['PYTHONDONTWRITEBYTECODE'] = '1'

    def run_cli(self, *args, packages=False):
        return subprocess.run([sys.executable, *([] if packages else ['-S']), str(self.root / 'main.py'), *args],
                              cwd=self.root.parent, env=self.environment, text=True, capture_output=True, timeout=30)

    def seed(self):
        with SQLiteReviewRepository(self.database) as repository:
            seed_comparison(repository)

    def test_comparison_and_exports_work_without_ai_or_optional_packages(self):
        self.seed()
        result = self.run_cli('compare', '--group-by', 'category', '--output', 'output/category')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('정제 리뷰 7건', result.stdout)
        self.assertIn('33.3%', result.stdout)
        files = list((self.root / 'output/category').iterdir())
        self.assertEqual({file.suffix for file in files}, {'.csv', '.json'})
        self.assertFalse((self.root.parent / 'output').exists())

    def test_exact_product_selection_date_and_sort_options(self):
        self.seed()
        result = self.run_cli('compare', '--name', '이어폰 A', '--name', '이어폰 B', '--category', '전자',
                              '--date-from', '2026-09-02', '--date-to', '2026-09-02', '--sort', 'rating', '--order', 'desc')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('2개 그룹 / 정제 리뷰 2건', result.stdout)
        self.assertLess(result.stdout.index('이어폰 B'), result.stdout.index('이어폰 A'))

    def test_invalid_arguments_and_help_do_not_open_database(self):
        cases = [('--min-reviews', '0'), ('--chart',), ('--force',), ('--name', ' '),
                 ('--date-from', '2026-09-10', '--date-to', '2026-09-01'),
                 ('--date-from', 'not-a-date'), ('--group-by', 'unknown')]
        for args in cases:
            result = self.run_cli('compare', *args)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(self.database.exists())
            self.assertNotIn('Traceback', result.stderr)
        result = self.run_cli('compare', '--help')
        self.assertEqual(result.returncode, 0)
        self.assertFalse(self.database.exists())

    def test_empty_database_generates_empty_tables(self):
        result = self.run_cli('compare', '--group-by', 'category', '--output', 'output/empty')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('조건에 맞는 정제 리뷰가 없습니다', result.stdout)
        document = json.loads(next((self.root / 'output/empty').glob('*.json')).read_text())
        self.assertEqual(document['groups'], [])

    def test_missing_chart_dependency_is_controlled_and_leaves_no_artifacts(self):
        self.seed()
        result = self.run_cli('compare', '--chart', '--output', 'output/chart')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        self.assertFalse(list((self.root / 'output/chart').iterdir()))

    def test_output_file_as_directory_reports_error(self):
        self.seed()
        (self.root / 'occupied').write_text('preserve')
        result = self.run_cli('compare', '--output', 'occupied', '--force')
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual((self.root / 'occupied').read_text(), 'preserve')

    def test_real_csv_import_clean_and_category_comparison_with_chart(self):
        (self.root / 'input.csv').write_text(
            'review_id,product_name,review_date,rating,review_text,카테고리\n'
            'a,제품 A,2026-09-01,5,만족스러운 제품입니다, 전자 \n'
            'b,제품 B,2026-09-02,1,아쉬운 제품입니다,전자\n'
            'c,제품 C,2026-09-02,3,보통인 제품입니다,\n', encoding='utf-8')
        for args in [('import', '--file', 'input.csv'), ('clean',),
                     ('compare', '--group-by', 'category', '--chart', '--output', 'output/actual')]:
            result = self.run_cli(*args, packages=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('N/A', result.stdout)
        output = self.root / 'output/actual'
        self.assertEqual({file.suffix for file in output.iterdir()}, {'.csv', '.json', '.png'})
        document = json.loads(next(output.glob('*.json')).read_text())
        electronics = next(group for group in document['groups'] if group['name'] == '전자')
        self.assertEqual(electronics['average_rating'], 3)
        self.assertEqual(electronics['total_reviews'], 2)
        self.assertEqual(electronics['product_count'], 2)
        self.assertIsNone(electronics['negative_ratio'])

    def test_excel_category_column_survives_import_and_clean(self):
        from openpyxl import Workbook
        workbook = Workbook()
        workbook.active.append(['review_id', 'product_name', 'review_date', 'rating', 'review_text', 'product_category'])
        workbook.active.append(['xlsx-a', '노트북', '2026-09-01', 4, '배송과 품질이 좋습니다', '전자'])
        workbook.save(self.root / 'input.xlsx')
        for args in [('import', '--file', 'input.xlsx'), ('clean',), ('compare', '--group-by', 'category')]:
            result = self.run_cli(*args, packages=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('전자', result.stdout)
        self.assertIn('4.00', result.stdout)
