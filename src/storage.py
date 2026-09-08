"""Persistence boundary shared by SQLite and JSONL implementations."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import unicodedata
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from src.config import get_logger
from src.errors import StorageError, ValidationError

from typing import List, Optional, Protocol, Sequence, runtime_checkable

from src.models import (
    AnalysisResult,
    BatchOperationResult,
    CleanReview,
    DuplicatePolicy,
    ItemError,
    Page,
    ProcessingStatus,
    RawReview,
    ReviewDetail,
    ReviewFilter,
    ReviewQuery,
    ReviewStatistics,
)


@runtime_checkable
class ReviewRepository(Protocol):
    """Backend-neutral persistence contract for the application."""

    def save_raw_reviews(
        self,
        reviews: Sequence[RawReview],
        policy: DuplicatePolicy,
    ) -> BatchOperationResult:
        ...

    def fetch_raw_reviews(
        self,
        *,
        status: Optional[ProcessingStatus] = None,
    ) -> List[RawReview]:
        ...

    def save_clean_reviews(
        self,
        reviews: Sequence[CleanReview],
        policy: DuplicatePolicy,
    ) -> BatchOperationResult:
        ...

    def fetch_clean_reviews(
        self,
        filters: Optional[ReviewFilter] = None,
        *,
        limit: Optional[int] = None,
    ) -> List[CleanReview]:
        ...

    def fetch_unanalyzed_reviews(
        self,
        *,
        limit: Optional[int] = None,
    ) -> List[CleanReview]:
        ...

    def save_analysis(self, result: AnalysisResult) -> None:
        ...

    def mark_analysis_failed(self, review_id: int, error_message: str) -> None:
        ...

    def get_review(self, review_id: int) -> Optional[ReviewDetail]:
        ...

    def list_reviews(self, query: ReviewQuery) -> Page[ReviewDetail]:
        ...

    def get_statistics(
        self,
        filters: Optional[ReviewFilter] = None,
    ) -> ReviewStatistics:
        ...


# Version 1 is the initial schema; future changes require an explicit migration.
_SCHEMA_VERSION = 1
_SCHEMA = (
    """CREATE TABLE raw_reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        dedupe_key TEXT NOT NULL UNIQUE,
        source_review_id TEXT,
        product_name TEXT NOT NULL,
        review_date TEXT NOT NULL,
        rating TEXT NOT NULL,
        review_text TEXT NOT NULL,
        source_file TEXT,
        raw_payload TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'RAW'
            CHECK (status IN ('RAW', 'REJECTED', 'CLEANED', 'ANALYZED', 'ANALYSIS_FAILED')),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE clean_reviews (
        id INTEGER PRIMARY KEY REFERENCES raw_reviews(id) ON DELETE CASCADE,
        source_review_id TEXT,
        product_name TEXT NOT NULL,
        review_date TEXT NOT NULL,
        rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
        review_text TEXT NOT NULL,
        cleaned_at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'CLEANED'
            CHECK (status IN ('CLEANED', 'ANALYZED', 'ANALYSIS_FAILED')),
        error_message TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE analysis_results (
        review_id INTEGER PRIMARY KEY REFERENCES clean_reviews(id) ON DELETE CASCADE,
        sentiment TEXT NOT NULL CHECK (sentiment IN ('positive', 'neutral', 'negative')),
        confidence REAL NOT NULL CHECK (confidence BETWEEN 0.0 AND 1.0),
        summary TEXT,
        keywords TEXT NOT NULL,
        analyzed_at TEXT NOT NULL,
        provider TEXT NOT NULL,
        model TEXT NOT NULL,
        prompt_version TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    "CREATE INDEX idx_raw_reviews_status ON raw_reviews(status)",
)
_SCHEMA_COLUMNS = {
    "raw_reviews": "id, dedupe_key, source_review_id, product_name, review_date, rating, "
                   "review_text, source_file, raw_payload, status, created_at, updated_at",
    "clean_reviews": "id, source_review_id, product_name, review_date, rating, review_text, "
                     "cleaned_at, status, error_message, created_at, updated_at",
    "analysis_results": "review_id, sentiment, confidence, summary, keywords, analyzed_at, "
                        "provider, model, prompt_version, created_at, updated_at",
}


def _json_value(value: object) -> object:
    """Convert file-input values to JSON without silently stringifying objects."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return None
        if not math.isfinite(value):
            raise ValueError("Non-finite numeric value")
        return value
    if isinstance(value, datetime):
        if value.utcoffset() is not None:
            return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return {key: _json_value(item) for key, item in value.items()}
    raise ValueError("Unsupported raw value")


def _json_dump(value: object) -> str:
    return json.dumps(_json_value(value), ensure_ascii=False, allow_nan=False,
                      sort_keys=True, separators=(",", ":"))


def _normalized_text(value: object) -> str:
    value = _json_value(value)
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        text = _json_dump(value)
    else:
        text = str(value)
    return " ".join(unicodedata.normalize("NFKC", text).split())


def _normalized_number(value: object) -> str:
    text = _normalized_text(value)
    try:
        number = Decimal(text)
        if number.is_finite():
            if number == 0:
                return "0"
            # Decimal.normalize() uses the active precision and can round large
            # raw values or overflow. Strip zeros exactly, without arithmetic.
            sign, digits, exponent = number.as_tuple()
            digits = list(digits)
            while digits[-1] == 0:
                digits.pop()
                exponent += 1
            return ("-" if sign else "") + "".join(map(str, digits)) + "e" + str(exponent)
    except InvalidOperation:
        pass
    return text


def _raw_dedupe_key(review: RawReview) -> str:
    source_id = _normalized_text(review.source_review_id)
    # Excel often represents integer identifiers as floats. Preserve textual
    # leading zeros ("001" remains different from "1").
    if isinstance(review.source_review_id, (int, float)) and not isinstance(
        review.source_review_id, bool
    ) and source_id:
        number = Decimal(source_id)
        if number == number.to_integral_value():
            source_id = str(int(number))
    if source_id:
        return "id:" + source_id
    review_date = _normalized_text(review.review_date)
    try:
        review_date = date.fromisoformat(review_date).isoformat()
    except ValueError:
        pass  # Invalid dates remain raw input for the cleaner.
    fields = [
        _normalized_text(review.product_name), review_date,
        _normalized_number(review.rating), _normalized_text(review.review_text),
    ]
    # JSON framing avoids collisions between adjacent fields containing delimiters.
    return "sha256:" + hashlib.sha256(_json_dump(fields).encode("utf-8")).hexdigest()


class SQLiteReviewRepository:
    """File-backed SQLite storage. This increment implements lifecycle and Raw I/O.

    Clean/Analysis I/O and query/statistics methods will be added separately;
    this class does not yet implement the complete ReviewRepository protocol.
    Relative database paths are resolved against the project root.
    """

    def __init__(self, database_path: Path | str) -> None:
        if not str(database_path).strip() or str(database_path) == ":memory:":
            raise ValidationError("A persistent database file path is required")
        path = Path(database_path)
        if not path.is_absolute():
            path = Path(__file__).resolve().parents[1] / path
        self.database_path = path.resolve()
        self._connection: Optional[sqlite3.Connection] = None
        self._logger = get_logger("storage")
        try:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(self.database_path)
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._initialize_schema()
        except (OSError, sqlite3.Error, StorageError) as exc:
            self.close()
            self._logger.error("SQLite initialization failed")
            raise StorageError("SQLite 저장소를 초기화할 수 없습니다.") from exc

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise StorageError("SQLite 저장소가 닫혀 있습니다.")
        return self._connection

    def _initialize_schema(self) -> None:
        connection = self._require_connection()
        # Lock before inspecting version so concurrent first opens cannot race.
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                existing = connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
                if existing:
                    raise StorageError("기존 미등록 스키마는 자동으로 변경하지 않습니다.")
                for statement in _SCHEMA:
                    connection.execute(statement)
                connection.execute("PRAGMA user_version = 1")
            elif version != _SCHEMA_VERSION:
                raise StorageError("지원하지 않는 SQLite 스키마 버전입니다.")
            for table, columns in _SCHEMA_COLUMNS.items():
                connection.execute(f"SELECT {columns} FROM {table} LIMIT 0")

    def save_raw_reviews(
        self,
        reviews: Sequence[RawReview],
        policy: DuplicatePolicy,
    ) -> BatchOperationResult:
        """Commit valid rows together; isolate row errors using savepoints.

        Input IDs are ignored. UPSERT retains the stored ID and created_at,
        resets status to RAW, and invalidates dependent Clean/Analysis rows.
        """
        if not isinstance(policy, DuplicatePolicy):
            raise ValidationError("policy must be a DuplicatePolicy value")
        connection = self._require_connection()
        succeeded = skipped = failed = 0
        errors: List[ItemError] = []
        try:
            with connection:
                # BEGIN is essential: releasing a top-level savepoint alone would
                # commit each row and prevent whole-batch rollback on a DB failure.
                connection.execute("BEGIN IMMEDIATE")
                for row_number, review in enumerate(reviews, start=1):
                    connection.execute("SAVEPOINT raw_item")
                    try:
                        if not isinstance(review, RawReview):
                            raise ValueError("Expected RawReview")
                        if review.source_file is not None and not isinstance(review.source_file, str):
                            raise ValueError("Invalid source_file")
                        if not isinstance(review.raw_payload, dict):
                            raise ValueError("Invalid raw_payload")
                        values = tuple(_json_dump(value) for value in (
                            review.source_review_id, review.product_name, review.review_date,
                            review.rating, review.review_text,
                        )) + (review.source_file, _json_dump(review.raw_payload))
                        key = _raw_dedupe_key(review)
                        existing = connection.execute(
                            "SELECT id FROM raw_reviews WHERE dedupe_key = ?", (key,)
                        ).fetchone()
                        if existing is not None and policy is DuplicatePolicy.SKIP:
                            skipped += 1
                        else:
                            now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                            if existing is None:
                                connection.execute(
                                    "INSERT INTO raw_reviews (source_review_id, product_name, "
                                    "review_date, rating, review_text, source_file, raw_payload, "
                                    "dedupe_key, created_at, updated_at) "
                                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                    values + (key, now, now),
                                )
                            else:
                                # Foreign-key cascade also removes analysis_results.
                                connection.execute("DELETE FROM clean_reviews WHERE id = ?", (existing["id"],))
                                connection.execute(
                                    "UPDATE raw_reviews SET source_review_id=?, product_name=?, "
                                    "review_date=?, rating=?, review_text=?, source_file=?, raw_payload=?, "
                                    "status='RAW', updated_at=? WHERE id=?",
                                    values + (now, existing["id"]),
                                )
                            succeeded += 1
                    except (ValueError, TypeError, OverflowError, RecursionError,
                            sqlite3.IntegrityError):
                        connection.execute("ROLLBACK TO SAVEPOINT raw_item")
                        failed += 1
                        errors.append(ItemError(
                            item_ref=str(row_number), code="RAW_STORAGE_INVALID_ITEM",
                            message="원본 행을 저장할 수 없습니다. 값의 형식과 저장 제약을 확인하세요.",
                        ))
                        self._logger.warning("Raw row storage failed: row=%d", row_number)
                    finally:
                        connection.execute("RELEASE SAVEPOINT raw_item")
        except sqlite3.Error as exc:
            self._logger.error("Raw batch rolled back due to a storage failure")
            raise StorageError("원본 리뷰 저장에 실패하여 배치 전체를 취소했습니다.") from exc
        result = BatchOperationResult(
            processed=len(reviews), succeeded=succeeded, skipped=skipped,
            failed=failed, errors=errors,
        )
        self._logger.info(
            "Raw storage: processed=%d succeeded=%d skipped=%d failed=%d",
            result.processed, succeeded, skipped, failed,
        )
        return result

    def fetch_raw_reviews(
        self, *, status: Optional[ProcessingStatus] = None,
    ) -> List[RawReview]:
        """Return original values and storage-assigned IDs in ascending ID order."""
        if status is not None and not isinstance(status, ProcessingStatus):
            raise ValidationError("status must be a ProcessingStatus value")
        connection = self._require_connection()
        sql = "SELECT * FROM raw_reviews"
        parameters = ()
        if status is not None:
            sql += " WHERE status = ?"
            parameters = (status.value,)
        try:
            rows = connection.execute(sql + " ORDER BY id", parameters).fetchall()
            reviews = []
            for row in rows:
                values = {name: json.loads(row[name]) for name in (
                    "source_review_id", "product_name", "review_date", "rating",
                    "review_text", "raw_payload",
                )}
                if not isinstance(values["raw_payload"], dict):
                    raise ValueError("Invalid persisted raw_payload")
                reviews.append(RawReview(id=row["id"], source_file=row["source_file"], **values))
            return reviews
        except (sqlite3.Error, ValueError, TypeError, RecursionError) as exc:
            self._logger.error("Raw review read failed")
            raise StorageError("원본 리뷰를 읽을 수 없습니다.") from exc

    def close(self) -> None:
        """Close the connection; repeated calls are safe."""
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def __enter__(self) -> SQLiteReviewRepository:
        self._require_connection()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()


__all__ = ["ReviewRepository", "SQLiteReviewRepository"]
