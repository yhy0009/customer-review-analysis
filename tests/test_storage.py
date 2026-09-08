"""SQLite tests use temporary files and independent connections to verify persistence."""

import json
import os
import sqlite3
from datetime import date, datetime, timedelta, timezone
import tempfile
import unittest
from pathlib import Path

from src.errors import StorageError, ValidationError
from src.models import DuplicatePolicy, ProcessingStatus, RawReview
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


class SQLiteRawTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "reviews.db"
        self.repository = SQLiteReviewRepository(self.path)
        self.addCleanup(self.repository.close)

    def save(self, *reviews, policy=DuplicatePolicy.SKIP):
        return self.repository.save_raw_reviews(list(reviews), policy)

    def raw_row(self):
        with sqlite3.connect(self.path) as connection:
            connection.row_factory = sqlite3.Row
            return dict(connection.execute("SELECT * FROM raw_reviews").fetchone())

    def seed_dependents(self):
        """Seed future modules' output without pretending their APIs exist yet."""
        review_id = self.repository.fetch_raw_reviews()[0].id
        with sqlite3.connect(self.path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(
                "INSERT INTO clean_reviews (id, product_name, review_date, rating, review_text, "
                "cleaned_at, created_at, updated_at, status) "
                "VALUES (?, '제품', '2026-09-08', 5, '좋아요', 'time', 'time', 'time', 'ANALYZED')",
                (review_id,),
            )
            connection.execute(
                "INSERT INTO analysis_results (review_id, sentiment, confidence, keywords, "
                "analyzed_at, provider, model, created_at, updated_at) "
                "VALUES (?, 'positive', 0.9, '[]', 'time', 'test', 'test', 'time', 'time')",
                (review_id,),
            )
            connection.execute("UPDATE raw_reviews SET status='CLEANED'")

    def dependent_counts(self):
        with sqlite3.connect(self.path) as connection:
            return tuple(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                         for table in ("clean_reviews", "analysis_results"))

    def test_round_trip_preserves_invalid_raw_values_and_assigns_ids(self):
        reviews = [
            RawReview(source_review_id=123, product_name="  제품  ", review_date="잘못된 날짜",
                      rating=99, review_text="안녕 Hello 😀\n 그대로", source_file="input.csv",
                      raw_payload={"extra": [True, None, 1.5, {"이름": "값"}]}, id=900),
            RawReview(review_text=None),
        ]
        result = self.save(*reviews)
        self.assertEqual((result.processed, result.succeeded, result.failed), (2, 2, 0))
        self.repository.close()
        with SQLiteReviewRepository(self.path) as reopened:
            stored = reopened.fetch_raw_reviews()
        self.assertEqual([review.id for review in stored], [1, 2])
        self.assertEqual(reviews[0].id, 900)  # The caller's DTO is not mutated.
        for field in ("source_review_id", "product_name", "review_date", "rating",
                      "review_text", "source_file", "raw_payload"):
            self.assertEqual(getattr(stored[0], field), getattr(reviews[0], field))
        self.assertIsNone(stored[1].review_text)
        self.assertTrue(self.raw_row()["created_at"].endswith("Z"))

    def test_date_and_nan_values_are_json_safe(self):
        timestamp = datetime(2026, 9, 8, 9, tzinfo=timezone(timedelta(hours=9)))
        self.save(RawReview(review_date=date(2026, 9, 8), rating=float("nan"),
                            raw_payload={"timestamp": timestamp, "missing": float("nan")}))
        review = self.repository.fetch_raw_reviews()[0]
        self.assertEqual(review.review_date, "2026-09-08")
        self.assertIsNone(review.rating)
        self.assertEqual(review.raw_payload, {"timestamp": "2026-09-08T00:00:00Z", "missing": None})
        json.loads(self.raw_row()["raw_payload"], parse_constant=lambda value: self.fail(value))

    def test_skip_preserves_original_timestamps_and_dependents(self):
        self.save(RawReview(source_review_id="  Ａ-1  ", review_text="original"))
        self.seed_dependents()
        before = self.raw_row()
        result = self.save(RawReview(source_review_id="A-1", review_text="replacement"))
        self.assertEqual((result.succeeded, result.skipped), (0, 1))
        self.assertEqual(self.raw_row(), before)
        self.assertEqual(self.dependent_counts(), (1, 1))

    def test_upsert_preserves_identity_and_invalidates_dependents(self):
        self.save(RawReview(source_review_id=1, review_text="original"))
        self.seed_dependents()
        before = self.raw_row()
        result = self.save(RawReview(source_review_id=1.0, review_text="changed"), policy=DuplicatePolicy.UPSERT)
        after = self.raw_row()
        self.assertEqual(result.succeeded, 1)
        self.assertEqual((after["id"], after["created_at"]), (before["id"], before["created_at"]))
        self.assertGreaterEqual(after["updated_at"], before["updated_at"])
        self.assertEqual(after["status"], "RAW")
        self.assertEqual(self.repository.fetch_raw_reviews()[0].review_text, "changed")
        self.assertEqual(self.dependent_counts(), (0, 0))

    def test_source_id_leading_zeros_and_zero_are_not_lost(self):
        result = self.save(*(RawReview(source_review_id=value, review_text="same")
                             for value in (0, "0", "001", 1, 1.0, "1")))
        self.assertEqual((result.succeeded, result.skipped), (3, 3))

    def test_hash_normalizes_fields_but_preserves_original_text(self):
        first = RawReview(product_name="  제품  A ", review_date=date(2026, 9, 8),
                          rating=5, review_text="정말\n 좋아요", source_file="first.csv")
        duplicate = RawReview(source_review_id="  ", product_name="제품 A", review_date="2026-09-08",
                              rating="5.0", review_text="정말 좋아요", source_file="second.xlsx",
                              raw_payload={"extra": "not part of identity"})
        result = self.save(first, duplicate)
        self.assertEqual((result.succeeded, result.skipped), (1, 1))
        self.assertEqual(self.repository.fetch_raw_reviews()[0].review_text, first.review_text)
        self.assertTrue(self.raw_row()["dedupe_key"].startswith("sha256:"))

    def test_invalid_large_ratings_are_preserved_without_rounding_or_overflow(self):
        ratings = ["12345678901234567890123456781", "12345678901234567890123456782",
                   "1e999999999", "5", "5.000"]
        result = self.save(*(RawReview(rating=rating, review_text="same") for rating in ratings))
        self.assertEqual((result.succeeded, result.skipped), (4, 1))
        self.assertEqual([row.rating for row in self.repository.fetch_raw_reviews()], ratings[:4])

    def test_hash_boundaries_do_not_collide(self):
        self.save(RawReview(product_name="a|b", review_date="c", rating=1, review_text="d"),
                  RawReview(product_name="a", review_date="b|c", rating=1, review_text="d"))
        self.assertEqual(len(self.repository.fetch_raw_reviews()), 2)

    def test_duplicate_within_upsert_batch_keeps_last_value(self):
        result = self.save(RawReview(source_review_id="x", review_text="first"),
                           RawReview(source_review_id="x", review_text="last"), policy=DuplicatePolicy.UPSERT)
        self.assertEqual((result.processed, result.succeeded), (2, 2))
        self.assertEqual([row.review_text for row in self.repository.fetch_raw_reviews()], ["last"])

    def test_empty_batch_and_empty_status_result(self):
        result = self.save()
        self.assertEqual((result.processed, result.succeeded), (0, 0))
        self.assertEqual(self.repository.fetch_raw_reviews(), [])
        self.assertEqual(self.repository.fetch_raw_reviews(status=ProcessingStatus.RAW), [])

    def test_filters_status_and_returns_stable_order(self):
        self.save(RawReview(source_review_id="a"), RawReview(source_review_id="b"))
        with sqlite3.connect(self.path) as connection:
            connection.execute("UPDATE raw_reviews SET status='REJECTED' WHERE id=1")
        self.assertEqual([row.id for row in self.repository.fetch_raw_reviews()], [1, 2])
        self.assertEqual([row.id for row in self.repository.fetch_raw_reviews(status=ProcessingStatus.RAW)], [2])
        self.assertEqual([row.id for row in self.repository.fetch_raw_reviews(status=ProcessingStatus.REJECTED)], [1])

    def test_bad_row_does_not_prevent_good_rows_committing(self):
        with self.assertLogs("customer_review_analysis.storage", level="INFO") as logs:
            result = self.save(RawReview(source_review_id="a", review_text="SECRET_GOOD"),
                               RawReview(source_review_id="SECRET_ID", raw_payload={"SECRET": object()}),
                               RawReview(source_review_id="b"))
        self.assertEqual((result.processed, result.succeeded, result.failed, result.rejected), (3, 2, 1, 0))
        self.assertEqual(result.errors[0].item_ref, "2")
        self.assertFalse(result.errors[0].retryable)
        self.assertNotIn("SECRET", str(logs.output) + str(result.errors))
        self.repository.close()
        with SQLiteReviewRepository(self.path) as reopened:
            self.assertEqual([row.source_review_id for row in reopened.fetch_raw_reviews()], ["a", "b"])

    def test_invalid_serializable_shape_is_reported_per_row(self):
        result = self.save(RawReview(raw_payload={1: "not a string key"}),
                           RawReview(rating=float("inf")), RawReview(source_file=10), "not a review")
        self.assertEqual((result.processed, result.failed, len(result.errors)), (4, 4, 4))
        self.assertEqual(self.repository.fetch_raw_reviews(), [])

    def test_invalid_options_and_closed_repository_fail_explicitly(self):
        with self.assertRaises(ValidationError):
            self.repository.save_raw_reviews([], "skip")
        with self.assertRaises(ValidationError):
            self.repository.fetch_raw_reviews(status="RAW")
        self.repository.close()
        with self.assertRaises(StorageError):
            self.save()
        with self.assertRaises(StorageError):
            self.repository.fetch_raw_reviews()

    def test_infrastructure_failure_rolls_back_entire_batch_and_restores_children(self):
        self.save(RawReview(source_review_id="original", review_text="original"))
        self.seed_dependents()
        before = self.raw_row()
        with sqlite3.connect(self.path) as connection:
            # Integer overflow is an actual SQLite OperationalError at execution
            # time, after earlier rows have succeeded inside the same transaction.
            connection.execute("""CREATE TRIGGER fail_bad_insert BEFORE INSERT ON raw_reviews
                WHEN NEW.dedupe_key = 'id:bad'
                BEGIN SELECT abs(-9223372036854775808); END""")
        with self.assertLogs("customer_review_analysis.storage", level="ERROR") as logs:
            with self.assertRaises(StorageError):
                self.save(RawReview(source_review_id="original", review_text="SECRET_CHANGED"),
                          RawReview(source_review_id="new"), RawReview(source_review_id="bad"),
                          policy=DuplicatePolicy.UPSERT)
        self.assertNotIn("SECRET", str(logs.output))
        self.assertEqual(self.raw_row(), before)
        self.assertEqual(len(self.repository.fetch_raw_reviews()), 1)
        self.assertEqual(self.dependent_counts(), (1, 1))
        # Connection remains usable after rollback.
        self.assertEqual(self.save(RawReview(source_review_id="after_failure")).succeeded, 1)
        self.repository.close()
        with SQLiteReviewRepository(self.path) as reopened:
            self.assertEqual([r.source_review_id for r in reopened.fetch_raw_reviews()],
                             ["original", "after_failure"])

    def test_row_constraint_failure_restores_deleted_children_and_commits_other_rows(self):
        self.save(RawReview(source_review_id="original", review_text="original"))
        self.seed_dependents()
        before = self.raw_row()
        with sqlite3.connect(self.path) as connection:
            connection.execute("""CREATE TRIGGER reject_update BEFORE UPDATE ON raw_reviews
                WHEN NEW.dedupe_key = 'id:original'
                BEGIN SELECT RAISE(ABORT, 'SECRET_CONSTRAINT'); END""")
        with self.assertLogs("customer_review_analysis.storage", level="WARNING") as logs:
            result = self.save(RawReview(source_review_id="original", review_text="replacement"),
                               RawReview(source_review_id="new"), policy=DuplicatePolicy.UPSERT)
        self.assertEqual((result.processed, result.succeeded, result.failed), (2, 1, 1))
        self.assertEqual(result.errors[0].item_ref, "1")
        self.assertNotIn("SECRET", str(logs.output) + str(result.errors))
        self.assertEqual(self.raw_row(), before)
        self.assertEqual(self.dependent_counts(), (1, 1))
        self.assertEqual(len(self.repository.fetch_raw_reviews()), 2)

    def test_corrupt_persisted_json_is_a_storage_error(self):
        self.save(RawReview(source_review_id="a"))
        with sqlite3.connect(self.path) as connection:
            connection.execute("UPDATE raw_reviews SET raw_payload='SECRET_BROKEN_JSON'")
        with self.assertLogs("customer_review_analysis.storage", level="ERROR") as logs:
            with self.assertRaises(StorageError) as error:
                self.repository.fetch_raw_reviews()
        self.assertNotIn("SECRET", str(error.exception) + str(logs.output))

    def test_relative_database_path_is_based_on_project_root(self):
        project_root = Path(__file__).resolve().parents[1]
        relative_path = os.path.relpath(Path(self.temp.name) / "relative.db", project_root)
        previous = Path.cwd()
        try:
            os.chdir(self.temp.name)
            with SQLiteReviewRepository(relative_path) as repository:
                self.assertEqual(repository.database_path, (Path(self.temp.name) / "relative.db").resolve())
        finally:
            os.chdir(previous)

    def test_optional_raw_id_validation_is_backward_compatible(self):
        self.assertIsNone(RawReview().id)
        self.assertEqual(RawReview(id=1).id, 1)
        for invalid in (0, -1, True, "1"):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                RawReview(id=invalid)

    def test_sql_values_are_bound_as_parameters(self):
        text = "'); DROP TABLE raw_reviews; --"
        self.save(RawReview(source_review_id=text, review_text=text))
        self.assertEqual(self.repository.fetch_raw_reviews()[0].review_text, text)


if __name__ == "__main__":
    unittest.main()
