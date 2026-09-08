"""SQLite tests use temporary files and independent connections to verify persistence."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.errors import StorageError, ValidationError
from src.storage import SQLiteReviewRepository


class SQLiteLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "nested" / "reviews.db"

    def test_creates_schema_and_reopens_without_losing_data(self):
        with SQLiteReviewRepository(self.path) as repository:
            self.assertEqual(repository.database_path, self.path.resolve())
        with sqlite3.connect(self.path) as connection:
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            self.assertTrue({"raw_reviews", "clean_reviews", "analysis_results"} <= tables)
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
            connection.execute(
                "INSERT INTO raw_reviews (dedupe_key, product_name, review_date, rating, "
                "review_text, raw_payload, created_at, updated_at) "
                "VALUES ('id:1', 'null', 'null', 'null', 'null', '{}', 'time', 'time')"
            )
        with SQLiteReviewRepository(self.path):
            pass
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM raw_reviews").fetchone()[0], 1)

    def test_context_manager_closes_connection_even_on_exception(self):
        with self.assertRaisesRegex(RuntimeError, "test"):
            with SQLiteReviewRepository(self.path) as repository:
                raise RuntimeError("test")
        repository.close()
        with self.assertRaises(StorageError):
            repository.__enter__()

    def test_invalid_file_and_directory_raise_storage_error(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("not a sqlite database")
        with self.assertRaises(StorageError):
            SQLiteReviewRepository(self.path)
        with self.assertRaises(StorageError):
            SQLiteReviewRepository(self.path.parent)

    def test_rejects_memory_and_empty_paths(self):
        for path in ("", " ", ":memory:"):
            with self.subTest(path=path), self.assertRaises(ValidationError):
                SQLiteReviewRepository(path)

    def test_unknown_schema_is_not_modified(self):
        self.path.parent.mkdir(parents=True)
        with sqlite3.connect(self.path) as connection:
            connection.execute("CREATE TABLE existing (value TEXT)")
            connection.execute("INSERT INTO existing VALUES ('preserve')")
        with self.assertRaises(StorageError):
            SQLiteReviewRepository(self.path)
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute("SELECT value FROM existing").fetchone()[0], "preserve")
            connection.execute("PRAGMA user_version = 99")
        with self.assertRaises(StorageError):
            SQLiteReviewRepository(self.path)

    def test_incomplete_versioned_schema_is_rejected(self):
        with SQLiteReviewRepository(self.path):
            pass
        with sqlite3.connect(self.path) as connection:
            connection.execute("DROP TABLE analysis_results")
        with self.assertRaises(StorageError):
            SQLiteReviewRepository(self.path)


if __name__ == "__main__":
    unittest.main()
