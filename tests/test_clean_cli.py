"""Exercise import -> clean -> query with default CLI wiring and isolated files."""
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


class CleanCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / 'project'
        self.root.mkdir()
        source = Path(__file__).resolve().parents[1]
        shutil.copytree(source / 'src', self.root / 'src', ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copyfile(source / 'main.py', self.root / 'main.py')
        (self.root / 'config').mkdir()
        self.configure()
        self.database = self.root / 'data/app_database.db'
        self.input = self.root / 'reviews.csv'
        self.other_cwd = self.root.parent / 'elsewhere'
        self.other_cwd.mkdir()
        self.environment = {key: value for key, value in os.environ.items()
                            if key in {'PATH', 'TMPDIR', 'LANG', 'LC_ALL', 'SYSTEMROOT'}}
        self.environment['PYTHONDONTWRITEBYTECODE'] = '1'

    def configure(self, *, policy='skip', min_length=3):
        (self.root / 'config/config.json').write_text(json.dumps({
            'cleaning': {'duplicate_policy': policy, 'min_review_length': min_length},
            'logging': {'file': None, 'level': 'ERROR'},
        }), encoding='utf-8')

    def run_cli(self, *args):
        # Only file collection requires site packages. Clean/query/export must
        # run without pandas, matplotlib, OpenAI or API credentials.
        options = [] if args[0] == 'import' else ['-S']
        return subprocess.run([sys.executable, *options, str(self.root / 'main.py'), *args],
                              cwd=self.other_cwd, env=self.environment, text=True,
                              capture_output=True, timeout=30)

    def import_rows(self, rows, *options):
        self.input.write_text('review_id,product_name,review_date,rating,review_text\n' + rows,
                              encoding='utf-8-sig')
        result = self.run_cli('import', '--file', 'reviews.csv', *options)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_partial_clean_is_visible_to_queries_export_and_analysis_selection(self):
        self.import_rows('bad,제품,invalid,5,날짜가 없는 리뷰\n'
                         'good,제품,2026/09/18,5.0,  정말   좋아요  \n')
        result = self.run_cli('clean')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('processed=2 succeeded=1 skipped=0 failed=0 rejected=1', result.stdout)
        self.assertIn('[INVALID_REVIEW_DATE] item=1:', result.stdout)
        self.assertFalse(result.stderr)
        with SQLiteReviewRepository(self.database) as repository:
            self.assertEqual([r.id for r in repository.fetch_raw_reviews(status=ProcessingStatus.REJECTED)], [1])
            self.assertEqual([r.id for r in repository.fetch_unanalyzed_reviews()], [2])
        stats = self.run_cli('stats')
        self.assertEqual(stats.returncode, 0, stats.stderr)
        self.assertIn('정제 리뷰 수: 1건', stats.stdout)
        listed = self.run_cli('list')
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertIn('정말 좋아요', listed.stdout)
        self.assertNotIn('날짜가 없는 리뷰', listed.stdout)
        detail = self.run_cli('show', '--id', '2')
        self.assertEqual(detail.returncode, 0, detail.stderr)
        self.assertIn('분석 결과 없음', detail.stdout)
        exported = self.run_cli('export', '--format', 'jsonl', '--output', 'output/reviews.jsonl')
        self.assertEqual(exported.returncode, 0, exported.stderr)
        lines = (self.root / 'output/reviews.jsonl').read_text(encoding='utf-8').splitlines()
        self.assertEqual(len(lines), 1)
        self.assertIn('정말 좋아요', lines[0])
        self.assertFalse((self.other_cwd / 'data').exists())

    def test_config_defaults_and_cli_overrides_control_revalidation(self):
        self.import_rows('r1,제품,2026-09-18,5,정말 좋아요\n')
        self.configure(min_length=100)
        self.assertEqual(self.run_cli('clean').returncode, 1)
        accepted = self.run_cli('clean', '--min-length', '3')
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertIn('succeeded=1', accepted.stdout)
        self.configure(policy='upsert', min_length=100)
        skipped = self.run_cli('clean', '--policy', 'skip')
        self.assertEqual(skipped.returncode, 0, skipped.stderr)
        self.assertIn('succeeded=0 skipped=1', skipped.stdout)
        rejected = self.run_cli('clean')
        self.assertEqual(rejected.returncode, 1, rejected.stderr)
        self.assertIn('rejected=1', rejected.stdout)
        with SQLiteReviewRepository(self.database) as repository:
            self.assertIsNone(repository.get_review(1))
            self.assertEqual(len(repository.fetch_raw_reviews(status=ProcessingStatus.REJECTED)), 1)

    def test_reimport_corrected_raw_then_clean_preserves_internal_id(self):
        self.import_rows('r1,제품,bad-date,5,정말 좋아요\n')
        self.assertEqual(self.run_cli('clean').returncode, 1)
        self.import_rows('r1,제품,2026-09-18,5,정말 좋아요\n', '--policy', 'upsert')
        result = self.run_cli('clean')
        self.assertEqual(result.returncode, 0, result.stderr)
        with SQLiteReviewRepository(self.database) as repository:
            self.assertEqual([r.id for r in repository.fetch_clean_reviews()], [1])
            self.assertFalse(repository.fetch_raw_reviews(status=ProcessingStatus.REJECTED))

    def test_empty_clean_succeeds_without_optional_packages(self):
        result = self.run_cli('clean')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('processed=0 succeeded=0 skipped=0 failed=0 rejected=0', result.stdout)

    def test_help_and_invalid_options_do_not_create_database(self):
        result = self.run_cli('clean', '--help')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--min-length', result.stdout)
        for arguments in (('--min-length', '0'), ('--min-length', 'bad'), ('--policy', 'invalid')):
            with self.subTest(arguments=arguments):
                result = self.run_cli('clean', *arguments)
                self.assertEqual(result.returncode, 2)
                self.assertNotIn('Traceback', result.stderr)
        self.assertFalse(self.database.exists())

    def test_invalid_database_returns_three_without_success_output(self):
        self.database.parent.mkdir()
        self.database.write_bytes(b'invalid SQLite file')
        result = self.run_cli('clean')
        self.assertEqual(result.returncode, 3)
        self.assertFalse(result.stdout)
        self.assertNotIn('Traceback', result.stderr)
        self.assertEqual(self.database.read_bytes(), b'invalid SQLite file')


if __name__ == '__main__':
    unittest.main()
