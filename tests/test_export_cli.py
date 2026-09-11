"""Verify export files created by the real command-line entry point."""
import csv
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from openpyxl import load_workbook
from src.models import AnalysisResult, CleanReview, DuplicatePolicy, RawReview, Sentiment
from src.sqlite_repository import SQLiteReviewRepository


class ExportCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'project'
        self.root.mkdir()
        source = Path(__file__).resolve().parents[1]
        shutil.copytree(source / 'src', self.root / 'src', ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copyfile(source / 'main.py', self.root / 'main.py')
        (self.root / 'config').mkdir()
        (self.root / 'config/config.json').write_text('{}')
        self.database = self.root / 'data/app_database.db'
        self.cwd = Path(self.temp.name) / 'elsewhere'
        self.cwd.mkdir()
        self.env = {k: v for k, v in os.environ.items() if k in {'PATH', 'TMPDIR', 'LANG', 'LC_ALL', 'SYSTEMROOT'}}
        self.env['PYTHONDONTWRITEBYTECODE'] = '1'

    def run_cli(self, *args, stdlib_only=True):
        python = [sys.executable] + (['-S'] if stdlib_only else [])
        return subprocess.run([*python, str(self.root / 'main.py'), *args], cwd=self.cwd,
                              env=self.env, text=True, capture_output=True, timeout=20)

    def export(self, format, path, *args, stdlib_only=True):
        return self.run_cli('export', '--format', format, '--output', path, *args, stdlib_only=stdlib_only)

    def seed(self):
        now = datetime.now(timezone.utc)
        with SQLiteReviewRepository(self.database) as repo:
            repo.save_raw_reviews([RawReview(source_review_id=str(i)) for i in range(1, 4)], DuplicatePolicy.SKIP)
            repo.save_clean_reviews([CleanReview(id=i, source_review_id=str(i), product_name='이어폰',
                review_date=date(2026, 9, 9 + i), rating=i, review_text=f'본문 {i}, 한글 🎧\n다음 줄', cleaned_at=now)
                for i in range(1, 4)], DuplicatePolicy.SKIP)
            for i in (1, 2):
                repo.save_analysis(AnalysisResult(review_id=i, sentiment=Sentiment.NEGATIVE, confidence=.9,
                    analyzed_at=now, provider='test', model='fake', summary='요약', keywords=['배송', '음질']))

    def test_csv_filters_and_writes_bom_relative_to_project(self):
        self.seed()
        result = self.export('csv', 'output/reviews.csv', '--sentiment', 'negative', '--rating-min', '2',
                             '--date-from', '2026-09-11', '--date-to', '2026-09-11', '--product', '이어')
        self.assertEqual(result.returncode, 0, result.stderr)
        target = self.root / 'output/reviews.csv'
        self.assertTrue(target.read_bytes().startswith(b'\xef\xbb\xbf'))
        with target.open(encoding='utf-8-sig', newline='') as file:
            rows = list(csv.DictReader(file))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['id'], '2')
        self.assertEqual(rows[0]['review_text'], '본문 2, 한글 🎧\n다음 줄')
        self.assertEqual(json.loads(rows[0]['keywords']), ['배송', '음질'])
        self.assertIn('1건', result.stdout)
        self.assertIn(str(target.resolve()), result.stdout)
        self.assertFalse((self.cwd / 'output').exists())

    def test_jsonl_exports_all_reviews_with_typed_null_analysis(self):
        self.seed()
        result = self.export('jsonl', 'output/all.jsonl')
        self.assertEqual(result.returncode, 0, result.stderr)
        raw = (self.root / 'output/all.jsonl').read_bytes()
        self.assertFalse(raw.startswith(b'\xef\xbb\xbf'))
        rows = [json.loads(line) for line in raw.decode('utf-8').splitlines()]
        self.assertEqual([r['id'] for r in rows], [1, 2, 3])
        self.assertIsNone(rows[-1]['sentiment'])
        self.assertIsNone(rows[-1]['keywords'])
        self.assertEqual(rows[0]['keywords'], ['배송', '음질'])
        self.assertTrue(rows[0]['cleaned_at'].endswith('Z'))
        self.assertIsInstance(rows[0]['rating'], int)

    def test_excel_is_a_readable_workbook(self):
        self.seed()
        result = self.export('excel', 'output/all.xlsx', stdlib_only=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        workbook = load_workbook(self.root / 'output/all.xlsx', read_only=True)
        try:
            rows = list(workbook.active.values)
            header = rows[0]
            self.assertEqual(len(rows), 4)
            self.assertEqual(rows[1][header.index('id')], 1)
            self.assertEqual(rows[1][header.index('review_text')], '본문 1, 한글 🎧\n다음 줄')
        finally:
            workbook.close()

    def test_existing_output_is_preserved_without_force(self):
        self.seed()
        target = self.root / 'existing.csv'
        target.write_bytes(b'original')
        result = self.export('csv', str(target))
        self.assertEqual(result.returncode, 3)
        self.assertEqual(target.read_bytes(), b'original')
        self.assertNotIn('내보내기 완료', result.stdout)
        result = self.export('csv', str(target), '--force')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(target.read_bytes().startswith(b'\xef\xbb\xbf'))

    def test_empty_filter_creates_zero_row_file(self):
        self.seed()
        result = self.export('jsonl', 'none.jsonl', '--sentiment', 'positive')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('0건', result.stdout)
        self.assertEqual((self.root / 'none.jsonl').read_bytes(), b'')

    def test_database_and_sidecar_cannot_be_overwritten(self):
        self.seed()
        before = self.database.read_bytes()
        for path in (self.database, self.database.with_name(self.database.name + '-wal')):
            result = self.export('csv', str(path), '--force')
            self.assertEqual(result.returncode, 3)
            self.assertIn('SQLite', result.stderr)
        self.assertEqual(self.database.read_bytes(), before)
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_bad_db_and_invalid_dates_do_not_create_output(self):
        result = self.export('csv', 'bad.csv', '--date-from', '2026-09-11', '--date-to', '2026-09-01')
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.database.exists())
        self.database.parent.mkdir()
        self.database.write_text('broken DB')
        result = self.export('csv', 'bad.csv')
        self.assertEqual(result.returncode, 3)
        self.assertFalse((self.root / 'bad.csv').exists())
        self.assertNotIn('Traceback', result.stderr)

    def test_mismatched_suffix_and_symlink_return_expected_errors(self):
        self.seed()
        result = self.export('csv', 'wrong.xlsx')
        self.assertEqual(result.returncode, 2)
        self.assertFalse((self.root / 'wrong.xlsx').exists())
        target = self.root / 'original.csv'
        target.write_text('original')
        link = self.root / 'link.csv'
        link.symlink_to(target)
        result = self.export('csv', str(link), '--force')
        self.assertEqual(result.returncode, 3)
        self.assertEqual(target.read_text(), 'original')

    def test_overlong_output_path_has_clean_error_before_opening_database(self):
        result = self.export('csv', 'x' * 300 + '.csv')
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        self.assertNotIn('내보내기 완료', result.stdout)
        self.assertFalse(self.database.exists())

    def test_excel_without_dependency_has_clean_error(self):
        self.seed()
        result = self.export('excel', 'missing.xlsx', stdlib_only=True)
        self.assertEqual(result.returncode, 3)
        self.assertNotIn('Traceback', result.stderr)
        self.assertFalse((self.root / 'missing.xlsx').exists())


if __name__ == '__main__':
    unittest.main()
