"""Persistence boundary shared by SQLite and JSONL implementations."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from src.config import get_logger
from src.errors import StorageError, ValidationError

from typing import List, Optional, Protocol, Sequence, runtime_checkable

from src.models import (
    AnalysisResult,
    BatchOperationResult,
    CleanReview,
    DuplicatePolicy,
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
