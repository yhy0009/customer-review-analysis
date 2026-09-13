"""Run the real CLI in an isolated project copy with a populated SQLite file."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch
import contextlib
import io

from src.cli import main
from src.models import AnalysisResult, CleanReview, DuplicatePolicy, RawReview, Sentiment
from src.sqlite_repository import SQLiteReviewRepository


class QueryCliTests(unittest.TestCase):
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
        self.other_cwd = Path(self.temp.name) / 'elsewhere'
        self.other_cwd.mkdir()
        self.environment = {key: value for key, value in os.environ.items()
                            if key in {'PATH', 'TMPDIR', 'LANG', 'LC_ALL', 'SYSTEMROOT'}}
        self.environment['PYTHONDONTWRITEBYTECODE'] = '1'

    def run_cli(self, *args):
        # No site packages: these queries must work without pandas/OpenAI/API keys.
        return subprocess.run([sys.executable, '-S', str(self.root / 'main.py'), *args],
                              cwd=self.other_cwd, env=self.environment, text=True,
                              capture_output=True, timeout=15)

    def seed(self):
        now = datetime.now(timezone.utc)
        fields = [('이어폰', 5, 9, '배송이 빨라요'), ('이어폰', 2, 10, '음질이 아쉬워요'),
                  ('키보드', 3, 10, '새로운 리뷰입니다'), ('이어폰', 1, 11, '분석에 실패한 리뷰')]
        with SQLiteReviewRepository(self.database) as repo:
            repo.save_raw_reviews([RawReview(source_review_id=str(i)) for i in range(1, 5)], DuplicatePolicy.SKIP)
            repo.save_clean_reviews([CleanReview(id=i, product_name=product, rating=rating,
                                    review_date=date(2026, 9, day), review_text=body, cleaned_at=now)
                                    for i, (product, rating, day, body) in enumerate(fields, 1)], DuplicatePolicy.SKIP)
            for i, sentiment in [(1, Sentiment.POSITIVE), (2, Sentiment.NEGATIVE)]:
                repo.save_analysis(AnalysisResult(review_id=i, sentiment=sentiment, confidence=.9,
                    analyzed_at=now, provider='fake', model='test-model', summary='검증용 요약', keywords=['음질']))
            repo.mark_analysis_failed(4, 'failure')

    def test_list_filters_dates_rating_product_and_pagination(self):
        self.seed()
        result = self.run_cli('list', '--sentiment', 'negative', '--rating', '2',
                             '--product', '이어', '--date-from', '2026-09-10', '--date-to', '2026-09-10',
                             '--page', '1', '--size', '1', '--sort', 'date', '--order', 'asc')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('음질이 아쉬워요', result.stdout)
        self.assertNotIn('배송이 빨라요', result.stdout)
        self.assertIn('1/1', result.stdout)
        self.assertFalse(result.stderr)

    def test_list_sorts_and_handles_out_of_range_page(self):
        self.seed()
        result = self.run_cli('list', '--sort', 'rating', '--order', 'desc', '--size', '1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('배송이 빨라요', result.stdout)
        self.assertNotIn('음질이 아쉬워요', result.stdout)
        result = self.run_cli('list', '--page', '99')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('요청한 페이지에 리뷰가 없습니다', result.stdout)

    def test_show_analyzed_unanalyzed_and_missing_reviews(self):
        self.seed()
        result = self.run_cli('show', '--id', '1')
        self.assertEqual(result.returncode, 0, result.stderr)
        for text in ('배송이 빨라요', '검증용 요약', '음질', 'test-model'):
            self.assertIn(text, result.stdout)
        result = self.run_cli('show', '--id', '3')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('분석 결과 없음', result.stdout)
        result = self.run_cli('show', '--id', '999')
        self.assertEqual(result.returncode, 1)
        self.assertFalse(result.stdout)
        self.assertIn('ID=999', result.stderr)

    def test_stats_include_failed_and_unprocessed_with_correct_denominators(self):
        self.seed()
        result = self.run_cli('stats')
        self.assertEqual(result.returncode, 0, result.stderr)
        for text in ('정제 리뷰 수: 4건', '분석 완료: 2건', '미분석 (실패 제외): 1건',
                     '분석 실패: 1건', '분석 완료율: 50.0%', '평균 별점: 2.75', '분석 완료 2건 기준'):
            self.assertIn(text, result.stdout)
        result = self.run_cli('stats', '--sentiment', 'negative', '--date-from', '2026-09-10', '--product', '이어')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('정제 리뷰 수: 1건', result.stdout)
        self.assertIn('평균 별점: 2.00', result.stdout)

    def test_empty_database_has_successful_output(self):
        result = self.run_cli('stats')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('정제 리뷰 수: 0건', result.stdout)
        self.assertIn('N/A', result.stdout)
        result = self.run_cli('list')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('조회된 정제 리뷰가 없습니다', result.stdout)
        self.assertTrue(self.database.exists())
        self.assertTrue((self.root / 'logs/app.log').exists())
        self.assertFalse((self.other_cwd / 'logs').exists())
        self.assertFalse((self.other_cwd / 'data').exists())

    def test_env_and_custom_config_paths_use_project_root(self):
        # This file must be ignored because it belongs to the calling directory.
        (self.other_cwd / '.env').write_text('INVALID ENV LINE')
        (self.root / '.env').write_text('CRA_DATABASE_PATH=data/from-env.db\nCRA_LOG_FILE=logs/env.log\n')
        (self.root / 'config/query.json').write_text(json.dumps({'storage': {'database_path': 'data/config.db'}}))
        result = self.run_cli('--config', 'config/query.json', 'stats')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / 'data/from-env.db').exists())
        self.assertFalse((self.root / 'data/config.db').exists())
        self.assertTrue((self.root / 'logs/env.log').exists())

    def test_bad_db_and_jsonl_return_storage_exit_code(self):
        self.database.parent.mkdir()
        self.database.write_text('invalid SQLite file')
        result = self.run_cli('stats')
        self.assertEqual(result.returncode, 3)
        self.assertFalse(result.stdout)
        self.assertNotIn('Traceback', result.stderr)
        (self.root / 'config/config.json').write_text(json.dumps({'storage': {'backend': 'jsonl'}}))
        result = self.run_cli('stats')
        self.assertEqual(result.returncode, 3)

    def test_invalid_dates_and_help_do_not_open_database(self):
        result = self.run_cli('list', '--date-from', '2026-09-11', '--date-to', '2026-09-01')
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.database.exists())
        result = self.run_cli('--help')
        self.assertEqual(result.returncode, 0)
        self.assertFalse(self.database.exists())

    def test_unconnected_command_does_not_create_database_or_call_ai(self):
        result = self.run_cli('analyze', '--unanalyzed')
        self.assertEqual(result.returncode, 2)
        self.assertIn('아직 연결되지 않았습니다', result.stderr)
        self.assertFalse(self.database.exists())

    def test_unresolvable_config_path_returns_exit_two(self):
        loop = self.root / 'config/loop.json'
        loop.symlink_to(loop.name)
        result = self.run_cli('--config', 'config/loop.json', 'stats')
        self.assertEqual(result.returncode, 2)
        self.assertNotIn('Traceback', result.stderr)
        self.assertFalse(self.database.exists())

    def test_explicit_empty_handler_map_does_not_fall_back(self):
        with patch('src.cli.load_env_file'), patch('src.cli.load_config', return_value={}), \
             patch('src.cli.configure_logging'), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(['stats'], handlers={}), 2)


if __name__ == '__main__':
    unittest.main()
