"""Run the default import command with real files and SQLite from another cwd."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from src.models import ProcessingStatus
from src.sqlite_repository import SQLiteReviewRepository


class ImportCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / 'project'
        self.root.mkdir()
        source = Path(__file__).resolve().parents[1]
        shutil.copytree(source / 'src', self.root / 'src', ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copyfile(source / 'main.py', self.root / 'main.py')
        (self.root / 'config').mkdir()
        self.configure('skip')
        self.database = self.root / 'data/app_database.db'
        self.input = self.root / 'reviews.csv'
        self.other_cwd = self.root.parent / 'elsewhere'
        self.other_cwd.mkdir()
        self.environment = {key: value for key, value in os.environ.items()
                            if key in {'PATH', 'TMPDIR', 'LANG', 'LC_ALL', 'SYSTEMROOT'}}
        self.environment['PYTHONDONTWRITEBYTECODE'] = '1'

    def configure(self, policy):
        (self.root / 'config/config.json').write_text(json.dumps({
            'cleaning': {'duplicate_policy': policy},
            'logging': {'file': None, 'level': 'WARNING'},
        }), encoding='utf-8')

    def run_cli(self, *args, without_packages=False):
        options = ['-S'] if without_packages else []
        return subprocess.run([sys.executable, *options, str(self.root / 'main.py'), *args],
                              cwd=self.other_cwd, env=self.environment, text=True,
                              capture_output=True, timeout=30)

    def write_csv(self, text='배송이 빨라요', encoding='utf-8-sig'):
        self.input.write_text(
            f'리뷰_id,상품명,작성일,평점,리뷰 내용,채널\nr1,이어폰,2026-09-18,5,{text},웹\n',
            encoding=encoding,
        )

    def test_csv_import_from_other_cwd_keeps_provenance_and_raw_values(self):
        self.input.write_text(
            'review_id,product_name,review_date,rating,review_text,channel\n'
            'r1,이어폰,2026-09-18,5,배송이 빨라요,웹\n'
            'r2,,bad-date,99,,앱\n', encoding='utf-8-sig',
        )
        result = self.run_cli('import', '--file', 'reviews.csv')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('processed=2 succeeded=2 skipped=0 failed=0 rejected=0', result.stdout)
        self.assertFalse(result.stderr)
        with SQLiteReviewRepository(self.database) as repository:
            rows = repository.fetch_raw_reviews(status=ProcessingStatus.RAW)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0].source_file, str(self.input))
            self.assertEqual(rows[0].raw_payload, {'channel': '웹'})
            self.assertEqual((rows[1].review_date, rows[1].rating, rows[1].review_text),
                             ('bad-date', 99, None))
            self.assertFalse(repository.fetch_clean_reviews())
        stats = self.run_cli('stats', without_packages=True)
        self.assertEqual(stats.returncode, 0, stats.stderr)
        self.assertIn('정제 리뷰 수: 0건', stats.stdout)
        self.assertFalse((self.other_cwd / 'data').exists())

    def test_config_policy_and_cli_override_control_duplicates(self):
        self.write_csv()
        self.assertEqual(self.run_cli('import', '--file', str(self.input)).returncode, 0)
        self.configure('upsert')
        self.write_csv('내용을 수정했어요')
        skipped = self.run_cli('import', '--file', str(self.input), '--policy', 'skip')
        self.assertEqual(skipped.returncode, 0, skipped.stderr)
        self.assertIn('succeeded=0 skipped=1', skipped.stdout)
        with SQLiteReviewRepository(self.database) as repository:
            original = repository.fetch_raw_reviews()[0]
            self.assertEqual(original.review_text, '배송이 빨라요')
        updated = self.run_cli('import', '--file', str(self.input))
        self.assertEqual(updated.returncode, 0, updated.stderr)
        self.assertIn('succeeded=1 skipped=0', updated.stdout)
        with SQLiteReviewRepository(self.database) as repository:
            self.assertEqual([(r.id, r.review_text) for r in repository.fetch_raw_reviews()],
                             [(original.id, '내용을 수정했어요')])

    def test_cp949_and_korean_aliases_are_supported(self):
        self.write_csv(encoding='cp949')
        result = self.run_cli('import', '--file', 'reviews.csv')
        self.assertEqual(result.returncode, 0, result.stderr)
        with SQLiteReviewRepository(self.database) as repository:
            row = repository.fetch_raw_reviews()[0]
            self.assertEqual((row.source_review_id, row.product_name, row.review_text),
                             ('r1', '이어폰', '배송이 빨라요'))

    def test_excel_import_uses_default_service(self):
        from openpyxl import Workbook

        path = self.root / 'reviews.xlsx'
        book = Workbook()
        book.active.append(['review_id', 'review_text', 'rating', 'channel'])
        book.active.append(['excel-1', '엑셀 리뷰', 4, 'Excel'])
        book.save(path)
        book.close()
        result = self.run_cli('import', '--file', str(path))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('succeeded=1', result.stdout)
        with SQLiteReviewRepository(self.database) as repository:
            row = repository.fetch_raw_reviews()[0]
            self.assertEqual(row.source_review_id, 'excel-1')
            self.assertEqual(row.source_file, str(path))
            self.assertEqual(row.raw_payload, {'channel': 'Excel'})

    def test_file_errors_return_three_without_modifying_existing_rows(self):
        self.write_csv()
        self.assertEqual(self.run_cli('import', '--file', 'reviews.csv').returncode, 0)
        cases = {
            'empty.csv': '',
            'header-only.csv': 'review_text\n',
            'no-text.csv': 'rating\n5\n',
            'unsupported.txt': 'review_text\n리뷰\n',
            'broken.xlsx': 'not an Excel workbook',
        }
        for name, content in cases.items():
            (self.root / name).write_text(content, encoding='utf-8')
        for name in [*cases, 'missing.csv', 'config']:
            with self.subTest(file=name):
                result = self.run_cli('import', '--file', name)
                self.assertEqual(result.returncode, 3, result.stderr)
                self.assertFalse(result.stdout)
                self.assertTrue(result.stderr)
                self.assertNotIn('Traceback', result.stderr)
                with SQLiteReviewRepository(self.database) as repository:
                    self.assertEqual([r.source_review_id for r in repository.fetch_raw_reviews()], ['r1'])

    def test_import_help_works_without_optional_packages_or_database(self):
        result = self.run_cli('import', '--help', without_packages=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--policy', result.stdout)
        self.assertFalse(self.database.exists())

    def test_missing_pandas_is_a_configuration_error(self):
        self.write_csv()
        result = self.run_cli('import', '--file', 'reviews.csv', without_packages=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('requirements.txt', result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        self.assertFalse(result.stdout)


if __name__ == '__main__':
    unittest.main()
