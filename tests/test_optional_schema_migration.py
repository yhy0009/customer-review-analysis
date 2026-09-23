"""Migrate released v1 files atomically without losing IDs, analyses or metadata."""
import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from src.errors import StorageError
from src.models import CleanReview, DuplicatePolicy, RawReview
from src.sqlite_repository import SQLiteReviewRepository


class OptionalSchemaMigrationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'legacy.sqlite'
        self.now = '2026-09-22T00:00:00Z'
        with sqlite3.connect(self.path) as connection:
            connection.executescript((Path(__file__).parent / 'fixtures/sqlite_v1.sql').read_text())
            for id_, status in ((7, 'ANALYZED'), (11, 'ANALYSIS_FAILED'), (13, 'RAW'), (16, 'REJECTED')):
                connection.execute('INSERT INTO raw_reviews VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                    (id_, f'id:{id_}', json.dumps(str(id_)), json.dumps('제품'), json.dumps('2026-09-22'),
                     '5', json.dumps('테스트 원문'), None, '{}', status, self.now, self.now))
                if id_ in (7, 11):
                    connection.execute('INSERT INTO clean_reviews VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                        (id_, str(id_), '제품', '2026-09-22', 5, '테스트 원문', self.now, status,
                         'previous failure' if id_ == 11 else None, self.now, self.now))
            connection.execute('INSERT INTO analysis_results VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                (7, 'positive', .95, '이전 요약', '["품질"]', self.now, 'old', 'saved-model', 'v1', self.now, self.now))
            connection.execute('CREATE INDEX custom_clean_date ON clean_reviews(review_date)')
            connection.execute("CREATE TRIGGER guard_text BEFORE UPDATE OF review_text ON clean_reviews "
                               "WHEN NEW.review_text='' BEGIN SELECT RAISE(ABORT,'empty'); END")

    def rows(self):
        with sqlite3.connect(self.path) as connection:
            return {table: connection.execute(f'SELECT * FROM {table} ORDER BY 1').fetchall()
                    for table in ('raw_reviews', 'clean_reviews', 'analysis_results')}

    def test_upgrade_preserves_all_rows_and_foreign_keys_and_custom_objects(self):
        before = self.rows()
        with SQLiteReviewRepository(self.path) as repository:
            self.assertEqual(repository.get_review(7).analysis.summary, '이전 요약')
            self.assertEqual(repository.get_statistics().failed_reviews, 1)
            self.assertEqual(repository._connection.execute('PRAGMA foreign_keys').fetchone()[0], 1)
        self.assertEqual(self.rows(), before)
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 2)
            self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertEqual({row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE name IN ('custom_clean_date','guard_text')")},
                             {'custom_clean_date', 'guard_text'})
        with SQLiteReviewRepository(self.path) as repository:
            repository.save_clean_reviews([CleanReview(13, None, None, None, '본문만 새로 정제',
                datetime.now(timezone.utc))], DuplicatePolicy.SKIP)
            self.assertIsNone(repository.get_review(13).review.rating)
            # FK protection is still active after the table swap.
            repository.mark_cleaning_rejected(7)
            self.assertIsNone(repository.get_review(7))
            self.assertEqual(repository._connection.execute('SELECT count(*) FROM analysis_results').fetchone()[0], 0)

    def test_failed_migration_rolls_back_schema_and_rows_then_can_retry(self):
        before = self.rows()
        migrate = SQLiteReviewRepository._migrate_optional_fields
        def fail_after_swap(connection):
            migrate(connection)
            raise sqlite3.OperationalError('simulated migration failure')
        with patch.object(SQLiteReviewRepository, '_migrate_optional_fields', side_effect=fail_after_swap):
            with self.assertRaises(StorageError):
                SQLiteReviewRepository(self.path)
        self.assertEqual(self.rows(), before)
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 1)
            fields = {row[1]: row for row in connection.execute('PRAGMA table_info(clean_reviews)')}
            self.assertEqual(fields['rating'][3], 1)
        with SQLiteReviewRepository(self.path) as repository:
            self.assertIsNotNone(repository.get_review(7).analysis)

    def test_read_only_v1_is_supported_without_migration_or_file_writes(self):
        before = self.path.read_bytes()
        with SQLiteReviewRepository(self.path, read_only=True) as repository:
            self.assertEqual(repository.get_statistics().total_reviews, 2)
            self.assertEqual(repository._connection.execute('PRAGMA user_version').fetchone()[0], 1)
        self.assertEqual(self.path.read_bytes(), before)

    def test_concurrent_first_opens_upgrade_once_and_retain_saved_analysis(self):
        def open_database(_):
            with SQLiteReviewRepository(self.path) as repository:
                return repository.get_review(7).analysis.summary
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(open_database, range(2))), ['이전 요약', '이전 요약'])


if __name__ == '__main__':
    unittest.main()
